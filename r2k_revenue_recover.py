"""
r2k_revenue_recover.py  --  recover REVENUE for company-years where the DERA classifier extracted the
rest of the income statement (net income, etc.) but left revenue BLANK. These are real, revenue-
generating companies whose revenue tag the classifier's priority list missed (e.g. refiners /
healthcare using non-standard XBRL revenue tags): Western Refining fy2015 has net income $406M but no
revenue; FactSet/Morningstar show $9.8B. Left unfixed, they are miscounted as "no-revenue" names,
which inflates the no-revenue weight in the EARLY years (the extraction gap shrinks over time) and
makes the "% with revenue" trend look like a real improvement when it is mostly improving data capture.

WHAT IT DOES  (safe, additive, provenance-stamped -- the sanctioned "x ours / v Morningstar" adoption)
  * builds {(cik, fiscal_year) -> Total Revenue} from morningstar_long.csv (statement IS, full dollars),
  * for every fundamentals_dera row with a BLANK revenue where Morningstar has one, FILLS it,
  * NEVER overwrites an existing revenue; every fill is logged with source = "morningstar:recover",
  * writes fundamentals_dera_resolved.csv (which r2k_dera_to_fundamentals.py auto-prefers) + an audit.

Chains on top of r2k_resolve.py: if fundamentals_dera_resolved.csv already exists it is used as the
base, so running both is additive. Idempotent -- a second run refills nothing (revenue already set).

INPUTS   fundamentals_dera_resolved.csv (if present) else fundamentals_dera.csv ; morningstar_long.csv
OUTPUTS  fundamentals_dera_resolved.csv  (+ revenue_src column) ; revenue_recovery_audit.csv
RUN      python r2k_revenue_recover.py       (then re-run dera_to_fundamentals -> plausibility -> report)
"""
import csv
import os
import sys
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
DERA = BASE / "fundamentals_dera.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
MS = BASE / "morningstar_long.csv"
AUDIT = BASE / "revenue_recovery_audit.csv"

REV_METRIC = "Total Revenue"          # Morningstar standardized top line (full dollars)
REV_STMT = "IS"


def _ck(c):
    s = str(c).strip()
    return str(int(s)) if s.isdigit() else s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_ms_revenue():
    """{(cik, fiscal_year): revenue} from Morningstar 'Total Revenue' income-statement rows.
    If a (cik, fy) has several (restatement / period), keep the largest magnitude = the annual figure."""
    if not MS.exists():
        sys.exit(f"!! {MS.name} not found -- run r2k_morningstar_parse.py first.")
    out = {}
    n = 0
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.DictReader(f)
        for row in r:
            if row.get("metric") != REV_METRIC or row.get("statement") != REV_STMT:
                continue
            v = _num(row.get("value"))
            if v is None:
                continue
            key = (_ck(row.get("cik", "")), str(row.get("fiscal_year", "")).strip())
            if key[1] and (key not in out or abs(v) > abs(out[key])):
                out[key] = v
            n += 1
    print(f"  Morningstar '{REV_METRIC}' rows: {n:,}  -> {len(out):,} (cik, fy) revenue points")
    return out


def main():
    base_file = RESOLVED if RESOLVED.exists() else DERA
    if not base_file.exists():
        sys.exit(f"!! {base_file.name} not found -- run r2k_dera_classify.py first.")
    print(f"  base fundamentals: {base_file.name}")
    ms = load_ms_revenue()

    with open(base_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    if "revenue_src" not in fields:
        fields = fields + ["revenue_src"]

    filled = skipped_have = no_ms = 0
    audit = []
    for r in rows:
        r.setdefault("revenue_src", "")
        if _num(r.get("revenue")) is not None:          # already has revenue -- never overwrite
            skipped_have += 1
            continue
        key = (_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip())
        rev = ms.get(key)
        if rev is None or rev <= 0:          # only a real, positive top line -- never fill 0 (keeps
            no_ms += 1                        # genuinely pre-revenue names correctly classified no-rev)
            continue
        r["revenue"] = repr(rev) if False else f"{rev}"    # plain numeric string
        r["revenue_src"] = "morningstar:recover"
        filled += 1
        audit.append({"cik": key[0], "fiscal_year": key[1],
                      "recovered_revenue": f"{rev:.0f}", "net_income": r.get("net_income", ""),
                      "sector": r.get("sector", "")})

    with open(RESOLVED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "recovered_revenue", "net_income", "sector"])
        w.writeheader()
        w.writerows(sorted(audit, key=lambda x: -abs(_num(x["recovered_revenue"]) or 0)))

    print(f"\n  revenue RECOVERED for {filled:,} company-years (were blank, Morningstar had a value)")
    print(f"  left as-is: {skipped_have:,} already had revenue ; {no_ms:,} blank with no Morningstar match")
    print(f"  -> {RESOLVED.name}  (r2k_dera_to_fundamentals.py will auto-prefer it)")
    print(f"  -> {AUDIT.name}  ({len(audit):,} rows, largest first)")
    print("\n  largest recoveries:")
    for a in sorted(audit, key=lambda x: -abs(_num(x["recovered_revenue"]) or 0))[:10]:
        print(f"     cik {a['cik']:>9} fy{a['fiscal_year']}  revenue ${_num(a['recovered_revenue'])/1e9:,.2f}B  (had ni={a['net_income']})")
    print("\n  NEXT: python r2k_dera_to_fundamentals.py ; python r2k_plausibility.py ; python r2k_report.py")


if __name__ == "__main__":
    main()
