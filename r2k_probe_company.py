"""
r2k_probe_company.py  --  diagnose WHY a company's net income (and income statement)
isn't being matched, by dumping the raw companyfacts landscape per fiscal year.
============================================================
For each CIK (given as args, or the top ni_blank suspects from unknown_cohort_diagnostic.csv),
prints, per original 10-K fiscal year: the accession + FYE, then every us-gaap record for an
EXPANDED net-income tag family -- its period end, duration (days), accession, filed date, value
-- flagging whether it would match the strict pass, a +/-7d relaxed pass, or neither. This
reveals the cause: wrong tag (value only under e.g. ...AvailableToCommonStockholders), duration
outside the 340-380 window (stub/transition year), end far from the FYE, or simply absent from
companyfacts.

RUN
    python r2k_probe_company.py 0000887596 0001628063     # specific CIKs
    python r2k_probe_company.py                            # top 6 suspects from the diagnostic
============================================================
"""
from pathlib import Path
import os, sys, csv
from r2k_step2_asfiled import original_filing_map, _fetch, CF_CACHE, keyed_year, _days, BASE_FOLDER

DIAG = BASE_FOLDER / "unknown_cohort_diagnostic.csv"
NI_FAMILY = ["NetIncomeLoss", "ProfitLoss",
             "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
             "NetIncomeLossAvailableToCommonStockholdersBasic",
             "NetIncomeLossAvailableToCommonStockholdersDiluted",
             "IncomeLossFromContinuingOperations",
             "ProfitLossFromContinuingOperations"]
REV_PROBE = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"]


def pick_suspects(n=6):
    out = []
    if DIAG.exists():
        seen = set()
        for r in csv.DictReader(open(DIAG, encoding="utf-8-sig")):
            if r["reason"] == "ni_blank" and r["years_with_NI"].strip():
                c = str(int(r["cik"])).zfill(10)
                if c not in seen:
                    seen.add(c); out.append((c, r.get("ticker", ""), int(r["fy0"])))
            if len(out) >= n: break
    return out


def main():
    args = [a for a in sys.argv[1:] if a.isdigit()]
    targets = [(a.zfill(10), "", None) for a in args] if args else pick_suspects()
    if not targets:
        raise SystemExit("give CIKs as args, or run r2k_unknown_diagnostic.py first.")
    for cik, tk, focus_fy in targets:
        cf = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", CF_CACHE / f"CIK{cik}.json")
        usg = cf.get("facts", {}).get("us-gaap", {})
        name = cf.get("entityName", "")
        orig, _, _ = original_filing_map(cik, ("10-K",))
        print(f"\n{'='*78}\nCIK {cik}  {tk}  {name}")
        print(f"  NI-family tags present: {[t for t in NI_FAMILY if t in usg] or 'NONE'}")
        print(f"  revenue tags present:   {[t for t in REV_PROBE if t in usg] or 'NONE'}")
        # focus on a couple of fiscal years (the suspect fy0 +/- 1, else recent)
        fyes = sorted(orig)
        focus = [f for f in fyes if (focus_fy is None or abs(keyed_year(f) - focus_fy) <= 1)]
        for fye in (focus[:3] or fyes[-3:]):
            accn = orig[fye][0]
            print(f"  --- FY {keyed_year(fye)}  (FYE {fye}, orig accn {accn}) ---")
            any_rec = False
            for tag in NI_FAMILY:
                node = usg.get(tag)
                if not node: continue
                for r in node.get("units", {}).get("USD", []):
                    end = r.get("end"); start = r.get("start", "")
                    if not end: continue
                    de = _days(fye, end)
                    if de is None or abs(de) > 35: continue        # only records near this FYE
                    dur = _days(start, end)
                    a = r.get("accn", ""); v = r.get("val")
                    strict = (end == fye and dur is not None and 340 <= dur <= 380 and a == accn)
                    relax = (abs(de) <= 7 and dur is not None and 340 <= dur <= 380)
                    mark = "STRICT" if strict else ("relax7" if relax else ("DUR?" if (dur is None or not (340 <= dur <= 380)) else "end?"))
                    print(f"      {tag:<52} end={end} dur={dur} accn={'orig' if a==accn else a[:12]} val={v:,} [{mark}]")
                    any_rec = True
            if not any_rec:
                print("      (no NI-family record within 35 days of this FYE -- value absent from companyfacts)")
    print()


if __name__ == "__main__":
    main()
