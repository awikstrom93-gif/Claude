"""
r2k_isgp_probe.py  --  diagnose the HIGH-WEIGHT names still breaking IS_GP (Rev - COGS = GP) that the
split-COGS fix didn't catch. For each name's IS_GP break it shows our selected revenue / COGS / gross
profit and the break residual, then the as-filed revenue-class and cost-class tags on the income
statement -- so we see whether revenue is mis-selected (a fragment, a gross-up, the wrong segment) or
gross profit is mistagged.

USAGE  python r2k_isgp_probe.py                       # defaults to the plausibility worklist
       python r2k_isgp_probe.py 67887 1192448 ...
Reads tieout_report.csv + fundamentals_dera.csv + dera_facts.csv. Writes isgp_probe.txt.
"""
from pathlib import Path
import os, csv, re, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "isgp_probe.txt"

WORKLIST = ["67887", "1192448", "1368622", "1865782", "1796022", "1997464", "1666134", "910612", "1410098"]
REV_PAT = re.compile(r"revenue|sales|premiumsearned|noninterestincome|interestanddividend|"
                     r"operatingleases.*revenue|realestaterevenue|royalty", re.I)
COST_PAT = re.compile(r"costof|costsof|cogs", re.I)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    ciks = set(sys.argv[1:] or WORKLIST)
    # IS_GP breaks (+residual) for these names
    brk = {}
    for r in csv.DictReader(open(TIE, encoding="utf-8")):
        if r["cik"] in ciks and r["identity"].startswith("IS_GP") and r["result"] == "BREAK":
            brk[(r["cik"], r["fiscal_year"])] = fnum(r["residual"])
    fund = {(r["cik"], r["fiscal_year"]): r for r in csv.DictReader(open(FUND, encoding="utf-8"))}
    want = set(brk)
    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == "IS" and row[ui] == "USD" and (row[ci], row[fi]) in want:
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v

    L = [f"IS_GP HIGH-WEIGHT PROBE  --  {len(brk)} IS_GP breaks across {len(ciks)} names", ""]
    for k in sorted(brk, key=lambda x: (x[0], x[1])):
        cik, fy = k
        r = fund.get(k, {})
        rv, cg, gp = fnum(r.get("revenue")), fnum(r.get("cost_of_revenue")), fnum(r.get("gross_profit"))
        L.append(f"=== cik {cik} FY{fy}   residual={brk[k]:,.0f}   "
                 f"OURS: rev={rv and f'{rv:,.0f}'} cogs={cg and f'{cg:,.0f}'} gp={gp and f'{gp:,.0f}'} ===")
        d = facts.get(k, {})
        revs = sorted(((t, v) for t, v in d.items() if REV_PAT.search(t) and not COST_PAT.search(t)),
                      key=lambda x: -abs(x[1]))[:8]
        cogs = sorted(((t, v) for t, v in d.items() if COST_PAT.search(t)), key=lambda x: -abs(x[1]))[:6]
        gpt = [(t, v) for t, v in d.items() if t in ("GrossProfit",)]
        L.append("   revenue-class tags:")
        for t, v in revs:
            L.append(f"       {v:>20,.0f}  {t}")
        L.append("   cost-class tags:")
        for t, v in cogs:
            L.append(f"       {v:>20,.0f}  {t}")
        if gpt:
            L.append(f"   reported GrossProfit: {gpt[0][1]:,.0f}")
        L.append("")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"  -> {OUT.name}")


if __name__ == "__main__":
    main()
