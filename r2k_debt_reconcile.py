"""
============================================================
r2k_debt_reconcile.py  --  DECISIVE DEBT DIAGNOSTIC. Separates the DERA-vs-Morningstar debt
difference into its two parts -- FUNDED debt (bonds/loans/notes + finance leases) and OPERATING
LEASE liabilities -- and reconciles each independently across the whole universe.

WHY: the pilot showed most of the "debt understatement" is operating leases (Morningstar folds the
post-ASC-842 operating-lease liability into its debt line; DERA uses the GAAP funded-debt
definition). This script proves it at scale: if DERA FUNDED debt ties Morningstar FUNDED debt
universe-wide, the gap is purely definitional and DERA's funded number is clean. Whatever funded
gap survives is a list of REAL misses to fix.

DEFINITIONS
  DERA funded   = fundamentals_dera.total_debt           (LT + current + LOC, lease-inclusive of
                  FINANCE leases only -- the as-filed funded-debt tags)
  DERA op-lease = OperatingLeaseLiability (total) else Current + Noncurrent   [from dera_facts]
  DERA incl     = funded + op-lease
  MS lease      = "Total Lease Liability"  else  Capital Lease Obligations (Current + Non Current)
  MS incl       = "Long Term Debt And Capital Lease Obligation" + "Current Debt And Capital Lease
                  Obligation"  (Morningstar's debt line, leases folded in)  else funded + lease
  MS funded     = MS incl - MS lease

OUTPUTS
  debt_reconcile_report.txt    universe summary: agreement on funded / lease / incl, by COUNT and
                               by DOLLAR and by INDEX WEIGHT (if universe_ciks weights available)
  debt_reconcile_detail.csv    per (cik, fy): all six figures + per-leg verdict, sorted to put the
                               biggest UNEXPLAINED FUNDED gaps (the real misses) on top

RUN:  python r2k_debt_reconcile.py
SELFTEST: python r2k_debt_reconcile.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
MS = BASE / "morningstar_long.csv"
REPORT = BASE / "debt_reconcile_report.txt"
DETAIL = BASE / "debt_reconcile_detail.csv"

AGREE_REL, AGREE_ABS = 0.02, 2_000_000.0          # debt rounds coarsely; 2% / $2M
SANITY_CAP = 2.0e11                               # >$200B debt in a small-cap universe = bad source row

OPLEASE_TOTAL = ["OperatingLeaseLiability"]
OPLEASE_PARTS = ["OperatingLeaseLiabilityCurrent", "OperatingLeaseLiabilityNoncurrent"]
MS_LEASE = "Total Lease Liability"
MS_LEASE_PARTS = ["Capital Lease Obligations Current", "Capital Lease Obligations Non Current"]
MS_INCL_PARTS = ["Long Term Debt And Capital Lease Obligation", "Current Debt And Capital Lease Obligation"]
MS_FUNDED_PARTS = ["Long Term Debt", "Current Debt"]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(AGREE_ABS, AGREE_REL * max(abs(a), abs(b)))


def verdict(dera, ms):
    if dera is None and ms is None:
        return "both_blank"
    if dera is None:
        return "MS_only"
    if ms is None:
        return "DERA_only"
    return "tie" if close(dera, ms) else "DIFF"


# ------------------------------------------------------------------ loaders
def load_dera_funded():
    """(cik, fy) -> (funded total_debt, sector), from the actual pipeline output."""
    out = {}
    if not FUND.exists():
        raise SystemExit(f"!! {FUND.name} not found -- run r2k_dera_classify.py first.")
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        out[(r["cik"], r["fiscal_year"])] = (fnum(r.get("total_debt")), r.get("sector", ""))
    return out


def load_dera_oplease(keys):
    """(cik, fy) -> operating-lease liability, from dera_facts (current-period BS facts)."""
    want_ciks = {c for c, _ in keys}
    tot = defaultdict(dict)
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    with open(FACTS, newline="", encoding="utf-8") as f:
        r = csv.reader(f)
        head = next(r)
        ci, fi, ti, vi = (head.index("cik"), head.index("fiscal_year"),
                          head.index("tag"), head.index("value"))
        keepset = set(OPLEASE_TOTAL) | set(OPLEASE_PARTS)
        for row in r:
            if len(row) <= vi or row[ci] not in want_ciks or row[ti] not in keepset:
                continue
            v = fnum(row[vi])
            if v is not None:
                tot[(row[ci], row[fi])][row[ti]] = v
    out = {}
    for k, d in tot.items():
        if OPLEASE_TOTAL[0] in d:
            out[k] = d[OPLEASE_TOTAL[0]]
        else:
            parts = [d[t] for t in OPLEASE_PARTS if t in d]
            out[k] = sum(parts) if parts else None
    return out


def load_ms(keys):
    """(cik, fy) -> dict of the MS debt/lease lines we need (latest period_end per metric)."""
    want_ciks = {c for c, _ in keys}
    need = set(MS_INCL_PARTS) | set(MS_FUNDED_PARTS) | set(MS_LEASE_PARTS) | {MS_LEASE}
    vals = defaultdict(dict)
    ped = defaultdict(dict)
    if not MS.exists():
        raise SystemExit(f"!! {MS.name} not found -- run r2k_morningstar_parse.py first.")
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        head = next(r)
        ci, fi, pi, mi, vi = (head.index("cik"), head.index("fiscal_year"),
                              head.index("period_end"), head.index("metric"), head.index("value"))
        for row in r:
            if len(row) <= vi or row[ci] not in want_ciks or row[mi] not in need:
                continue
            v = fnum(row[vi])
            if v is None:
                continue
            k, metric, pe = (row[ci], row[fi]), row[mi], row[pi]
            if ped[k].get(metric) is None or pe >= ped[k][metric]:
                vals[k][metric] = v
                ped[k][metric] = pe
    return vals


def ms_breakdown(m):
    """funded / lease / incl for one MS company-year, with Morningstar's negative-sign handling
    irrelevant here (balance-sheet debt lines are positive)."""
    def s(*names):
        parts = [m[n] for n in names if n in m and m[n] is not None]
        return sum(parts) if parts else None

    lease = m.get(MS_LEASE)
    if lease is None:
        lease = s(*MS_LEASE_PARTS)
    incl = s(*MS_INCL_PARTS)
    funded = s(*MS_FUNDED_PARTS)
    if incl is None and (funded is not None or lease is not None):
        incl = (funded or 0) + (lease or 0)
    # prefer incl - lease for funded (captures current-portion folded into the combined lines)
    if incl is not None and lease is not None:
        funded = incl - lease
    elif funded is None and incl is not None:
        funded = incl
    return funded, lease, incl


# ------------------------------------------------------------------ run
def reconcile(dera_funded, dera_oplease, ms_vals, names=None):
    """Population = company-years DERA actually produced (so 'MS_only' means DERA had the filing but
    no funded-debt value -- a real miss candidate -- not merely a name DERA never covered)."""
    rows = []
    for k, val in dera_funded.items():
        df, sector = val if isinstance(val, tuple) else (val, "")
        dl = dera_oplease.get(k)
        di = (df or 0) + (dl or 0) if (df is not None or dl is not None) else None
        mf, ml, mi = ms_breakdown(ms_vals.get(k, {}))
        fv = verdict(df, mf)
        # guard: an absurd magnitude in either source is a bad data row, not a real disagreement
        if any(x is not None and abs(x) > SANITY_CAP for x in (df, mf)):
            fv = "data_error"
        rows.append(dict(
            cik=k[0], fiscal_year=k[1], name=(names or {}).get(k[0], ""), sector=sector,
            is_financial=sector in ("bank", "insurer"),
            dera_funded=df, dera_oplease=dl, dera_incl=di,
            ms_funded=mf, ms_lease=ml, ms_incl=mi,
            funded_verdict=fv, lease_verdict=verdict(dl, ml),
            incl_verdict=verdict(di, mi),
            funded_diff=(None if (df is None or mf is None) else df - mf),
        ))
    return rows


def summarize(rows):
    """agreement on each leg, by count and by dollar (sum of |MS value| as the materiality base).
    'where both present' counts only tie+DIFF; MS_only (DERA blank, real-miss candidate) and
    DERA_only (MS doesn't carry it -- e.g. bank borrowings) are reported separately."""
    legs = {"funded": ("dera_funded", "ms_funded", "funded_verdict"),
            "op-lease": ("dera_oplease", "ms_lease", "lease_verdict"),
            "incl (funded+lease)": ("dera_incl", "ms_incl", "incl_verdict")}
    out = {}
    for leg, (dk, mk, vk) in legs.items():
        comparable = [r for r in rows if r[vk] in ("tie", "DIFF")]
        n = len(comparable)
        tie = sum(1 for r in comparable if r[vk] == "tie")
        dollar_base = sum(abs(r[mk]) for r in comparable if r[mk] is not None) or 1.0
        dollar_tie = sum(abs(r[mk]) for r in comparable if r[vk] == "tie" and r[mk] is not None)
        out[leg] = dict(n=n, tie=tie, pct_n=(100 * tie / n if n else 0.0),
                        pct_dollar=100 * dollar_tie / dollar_base,
                        ms_only=sum(1 for r in rows if r[vk] == "MS_only"),
                        dera_only=sum(1 for r in rows if r[vk] == "DERA_only"))
    return out


def main():
    dera_funded = load_dera_funded()
    keys = set(dera_funded)
    ms_vals = load_ms(keys)
    keys |= set(ms_vals)
    dera_oplease = load_dera_oplease(keys)
    # names from the index if present (optional cosmetic)
    names = {}
    idx = BASE / "dera_filing_index.csv"
    if idx.exists():
        for r in csv.DictReader(open(idx, encoding="utf-8")):
            names.setdefault(r.get("cik", ""), r.get("name", ""))

    rows = reconcile(dera_funded, dera_oplease, ms_vals, names)
    commercial = [r for r in rows if not r["is_financial"]]
    summ_all, summ_comm = summarize(rows), summarize(commercial)

    def block(title, s):
        out = [title,
               f"  {'LEG':<22}{'both present':>13}{'tie %(count)':>14}{'tie %($)':>11}"
               f"{'MS-only':>9}{'DERA-only':>11}"]
        for leg, v in s.items():
            out.append(f"  {leg:<22}{v['n']:>13,}{v['pct_n']:>13.1f}%{v['pct_dollar']:>10.1f}%"
                       f"{v['ms_only']:>9,}{v['dera_only']:>11,}")
        return out

    lines = ["DEBT RECONCILIATION  --  DERA (as-filed) vs Morningstar, split funded vs operating-lease",
             "Population = company-years DERA produced. 'MS-only' on FUNDED = DERA had the filing but",
             "no funded-debt value (a real miss candidate). DERA-only on funded is common for banks/",
             "insurers (their borrowings sit outside Morningstar's standard debt lines -- DERA better).",
             ""]
    lines += block("ALL SECTORS", summ_all) + [""]
    lines += block("COMMERCIAL ONLY (the meaningful funded comparison)", summ_comm) + [""]
    lines += ["READ: high commercial funded tie% => DERA funded debt is clean and the headline gap is",
              "operating leases (definitional). MS-only commercial names below are the funded misses to fix.",
              ""]
    # (a) real funded MISSES: commercial, DERA funded blank, MS funded materially present
    miss = [r for r in commercial if r["funded_verdict"] == "MS_only"
            and r["ms_funded"] is not None and abs(r["ms_funded"]) >= 10_000_000]
    miss.sort(key=lambda r: -abs(r["ms_funded"]))
    lines.append(f"TOP FUNDED-DEBT MISSES (commercial, DERA blank, MS has it)  -- {len(miss):,} names:")
    lines.append(f"  {'cik':<9}{'fy':<6}{'name':<34}{'MS funded':>15}")
    for r in miss[:30]:
        lines.append(f"  {r['cik']:<9}{r['fiscal_year']:<6}{(r['name'] or '')[:32]:<34}{r['ms_funded']:>15,.0f}")
    # (b) both present but disagree
    diffs = [r for r in commercial if r["funded_verdict"] == "DIFF" and r["funded_diff"] is not None]
    diffs.sort(key=lambda r: -abs(r["funded_diff"]))
    lines.append("")
    lines.append(f"TOP FUNDED DISAGREEMENTS (commercial, both present)  -- {len(diffs):,} names:")
    lines.append(f"  {'cik':<9}{'fy':<6}{'name':<34}{'DERA fund':>14}{'MS fund':>14}{'diff':>14}")
    for r in diffs[:30]:
        lines.append(f"  {r['cik']:<9}{r['fiscal_year']:<6}{(r['name'] or '')[:32]:<34}"
                     f"{r['dera_funded']:>14,.0f}{r['ms_funded']:>14,.0f}{r['funded_diff']:>14,.0f}")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    summ = summ_comm
    fields = ["cik", "fiscal_year", "name", "dera_funded", "dera_oplease", "dera_incl",
              "ms_funded", "ms_lease", "ms_incl", "funded_verdict", "lease_verdict",
              "incl_verdict", "funded_diff"]
    with open(DETAIL, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in sorted(rows, key=lambda r: -(abs(r["funded_diff"]) if r["funded_diff"] else 0)):
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in fields})
    print(f"  -> {REPORT.name}")
    print(f"  -> {DETAIL.name}  ({len(rows):,} company-years)")
    for leg, s in summ.items():
        print(f"     {leg:<22} tie {s['pct_n']:.1f}% by count, {s['pct_dollar']:.1f}% by $  "
              f"(n={s['n']:,}, DERA-only={s['dera_only']:,}, MS-only={s['ms_only']:,})")


# ------------------------------------------------------------------ selftest
def selftest():
    # Sportsman's-style: DERA funded matches MS funded exactly; the rest is leases.
    dera_funded = {("A", "2026"): 91_689_000.0, ("B", "2026"): 0.0, ("C", "2026"): 500_000_000.0}
    dera_oplease = {("A", "2026"): 334_886_000.0, ("B", "2026"): 2_032_189_000.0, ("C", "2026"): None}
    ms_vals = {
        ("A", "2026"): {"Long Term Debt And Capital Lease Obligation": 324_993_000.0,
                        "Current Debt And Capital Lease Obligation": 101_582_000.0,
                        "Total Lease Liability": 334_886_000.0},
        ("B", "2026"): {"Long Term Debt And Capital Lease Obligation": 1_731_041_000.0,
                        "Current Debt And Capital Lease Obligation": 301_148_000.0,
                        "Total Lease Liability": 2_032_189_000.0},
        ("C", "2026"): {"Long Term Debt": 400_000_000.0, "Total Lease Liability": 50_000_000.0,
                        "Long Term Debt And Capital Lease Obligation": 450_000_000.0,
                        "Current Debt And Capital Lease Obligation": 0.0},
    }
    rows = reconcile(dera_funded, dera_oplease, ms_vals)
    by = {r["cik"]: r for r in rows}
    checks = [
        ("A funded ties", by["A"]["funded_verdict"] == "tie"),
        ("A lease ties", by["A"]["lease_verdict"] == "tie"),
        ("B funded ties (0=0)", by["B"]["funded_verdict"] == "tie"),
        ("B lease ties", by["B"]["lease_verdict"] == "tie"),
        # C: MS funded = 450-50 = 400 vs DERA 500 -> real funded gap
        ("C funded DIFF", by["C"]["funded_verdict"] == "DIFF"),
        ("C funded diff=100M", abs(by["C"]["funded_diff"] - 100_000_000.0) < 1),
    ]
    summ = summarize(rows)
    for nm, ok in checks:
        print(f"     {'PASS' if ok else 'FAIL'}  {nm}")
    print(f"  funded tie% by count: {summ['funded']['pct_n']:.0f}  (expect 67)")
    print(f"\n  SELFTEST: {'PASS' if all(o for _, o in checks) else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
