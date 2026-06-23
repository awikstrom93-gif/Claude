"""
============================================================
r2k_recover_missing_ni.py  --  targeted recovery of the net-income cells the
strict as-filed pass missed (the 'ni_blank' suspects from the Unknown diagnostic).
============================================================
The strict engine requires an EXACT period-end match against the original accession.
For a block of older filings (mostly 52/53-week filers + some banks) that exact match
fails for the income statement, leaving net_income blank even though the company DID
report it. This script:

  1. reads the suspects from unknown_cohort_diagnostic.csv (ni_blank with NI elsewhere),
  2. for each unique (CIK, fiscal year), re-reads the CACHED companyfacts and recovers
     net income with a RELAXED matcher (period-end within +/-7 days of the FYE to absorb
     52/53-week drift; original-accession value preferred, else earliest-filed vintage),
  3. writes the recoveries to manual_value_overrides.csv (durable; picked up on any
     future engine run) AND patches edgar_annual_fundamentals_ASFILED.csv in place so the
     analytics see them now -- no full re-extraction.

This is targeted and from-source: only the known-bad CIK-years are touched, and the
recovered value is the company's own reported full-year figure, not an imputation.

RUN
    python r2k_recover_missing_ni.py            # recover + patch CSV + append overrides
    python r2k_recover_missing_ni.py --dry-run  # report only, change nothing
============================================================
"""
from pathlib import Path
from datetime import date
import os, sys, csv, shutil

from r2k_step2_asfiled import (original_filing_map, _fetch, CF_CACHE, keyed_year,
                               _days, BASE_FOLDER)

DIAG = BASE_FOLDER / "unknown_cohort_diagnostic.csv"
OVERRIDES = BASE_FOLDER / "manual_value_overrides.csv"
FUND = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
REPORT = BASE_FOLDER / "ni_recovery_report.csv"
DRY = "--dry-run" in sys.argv

NI_TAGS = ["NetIncomeLoss", "ProfitLoss",
           "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"]
END_TOL_DAYS = 7            # absorb 52/53-week period-end drift
DUR_LO, DUR_HI = 330, 400  # relaxed full-year window


def recover_ni(usg, fye, accn):
    """Return (value, tag, basis, end_used) or (None,...). Relaxed end match; original
    accession preferred, else earliest-filed vintage."""
    cands = []
    for tag in NI_TAGS:
        node = usg.get(tag)
        if not node: continue
        for r in node.get("units", {}).get("USD", []):
            end = r.get("end"); val = r.get("val")
            if not end or val is None: continue
            de = _days(fye, end)
            if de is None or abs(de) > END_TOL_DAYS: continue
            dur = _days(r.get("start", ""), end)
            if dur is None or not (DUR_LO <= dur <= DUR_HI): continue
            cands.append((r.get("filed", ""), r.get("accn", ""), val, tag, end))
    if not cands: return None, None, None, None
    native = [c for c in cands if c[1] == accn]
    pick = native[0] if native else sorted(cands, key=lambda c: c[0])[0]
    return pick[2], pick[3], ("asfiled-relaxed-end" if native else "earliest-relaxed"), pick[4]


def main():
    if not DIAG.exists():
        raise SystemExit(f"!! {DIAG.name} not found -- run r2k_unknown_diagnostic.py first.")
    suspects = {}
    for r in csv.DictReader(open(DIAG, encoding="utf-8-sig")):
        if r["reason"] != "ni_blank" or not r["years_with_NI"].strip(): continue
        try: fy = int(r["fy0"])
        except (TypeError, ValueError): continue
        cik = str(int(r["cik"])).zfill(10)
        suspects.setdefault((cik, fy), r.get("ticker", ""))
    print(f"  {len(suspects)} unique (CIK, fiscal-year) suspects to recover...")

    recovered, failed = [], []
    fmap_cache = {}
    for i, ((cik, fy), tk) in enumerate(sorted(suspects.items()), 1):
        try:
            if cik not in fmap_cache:
                fmap_cache[cik] = original_filing_map(cik, ("10-K",))[0]
            orig = fmap_cache[cik]
            cand_fye = [f for f in orig if keyed_year(f) == fy]
            if not cand_fye:
                failed.append([cik, tk, fy, "no original 10-K for fiscal year"]); continue
            fye = cand_fye[0]; accn = orig[fye][0]
            cf = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", CF_CACHE / f"CIK{cik}.json")
            usg = cf.get("facts", {}).get("us-gaap", {})
            val, tag, basis, end_used = recover_ni(usg, fye, accn)
            if val is None:
                failed.append([cik, tk, fy, "no NI record within +/-7d of FYE"]); continue
            recovered.append([cik, tk, fy, val, tag, basis, end_used, fye])
        except Exception as e:
            failed.append([cik, tk, fy, f"{type(e).__name__}: {str(e)[:50]}"])
        if i % 25 == 0: print(f"    {i}/{len(suspects)}  (recovered {len(recovered)}, failed {len(failed)})")

    # report
    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cik", "ticker", "fiscal_year", "recovered_net_income", "tag", "basis", "end_used", "fye"])
        w.writerows(recovered)
        if failed:
            w.writerow([]); w.writerow(["-- UNRECOVERED --"])
            w.writerow(["cik", "ticker", "fiscal_year", "reason"]); w.writerows(failed)

    print(f"\n  recovered {len(recovered)} cells, {len(failed)} unrecovered -> {REPORT.name}")
    from collections import Counter
    bc = Counter(r[5] for r in recovered)
    for b, n in bc.items(): print(f"    {b}: {n}")
    if recovered[:8]:
        print("  sample:")
        for r in recovered[:8]:
            print(f"    {r[1]:<7} FY{r[2]}  NI {r[3]:>16,.0f}  [{r[5]}, end {r[6]}]")

    if DRY:
        print("\n  --dry-run: no files changed. Review ni_recovery_report.csv, then run without --dry-run.")
        return

    # 1) append to manual_value_overrides.csv (durable)
    existing = set()
    rows_out = []
    if OVERRIDES.exists():
        for r in csv.DictReader(open(OVERRIDES, encoding="utf-8-sig")):
            rows_out.append([r.get("cik",""), r.get("fiscal_year",""), r.get("metric",""),
                             r.get("value",""), r.get("source",""), r.get("note","")])
            existing.add((str(r.get("cik","")).strip(), str(r.get("fiscal_year","")).strip(),
                          str(r.get("metric","")).strip().lower()))
    added = 0
    for cik, tk, fy, val, tag, basis, end_used, fye in recovered:
        key = (str(int(cik)), str(fy), "net_income")
        if key in existing or (cik, str(fy), "net_income") in existing: continue
        rows_out.append([int(cik), fy, "net_income", val,
                         f"as-filed 10-K (relaxed end match, {tag})",
                         f"recovered missed NI; {basis}; period end {end_used}"])
        added += 1
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "fiscal_year", "metric", "value", "source", "note"]); w.writerows(rows_out)
    print(f"\n  appended {added} net_income overrides -> {OVERRIDES.name}")

    # 2) patch edgar_annual_fundamentals_ASFILED.csv in place (immediate effect)
    if FUND.exists():
        shutil.copy(FUND, FUND.with_suffix(".csv.bak"))
        rows = list(csv.DictReader(open(FUND, encoding="utf-8-sig")))
        flds = rows[0].keys() if rows else []
        rec_map = {(str(int(c)), int(fy)): v for c, tk, fy, v, *_ in recovered}
        patched = 0
        for r in rows:
            try: key = (str(int(r["cik"])), int(r["fiscal_year"]))
            except (TypeError, ValueError): continue
            if key in rec_map and (r.get("net_income") in (None, "", )):
                r["net_income"] = rec_map[key]; patched += 1
        with open(FUND, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(flds)); w.writeheader(); w.writerows(rows)
        print(f"  patched {patched} net_income cells in {FUND.name} (backup: {FUND.name}.bak)")
        print("  -> re-run r2k_step5/6/9 + r2k_step7 to refresh the analytics with the recovered NI.")


if __name__ == "__main__":
    main()
