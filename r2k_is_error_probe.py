"""
r2k_is_error_probe.py  --  diagnose the income-statement true errors the audit flagged:
  revenue<0, GM>100% / COGS<0, and NItoCommon>parentNI.
For each, it shows our selected value + provenance and the AS-FILED candidate tags on the income
statement, so we see exactly which tag was mis-selected (e.g. a contra-revenue line picked as revenue,
or a parent NI under-selected below net-income-available-to-common).

Reads fundamentals_dera.csv + dera_facts.csv. Writes is_error_probe.txt.
RUN:  python r2k_is_error_probe.py
"""
from pathlib import Path
import os, csv, re
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "is_error_probe.txt"

REV_PAT = re.compile(r"revenue|sales|netinterestincome|premiums?earned|noninterestincome", re.I)
COGS_PAT = re.compile(r"costof|costsof", re.I)
NI_PAT = re.compile(r"netincomeloss|profitloss|incomeavailable|incomelossfromcontinuing", re.I)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mprov(p, role):
    for c in (p or "").split(";"):
        if c.strip().startswith(role + "="):
            return c.strip()[len(role) + 1:]
    return "(single-tag pick)"


def main():
    rows = list(csv.DictReader(open(FUND, encoding="utf-8")))
    rev_neg, gm, nicom = [], [], []
    for r in rows:
        rv, cg, gp = fnum(r.get("revenue")), fnum(r.get("cost_of_revenue")), fnum(r.get("gross_profit"))
        ni, nc = fnum(r.get("net_income")), fnum(r.get("net_income_to_common"))
        if rv is not None and rv < 0:
            rev_neg.append(r)
        if (gp is not None and rv is not None and rv > 0 and gp > rv * 1.005) or (cg is not None and cg < 0):
            gm.append(r)
        if nc is not None and ni is not None and ni > 0 and nc > ni * 1.02:
            nicom.append(r)
    want = {(r["cik"], r["fiscal_year"]) for r in rev_neg + gm + nicom}
    ciks = {c for c, _ in want}

    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == "IS" and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None and (row[ci], row[fi]) in want:
                    facts[(row[ci], row[fi])][row[ti]] = v

    L = [f"INCOME-STATEMENT ERROR PROBE  --  revenue<0: {len(rev_neg)}, GM>100%/COGS<0: {len(gm)}, "
         f"NItoCommon>parentNI: {len(nicom)}", ""]

    def dump(title, group, pat, roles):
        L.append(f"=== {title} ===")
        for r in group[:12]:
            k = (r["cik"], r["fiscal_year"])
            L.append(f"  {r['cik']} {r['fiscal_year']}  " +
                     "  ".join(f"{ro}={r.get(ro)}" for ro in roles) +
                     f"   prov[{roles[0]}]={mprov(r.get('provenance'), roles[0])}")
            cand = sorted(((t, v) for t, v in facts.get(k, {}).items() if pat.search(t)),
                          key=lambda x: -abs(x[1]))[:8]
            for t, v in cand:
                L.append(f"        {v:>18,.0f}  {t}")
            L.append("")

    dump("revenue < 0  (which revenue/contra tag did we pick?)", rev_neg, REV_PAT,
         ["revenue", "gross_profit"])
    dump("GM>100% / COGS<0  (revenue vs COGS mis-selection)", gm, REV_PAT,
         ["revenue", "cost_of_revenue", "gross_profit"])
    dump("NItoCommon > parentNI  (is parent NI under-selected?)", nicom, NI_PAT,
         ["net_income", "net_income_to_common"])
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:80]))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
