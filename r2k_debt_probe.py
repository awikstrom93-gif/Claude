"""
r2k_debt_probe.py  --  pinpoint the DEBT OVER-CAPTURE behind the `debt > liabilities` plausibility
flags. Debt is a SUBSET of liabilities, so funded debt exceeding total liabilities is always a
double-count (a subtotal summed with its own parts, or a non-debt line -- e.g. convertible PREFERRED
equity -- swept in by the debt name pattern).

For each filing where total_debt > total_liabilities it prints our reconstructed debt and its
PROVENANCE (the exact tags we summed), total liabilities, and every as-filed BS tag the debt pattern
matches -- so the overcount is visible. It also tallies which tags recur in the provenance of
overcounted filings, so the fix to reconstruct_debt() is precise.

Reads fundamentals_dera.csv (total_debt, total_liabilities, provenance), dera_facts.csv (BS tags).
Writes debt_probe.txt.   RUN:  python r2k_debt_probe.py
"""
from pathlib import Path
import os, csv, re
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "debt_probe.txt"

try:
    from r2k_dera_classify import DEBT_INCL, DEBT_EXCL
except Exception:                                  # standalone fallback (keep in sync with the engine)
    DEBT_INCL = re.compile(r"debt|borrow|notespayable|senior.?notes?|term.?loan|revolv|"
                           r"line.?of.?credit|lineofcredit|convertible|financingobligation|"
                           r"loanspayable|subordinat|mediumterm|commercialpaper|vehicleprogram|floor.?plan", re.I)
    DEBT_EXCL = re.compile(r"securit|heldtomaturity|availableforsale|receivable|investment|"
                           r"repaymentsof|proceedsfrom|faceamount|fairvalue|interestrate|interestexpense|"
                           r"rightofuseasset|netinvestmentinlease|salestypelease|fixedmaturit|weightedaverage|"
                           r"allowance|unamortized|issuancecost|financingcost|covenant|redemption|conversion|"
                           r"numberof|percentage|paymentsof|accruedinterest|deferred|restrictedcash|grossnotes|noncash", re.I)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def main():
    over = []   # (cik, fy, debt, liab, prov)
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        debt, tl = fnum(r.get("total_debt")), fnum(r.get("total_liabilities"))
        if debt is not None and tl is not None and tl > 0 and debt > tl * 1.05:
            over.append((r["cik"], r["fiscal_year"], debt, tl, r.get("provenance", "")))
    ciks = {c for c, *_ in over}

    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == "BS" and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v

    # which provenance tags recur in overcounted filings, and which debt-pattern tags look swept-in
    prov_tags = Counter()
    suspect = Counter()    # debt-pattern tags present that are plausibly NON-debt (preferred/equity-ish)
    EQUITYISH = re.compile(r"preferred|temporaryequity|redeemable|warrant|mezzanine|stockholders", re.I)
    L = [f"DEBT OVER-CAPTURE PROBE  --  {len(over):,} filings where total_debt > total_liabilities", ""]
    for cik, fy, debt, tl, prov in sorted(over, key=lambda x: -(x[2] - x[3]))[:40]:
        d = facts.get((cik, fy), {})
        dtags = {t: v for t, v in d.items() if DEBT_INCL.search(t) and not DEBT_EXCL.search(t)}
        for t in prov.split("+"):
            if t:
                prov_tags[t.strip()] += 1
        for t in dtags:
            if EQUITYISH.search(t):
                suspect[t] += 1
        L.append(f"=== cik {cik} FY{fy}   debt={debt:,.0f}  >  liab={tl:,.0f}  (excess {debt-tl:,.0f}) ===")
        L.append(f"   prov: {prov}")
        for t, v in sorted(dtags.items(), key=lambda x: -abs(x[1]))[:10]:
            mark = "  <-- equity/preferred-ish?" if EQUITYISH.search(t) else ""
            L.append(f"       {v:>18,.0f}  {t}{mark}")
        L.append("")
    L.append("TOP TAGS in the provenance of overcounted filings (candidate double-counts):")
    for t, n in prov_tags.most_common(25):
        L.append(f"   {n:>4}  {t}")
    if suspect:
        L.append("\nDEBT-PATTERN tags that look like EQUITY/PREFERRED (likely wrongly swept into debt):")
        for t, n in suspect.most_common(20):
            L.append(f"   {n:>4}  {t}")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:60]))
    print(f"\n  -> {OUT.name}  ({len(over)} overcounted filings)")


if __name__ == "__main__":
    main()
