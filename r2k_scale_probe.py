"""
r2k_scale_probe.py  --  diagnose the high-weight SCALE / mapping anomalies the audit flagged (a year
where revenue/assets/equity/debt all jump ~1000x or collapse, or assets implausibly large for a small
cap). For each name it prints the YEAR SERIES of the key line items (to see the anomaly), then the raw
AS-FILED values + units for the worst year's tags (to tell a source/unit error from a mapping/CIK
collision or our processing).

USAGE  python r2k_scale_probe.py                 # defaults to the flagged names
       python r2k_scale_probe.py 1048268 1664703 1364479
Reads fundamentals_dera.csv + dera_facts.csv. Writes scale_probe.txt.
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "scale_probe.txt"

WORKLIST = ["1048268", "1664703", "1364479"]
SERIES = ["revenue", "total_assets", "total_equity", "total_liabilities", "total_debt", "net_income"]
KEYTAGS = ("Revenues", "Assets", "Liabilities", "StockholdersEquity",
           "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
           "NetIncomeLoss", "ProfitLoss")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ciks = sys.argv[1:] or WORKLIST
    cset = set(ciks)
    rows = [r for r in csv.DictReader(open(FUND, encoding="utf-8")) if r["cik"] in cset]
    by_cik = defaultdict(list)
    for r in rows:
        by_cik[r["cik"]].append(r)

    # find the worst-jump year per cik (largest single-step ratio in total_assets or revenue)
    worst = {}
    for c, yrs in by_cik.items():
        yrs = sorted([y for y in yrs if y["fiscal_year"].isdigit()], key=lambda y: y["fiscal_year"])
        best = None
        for a, b in zip(yrs, yrs[1:]):
            for k in ("total_assets", "revenue"):
                va, vb = fnum(a.get(k)), fnum(b.get(k))
                if va and vb and abs(va) > 1e6:
                    ratio = abs(vb / va)
                    jump = max(ratio, 1 / ratio if ratio else 0)
                    if best is None or jump > best[0]:
                        best = (jump, b["fiscal_year"])
        if best:
            worst[c] = best[1]

    # raw as-filed values for the worst years
    want = {(c, worst[c]) for c in worst}
    raw = defaultdict(list)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("tag"),
                              h.index("uom"), h.index("value"))
        qi = h.index("qtrs") if "qtrs" in h else None
        for row in rd:
            if len(row) > vi and (row[ci], row[fi]) in want and row[ti] in KEYTAGS:
                raw[(row[ci], row[fi])].append((row[ti], row[ui], row[vi], row[qi] if qi is not None else "?"))

    L = [f"SCALE / MAPPING ANOMALY PROBE  --  {len(cset)} names", ""]
    for c in ciks:
        yrs = sorted([y for y in by_cik.get(c, []) if y["fiscal_year"].isdigit()],
                     key=lambda y: y["fiscal_year"])
        L.append(f"=== cik {c}  (worst-jump year: {worst.get(c, '?')}) ===")
        L.append("   fy    " + "".join(f"{k.split('_')[-1][:8]:>14}" for k in SERIES))
        for y in yrs:
            L.append(f"   {y['fiscal_year']}  " +
                     "".join(f"{(fnum(y.get(k)) or 0)/1e6:>14,.1f}" for k in SERIES) + "  (in $M)")
        wy = worst.get(c)
        if wy:
            L.append(f"\n   RAW as-filed facts for the worst year (FY{wy})  [tag | uom | value | qtrs]:")
            for tag, uom, val, q in sorted(raw.get((c, wy), [])):
                L.append(f"      {tag:<60} {uom:<5} {val:>20} q={q}")
        L.append("")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"  -> {OUT.name}")


if __name__ == "__main__":
    main()
