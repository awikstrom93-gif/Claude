"""
r2k_name_audit.py  --  per-name balance-sheet audit for the HIGH-WEIGHT review worklist (resolve the
names that move the index aggregate, not the microcap tail). For each given CIK's BS_FOOTS breaks it
lays the sheet bare: our selected A / L / E / mezz and the gap, the filer's reported totals, and the
as-filed BS line items closest to the gap -- so the shared root cause behind the top-weight breaks is
visible at a glance rather than guessed.

USAGE
  python r2k_name_audit.py                  # defaults to the plausibility report's top-weight worklist
  python r2k_name_audit.py 67887 1192448    # specific CIKs

Reads tieout_report.csv, fundamentals_dera.csv, dera_facts.csv. Writes name_audit.txt.
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "name_audit.txt"

# highest-weight names the plausibility report flagged for review (plbs / balance-sheet breaks)
WORKLIST = ["67887", "1192448", "1368622", "1865782", "1144879",
            "1796022", "1805077", "1997464", "1612720"]
REPORTED = ["Assets", "Liabilities", "LiabilitiesAndStockholdersEquity", "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "MinorityInterest", "LiabilitiesCurrent", "LiabilitiesNoncurrent",
            "AssetsCurrent", "AssetsNoncurrent"]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b, rel=0.02, ab=2_000_000.0):
    return a is not None and b is not None and abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def main():
    ciks = [c for c in sys.argv[1:]] or WORKLIST
    cset = set(ciks)
    # BS_FOOTS breaks (signed gap) for these names
    breaks = {}
    for r in csv.DictReader(open(TIE, encoding="utf-8")):
        if r["cik"] in cset and r["identity"].startswith("BS_FOOTS") and r["result"] == "BREAK":
            g = fnum(r["residual"])
            if g is not None:
                breaks[(r["cik"], r["fiscal_year"])] = g
    fund = {(r["cik"], r["fiscal_year"]): r for r in csv.DictReader(open(FUND, encoding="utf-8"))}

    # all as-filed BS facts for these names
    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in cset and row[si] == "BS" and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v

    L = [f"NAME AUDIT  --  BS_FOOTS breaks for {len(cset)} high-weight names", ""]
    for k in sorted(breaks):
        cik, fy = k
        g = breaks[k]
        f = fund.get(k, {})
        d = facts.get(k, {})
        rp = {t: d[t] for t in REPORTED if t in d}
        L.append(f"=== cik {cik}  FY{fy}   gap = {g:,.0f} ===")
        L.append("  OURS:    A={A}  L={L}  E={E}  mezz={M}".format(
            A=f.get("total_assets"), L=f.get("total_liabilities"),
            E=f.get("total_equity"), M=f.get("redeemable_nci")))
        L.append("  REPORTED: " + "  ".join(f"{t}={v:,.0f}" for t, v in rp.items()))
        # as-filed BS tags whose value ~= +/- the gap (the component we likely miss)
        near = [(t, v) for t, v in d.items() if close(v, g) or close(v, -g)]
        near.sort(key=lambda x: -abs(x[1]))
        if near:
            L.append("  TAGS ~= gap:")
            for t, v in near[:12]:
                L.append(f"       {v:>18,.0f}  {t}")
        else:
            L.append("  (no single as-filed BS tag ~= the gap -- multi-component)")
        L.append("")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"  -> {OUT.name}")


if __name__ == "__main__":
    main()
