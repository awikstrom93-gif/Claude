"""
r2k_bsfoots_diagnose.py  --  ROOT-CAUSE buckets for the remaining BS_FOOTS breaks.

The tag-frequency probe (r2k_bsfoots_probe.py) is too blunt for the balance sheet: with dozens of
line items, some tag coincidentally equals the A-(L+E+mezz) gap, so the "matches" are mostly noise
(generic line items, near-zero per-share values). This script instead asks, per break, WHICH of our
selected major totals disagrees with the filer's reported total by ~the gap -- the actual cause:

  ASSETS_MISSELECT  our total_assets      != reported Assets,         and the diff ~= gap
  LIAB_MISSELECT    our total_liabilities != reported Liabilities,    and the diff ~= gap
  EQUITY_INCL_NCI   our total_equity      != reported equity-incl-NCI,and the diff ~= gap
                    (we used parent equity where the A=L+E line needs equity INCLUDING NCI)
  NCI_NOT_IN_EQUITY gap ~= reported MinorityInterest and we don't carry it in equity or mezz
  EQUITY_MISSELECT  our total_equity      != reported StockholdersEquity (parent), diff ~= gap
  SMALL_FOOT        |gap| is immaterial (< $2M and < 1% of assets) -- rounding / a tolerance edge
  OTHER             none of the above (genuine multi-component / deeper mis-selection)

Reads tieout_report.csv (BS_FOOTS breaks + residual), fundamentals_dera.csv (our selected totals),
dera_facts.csv (the reported as-filed totals). Writes bsfoots_diagnose.txt.

RUN:  python r2k_bsfoots_diagnose.py
"""
from pathlib import Path
import os, csv
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "bsfoots_diagnose.txt"

EQ_INCL = ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
EQ_PARENT = ["StockholdersEquity"]
REPORTED = ["Assets", "Liabilities", "LiabilitiesAndStockholdersEquity",
            "StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            "MinorityInterest"]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b, rel=0.01, ab=1_000_000.0):
    return a is not None and b is not None and abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def main():
    # BS_FOOTS breaks + signed residual (gap = A - (L + E + mezz))
    gaps = {}
    for r in csv.DictReader(open(TIE, encoding="utf-8")):
        if r["identity"].startswith("BS_FOOTS") and r["result"] == "BREAK":
            g = fnum(r["residual"])
            if g is not None:
                gaps[(r["cik"], r["fiscal_year"])] = g
    fund = {(r["cik"], r["fiscal_year"]): r for r in csv.DictReader(open(FUND, encoding="utf-8"))}

    # reported as-filed BS totals for the break filings
    ciks = {c for c, _ in gaps}
    rep = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        want = set(REPORTED)
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == "BS" and row[ui] == "USD" and row[ti] in want:
                v = fnum(row[vi])
                if v is not None:
                    rep[(row[ci], row[fi])][row[ti]] = v

    bucket = Counter()
    examples = defaultdict(list)
    for k, g in gaps.items():
        f = fund.get(k, {})
        rp = rep.get(k, {})
        oA = fnum(f.get("total_assets"))
        oL = fnum(f.get("total_liabilities"))
        oE = fnum(f.get("total_equity"))
        rA = rp.get("Assets")
        rL = rp.get("Liabilities")
        rEi = rp.get("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest")
        rEp = rp.get("StockholdersEquity")
        rNCI = rp.get("MinorityInterest")
        ag = abs(g)

        # ordered classification -- first match wins
        if oA is not None and rA is not None and not close(oA, rA) and close(abs(oA - rA), ag):
            b = "ASSETS_MISSELECT"
        elif oL is not None and rL is not None and not close(oL, rL) and close(abs(oL - rL), ag):
            b = "LIAB_MISSELECT"
        elif (oE is not None and rEi is not None and not close(oE, rEi)
              and close(abs(oE - rEi), ag)):
            b = "EQUITY_INCL_NCI"   # should have used equity INCLUDING NCI
        elif rNCI is not None and close(ag, abs(rNCI)) and (oE is None or rEi is None or not close(oE, rEi)):
            b = "NCI_NOT_IN_EQUITY"
        elif (oE is not None and rEp is not None and not close(oE, rEp)
              and close(abs(oE - rEp), ag)):
            b = "EQUITY_MISSELECT"
        elif ag < 2_000_000 and (oA is None or ag < 0.01 * abs(oA)):
            b = "SMALL_FOOT"
        else:
            b = "OTHER"
        bucket[b] += 1
        if len(examples[b]) < 4:
            examples[b].append((k[0], k[1], f"gap={g:,.0f}",
                                f"A o={oA} r={rA}", f"L o={oL} r={rL}",
                                f"E o={oE} rIncl={rEi} rParent={rEp} NCI={rNCI}"))

    L = [f"BS_FOOTS ROOT-CAUSE DIAGNOSE  --  {len(gaps):,} breaks bucketed by why they fail", ""]
    for b, n in bucket.most_common():
        L.append(f"   {n:>5}  {b}")
    L.append("")
    for b, _ in bucket.most_common():
        L.append(f"--- {b} (examples) ---")
        for ex in examples[b]:
            L.append("     " + "  |  ".join(ex))
        L.append("")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"  -> {OUT.name}")


if __name__ == "__main__":
    main()
