"""
============================================================
r2k_debt_materiality.py  --  weight the debt reconciliation by INDEX WEIGHT, not name count.

The reconciliation counts names; the IC cares about the benchmark. A small-cap GROWTH index is
light on financials and lessors, so 1,100 disagreeing issuers may be a tiny slice of weight -- or
not. This answers: what % of the R2000G's WEIGHT has lease-adjusted debt that ties Morningstar,
forks, or is missing -- and which high-weight names actually move the number.

INPUTS (project folder)
  *Russell*Growth*Holding*.xlsx   index holdings (latest snapshot used for weights)
  security_cik_map.json           ticker -> cik
  debt_reconcile_detail.csv       per (cik, fy) debt comparison  (r2k_debt_reconcile.py)

OUTPUT
  debt_materiality_report.txt     weight by verdict bucket + the highest-weight disagreements

RUN:  python r2k_debt_materiality.py
SELFTEST: python r2k_debt_materiality.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, json, re, sys

BASE = Path(os.environ.get("R2KG_BASE", "."))
CIKMAP = BASE / "security_cik_map.json"
DETAIL = BASE / "debt_reconcile_detail.csv"
REPORT = BASE / "debt_materiality_report.txt"
AGREE_REL, AGREE_ABS = 0.02, 2_000_000.0


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ntk(t):
    return re.sub(r"[^A-Z0-9]", "", str(t).upper()) if t else ""


def close(a, b):
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(AGREE_ABS, AGREE_REL * max(abs(a), abs(b)))


def find_holdings():
    c = (list(BASE.glob("*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"))
         or list(BASE.glob("*[Hh]olding*.xlsx")))
    if not c:
        raise FileNotFoundError("holdings workbook not found")
    return c[0]


def _snap_year(sn):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(sn))
    return int("20" + m.group(3)) if m and len(m.group(3)) == 2 else (int(m.group(3)) if m else None)


def load_latest_weights():
    """ticker(normalized) -> (weight, name) from the most recent snapshot sheet."""
    import openpyxl
    wb = openpyxl.load_workbook(find_holdings(), read_only=True, data_only=True)
    best_yr, best = None, None
    for sn in wb.sheetnames:
        yr = _snap_year(sn)
        if yr is None:
            continue
        if best_yr is None or yr > best_yr:
            best_yr, best = yr, sn
    ws = wb[best]
    hdr = None; idx = {}; out = {}
    for r in ws.iter_rows(values_only=True):
        if hdr is None:
            hdr = [str(c).strip() if c else "" for c in r]; idx = {h: i for i, h in enumerate(hdr)}
            if "Ticker" not in idx:
                hdr = None
            continue
        def g(col):
            i = idx.get(col)
            return r[i] if (i is not None and i < len(r) and r[i] is not None) else None
        tk = g("Ticker")
        if tk is None:
            continue
        out[ntk(tk)] = (fnum(g("Portfolio Weighting %")) or 0.0, str(g("Name") or ""))
    wb.close()
    return out, best_yr


def _norm_cik(c):
    c = str(c).strip()
    return str(int(c)) if c.isdigit() else c        # drop zero-padding to match the detail file


def load_cikmap():
    base = {}
    if CIKMAP.exists():
        for t, m in json.load(open(CIKMAP)).items():
            base[ntk(t)] = _norm_cik(m["cik"] if isinstance(m, dict) else m)
    return base


def load_debt_by_cik():
    """cik -> latest-year dict(dera_incl, ms_incl) from the reconciliation detail."""
    latest = {}
    if not DETAIL.exists():
        raise SystemExit(f"!! {DETAIL.name} not found -- run r2k_debt_reconcile.py first.")
    for r in csv.DictReader(open(DETAIL, encoding="utf-8")):
        cik = _norm_cik(r["cik"]); fy = r.get("fiscal_year", "")
        if cik not in latest or fy > latest[cik]["fy"]:
            latest[cik] = dict(fy=fy, dera_incl=fnum(r.get("dera_incl")), ms_incl=fnum(r.get("ms_incl")))
    return latest


def verdict(d):
    if d is None:
        return "unresolved"
    di, mi = d["dera_incl"], d["ms_incl"]
    if di is None and mi is None:
        return "no_debt(both blank)"
    if di is None:
        return "MISS (DERA blank, MS has it)"
    if mi is None:
        return "DERA only (MS blank)"
    return "TIE" if close(di, mi) else "FORK (both present, differ)"


def run(weights, cikmap, debt):
    rows = []
    for nt, (wt, name) in weights.items():
        cik = cikmap.get(nt)
        d = debt.get(cik) if cik else None
        rows.append(dict(nt=nt, name=name, weight=wt, cik=cik, verdict=verdict(d),
                         dera_incl=(d or {}).get("dera_incl"), ms_incl=(d or {}).get("ms_incl")))
    return rows


def main():
    weights, yr = load_latest_weights()
    cikmap = load_cikmap()
    debt = load_debt_by_cik()
    rows = run(weights, cikmap, debt)

    total_wt = sum(r["weight"] for r in rows) or 1.0
    buckets = {}
    for r in rows:
        buckets.setdefault(r["verdict"], [0.0, 0])
        buckets[r["verdict"]][0] += r["weight"]; buckets[r["verdict"]][1] += 1
    order = ["TIE", "FORK (both present, differ)", "MISS (DERA blank, MS has it)",
             "DERA only (MS blank)", "no_debt(both blank)", "unresolved"]

    L = [f"DEBT MATERIALITY  --  R2000G lease-adjusted debt vs Morningstar, weighted by INDEX WEIGHT",
         f"Snapshot {yr}-04-30   total index weight {total_wt:.1f}%   ({len(rows):,} members)", ""]
    L.append(f"  {'VERDICT':<34}{'index wt %':>12}{'% of index':>12}{'names':>8}")
    for v in order + [b for b in buckets if b not in order]:
        if v not in buckets:
            continue
        w, n = buckets[v]
        L.append(f"  {v:<34}{w:>11.2f}%{100*w/total_wt:>11.1f}%{n:>8,}")
    reliable = buckets.get("TIE", [0, 0])[0] + buckets.get("no_debt(both blank)", [0, 0])[0]
    # split FORK weight by magnitude: small (<=10%, near-tie / finance-lease classification noise)
    # vs large (>25%, genuinely structural) -- the large slice is the true uncertainty.
    def _pct(r):
        mi = r["ms_incl"]
        return abs((r["dera_incl"] - mi) / mi) if (mi and r["dera_incl"] is not None) else 9.99
    forks = [r for r in rows if r["verdict"].startswith("FORK")]
    sm = sum(r["weight"] for r in forks if _pct(r) <= 0.10)
    lg = sum(r["weight"] for r in forks if _pct(r) > 0.25)
    L += ["",
          f"  => {100*reliable/total_wt:.1f}% of index weight TIES Morningstar (or is genuinely debt-free).",
          f"  => of the {100*buckets.get('FORK (both present, differ)',[0,0])[0]/total_wt:.1f}% FORK weight: "
          f"{100*sm/total_wt:.1f}% is near-tie (<=10%, finance-lease noise), "
          f"{100*lg/total_wt:.1f}% is structural (>25%).",
          f"  => so ~{100*(reliable+sm)/total_wt:.0f}% is reliable-or-near; "
          f"~{100*(lg+buckets.get('MISS (DERA blank, MS has it)',[0,0])[0])/total_wt:.0f}% "
          f"(large forks + misses) is the genuine uncertainty to resolve.", ""]
    # highest-weight disagreements -- the names that actually move the benchmark
    bad = [r for r in rows if r["verdict"].startswith(("FORK", "MISS"))]
    bad.sort(key=lambda r: -r["weight"])
    L.append(f"TOP INDEX-WEIGHT DEBT DISAGREEMENTS (these move the number):")
    L.append(f"  {'wt%':>6}  {'verdict':<32}{'name':<30}{'DERA incl':>15}{'MS incl':>15}")
    for r in bad[:30]:
        di = "-" if r["dera_incl"] is None else f"{r['dera_incl']:,.0f}"
        mi = "-" if r["ms_incl"] is None else f"{r['ms_incl']:,.0f}"
        L.append(f"  {r['weight']:>5.2f}  {r['verdict'][:30]:<32}{(r['name'] or '')[:28]:<30}{di:>15}{mi:>15}")
    REPORT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:14]))
    print(f"\n  -> {REPORT.name}")


def selftest():
    weights = {"AAA": (5.0, "Big Tie"), "BBB": (3.0, "Big Fork"), "CCC": (2.0, "Big Miss"),
               "DDD": (1.0, "Debt Free"), "EEE": (0.5, "Unmapped")}
    cikmap = {"AAA": "1", "BBB": "2", "CCC": "3", "DDD": "4"}
    debt = {"1": dict(fy="2025", dera_incl=1_000_000_000, ms_incl=1_005_000_000),
            "2": dict(fy="2025", dera_incl=500_000_000, ms_incl=2_000_000_000),
            "3": dict(fy="2025", dera_incl=None, ms_incl=900_000_000),
            "4": dict(fy="2025", dera_incl=None, ms_incl=None)}
    rows = run(weights, cikmap, debt)
    v = {r["nt"]: r["verdict"] for r in rows}
    checks = [("AAA tie", v["AAA"] == "TIE"), ("BBB fork", v["BBB"].startswith("FORK")),
              ("CCC miss", v["CCC"].startswith("MISS")), ("DDD no_debt", v["DDD"].startswith("no_debt")),
              ("EEE unresolved", v["EEE"] == "unresolved")]
    for nm, ok in checks:
        print(f"     {'PASS' if ok else 'FAIL'}  {nm}")
    print(f"\n  SELFTEST: {'PASS' if all(o for _, o in checks) else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
