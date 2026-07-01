"""
r2k_revenue_recover.py  --  recover REVENUE for company-years where the DERA classifier extracted the
rest of the income statement (net income, etc.) but left revenue BLANK, because the filer's revenue
tag isn't in the classifier's priority list (REV in r2k_dera_classify.py). These are real, revenue-
generating companies (refiners, healthcare, hotels): the revenue IS in the as-filed DERA facts, just
under a tag we didn't select -- either a non-standard single tag (e.g. RegulatedAndUnregulatedOperating
Revenue) or, commonly, only as SEGMENT lines that must be summed (FoodAndBeverageRevenue +
OccupancyRevenue + ...).

PHILOSOPHY-CONSISTENT ("diagnose, don't plug"): we DO NOT adopt Morningstar's number. We use Morningstar
'Total Revenue' only as a TARGET to identify which AS-FILED DERA tag (or sum of segment tags) is the
real top line, then adopt that AS-FILED value -- the ORIGINAL filing's number, point-in-time, not a
restatement. Morningstar (which may carry restated figures) never enters the data; it only locates the
tag. If no as-filed fact reconciles to the target, we fall back to the Morningstar value but STAMP it
`morningstar:fallback` so it is auditable and rare, not the default.

Original-filing (no-restatement) selection: DERA carries a fiscal year in several filings (the original
10-K plus later 10-Ks' comparatives, which may be restated). For each (cik, fy) we take the value from
the accession whose OWN latest year == fy -- i.e. the original 10-K for that year -- matching the
classifier's is-original preference.

INPUTS   fundamentals_dera_resolved.csv (if present) else fundamentals_dera.csv ;
         dera_facts.csv (as-filed tags) ; morningstar_long.csv (target only)
OUTPUTS  fundamentals_dera_resolved.csv (+ revenue / revenue_src cols) ; revenue_recovery_audit.csv
RUN      python r2k_revenue_recover.py     (then dera_to_fundamentals -> plausibility -> report)
"""
import csv
import os
import sys
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
DERA = BASE / "fundamentals_dera.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
FACTS = BASE / "dera_facts.csv"
MS = BASE / "morningstar_long.csv"
AUDIT = BASE / "revenue_recovery_audit.csv"

TOL = 0.08                    # as-filed value must reconcile to the Morningstar target within this
TOL_LOOSE = 0.12              # looser tolerance for a single best-match tag
# curated TOTAL top-line tags (a single tag that already is the whole revenue), in priority order
REV_TOTAL = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
             "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
             "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenueFromContractsWithCustomers",
             "Revenue", "RegulatedAndUnregulatedOperatingRevenue", "TotalRevenuesAndOtherIncome",
             "RevenuesNetOfInterestExpense"]
_EXCLUDE = ("cost", "gain", "loss", "deferred", "unearned", "receivable", "expense", "tax",
            "pershare", "pershare", "growth", "percent")


def _ck(c):
    s = str(c).strip()
    return str(int(s)) if s.isdigit() else s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def is_rev_tag(tag):
    tl = tag.lower()
    if not ("revenue" in tl or "sales" in tl):
        return False
    return not any(w in tl for w in _EXCLUDE)


def is_segment(tag):
    return is_rev_tag(tag) and tag not in REV_TOTAL


def load_ms_target():
    """{(cik, fy): Total Revenue} from Morningstar -- the TARGET used to locate the as-filed tag."""
    if not MS.exists():
        sys.exit(f"!! {MS.name} not found.")
    out = {}
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("metric") != "Total Revenue" or row.get("statement") != "IS":
                continue
            v = _num(row.get("value"))
            k = (_ck(row.get("cik", "")), str(row.get("fiscal_year", "")).strip())
            if v is not None and v > 0 and k[1] and (k not in out or v > out[k]):
                out[k] = v
    return out


def load_asfiled_revenue(need):
    """{(cik, fy): {tag: value}} of ORIGINAL-filing, annual, IS revenue-like facts, for the (cik, fy)
    in `need`. Dedups multi-filing years to the accession whose own latest year == fy (the original)."""
    if not FACTS.exists():
        sys.exit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    kept = []            # (cik, fy, accession, tag, value)
    acc_year = {}        # accession -> its own latest fiscal year (identifies the original filing)
    need_ciks = {c for c, _ in need}
    with open(FACTS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = _ck(r.get("cik", ""))
            if c not in need_ciks or r.get("stmt") != "IS":
                continue
            if (r.get("uom") or "USD") != "USD":
                continue
            tag = r.get("tag", "")
            if not is_rev_tag(tag):
                continue
            fy = str(r.get("fiscal_year", "")).strip()
            v = _num(r.get("value"))
            if v is None or not fy.isdigit():
                continue
            acc = r.get("version", "")
            kept.append((c, fy, acc, tag, v))
            y = int(fy)
            if acc not in acc_year or y > acc_year[acc]:
                acc_year[acc] = y
    # choose the original filing's value per (cik, fy, tag): prefer accession whose latest year == fy
    best = {}            # (cik, fy, tag) -> (priority, value)  lower priority = better
    for c, fy, acc, tag, v in kept:
        pr = 0 if acc_year.get(acc) == int(fy) else 1   # 0 = original filing, 1 = comparative
        key = (c, fy, tag)
        if key not in best or pr < best[key][0]:
            best[key] = (pr, v)
    out = {}
    for (c, fy, tag), (_pr, v) in best.items():
        out.setdefault((c, fy), {})[tag] = v
    return out


def resolve(byt, target):
    """Return (asfiled_value, provenance) using `target` only to VERIFY the right as-filed tag/sum."""
    if not target:
        return None, "no-target"
    # 1. a curated TOTAL tag that reconciles to the target
    for t in REV_TOTAL:
        v = byt.get(t)
        if v and abs(v - target) / target <= TOL:
            return v, "asfiled:" + t
    # 2. ANY single revenue-like tag that already reconciles to the target (a non-standard top line
    #    the REV list just doesn't name -- e.g. RefiningAndMarketingRevenue, HealthCareOrganization...)
    singles = [(t, v) for t, v in byt.items() if v and v > 0 and abs(v - target) / target <= TOL]
    if singles:
        t, v = min(singles, key=lambda kv: abs(kv[1] - target))
        return v, "asfiled:" + t
    # 3. SUM of segment revenue lines that reconciles to the target (revenue reported only by segment)
    segs = {t: v for t, v in byt.items() if is_segment(t) and v and v > 0}
    if len(segs) >= 2:
        ssum = sum(segs.values())
        if abs(ssum - target) / target <= TOL:
            return ssum, "asfiled:sum(" + "+".join(sorted(segs)) + ")"
    # 4. the single revenue-like tag closest to the target, within a looser tolerance
    cands = [(t, v) for t, v in byt.items() if v and v > 0]
    if cands:
        t, v = min(cands, key=lambda kv: abs(kv[1] - target))
        if abs(v - target) / target <= TOL_LOOSE:
            return v, "asfiled:" + t + "(closest)"
    return None, "no-asfiled-match"


def main():
    base_file = RESOLVED if RESOLVED.exists() else DERA
    if not base_file.exists():
        sys.exit(f"!! {base_file.name} not found -- run r2k_dera_classify.py first.")
    print(f"  base fundamentals: {base_file.name}")
    with open(base_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    if "revenue_src" not in fields:
        fields = fields + ["revenue_src"]

    blanks = [r for r in rows if _num(r.get("revenue")) is None]
    need = {(_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip()) for r in blanks}
    print(f"  {len(blanks):,} company-years have BLANK revenue -- locating the as-filed tag for each")
    target = load_ms_target()
    asfiled = load_asfiled_revenue(need)
    print(f"  Morningstar targets: {len(target):,} ; as-filed revenue-fact sets: {len(asfiled):,}")

    n_asfiled = n_fallback = n_none = 0
    audit = []
    for r in blanks:
        key = (_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip())
        tgt = target.get(key)
        val, prov = resolve(asfiled.get(key, {}), tgt)
        if val is None:                       # no as-filed reconciliation
            if tgt is not None:
                val, prov = tgt, "morningstar:fallback"   # last resort, stamped + audited
                n_fallback += 1
            else:
                n_none += 1
                continue
        else:
            n_asfiled += 1
        r["revenue"] = f"{val}"
        r["revenue_src"] = prov
        audit.append({"cik": key[0], "fiscal_year": key[1], "adopted_revenue": f"{val:.0f}",
                      "ms_target": f"{tgt:.0f}" if tgt else "", "source": prov,
                      "ms_vs_asfiled_pct": (f"{100*(tgt-val)/val:+.1f}" if (tgt and prov.startswith('asfiled')) else ""),
                      "net_income": r.get("net_income", ""), "sector": r.get("sector", "")})
    for r in rows:
        r.setdefault("revenue_src", "")

    with open(RESOLVED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        cols = ["cik", "fiscal_year", "adopted_revenue", "ms_target", "source", "ms_vs_asfiled_pct",
                "net_income", "sector"]
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(sorted(audit, key=lambda x: -abs(_num(x["adopted_revenue"]) or 0)))

    print(f"\n  recovered AS-FILED (original filing, point-in-time): {n_asfiled:,}")
    print(f"  Morningstar fallback (no as-filed fact reconciled -- stamped, audited): {n_fallback:,}")
    print(f"  still blank (no target, no fact): {n_none:,}")
    print(f"  -> {RESOLVED.name} ; {AUDIT.name}")
    # how often as-filed differs from Morningstar (= restatements Morningstar carries that we avoided)
    diffs = [abs(_num(a["ms_vs_asfiled_pct"])) for a in audit if a["ms_vs_asfiled_pct"]]
    if diffs:
        big = sum(1 for d in diffs if d > 2)
        print(f"  as-filed vs Morningstar >2% apart on {big:,}/{len(diffs):,} recovered names "
              f"-- those are restatements we did NOT adopt (kept the original as-filed figure).")
    print("\n  NEXT: python r2k_dera_to_fundamentals.py ; python r2k_plausibility.py ; python r2k_report.py")


if __name__ == "__main__":
    main()
