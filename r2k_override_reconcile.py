"""
============================================================
r2k_override_reconcile.py  --  reconcile a LEGACY manual-override file against
the new as-filed engine, so legacy (companyfacts-calibrated) overrides don't
silently corrupt the clean rebuild.
============================================================
For every (cik, fiscal_year, metric, override_value) in the input file, it runs
the new as-filed engine and classifies:

  REDUNDANT      new engine already == override   -> the rebuild fixed it; DROP
  KEEP           new engine is BLANK              -> genuine manual fill; carry forward
  SIGN_CONFLICT  new and override differ in SIGN  -> 10-K adjudication (filer mis-tag vs bad guess)
  DIFFERS        both present, differ >1%, same sign -> 10-K adjudication

Outputs (into BASE_FOLDER):
  manual_value_overrides.csv   the CLEAN file for the new engine = KEEP rows only
  overrides_to_review.csv      SIGN_CONFLICT + DIFFERS with both values + EDGAR link
  overrides_reconciled.csv     full audit: every row + its classification

RUN
    python r2k_override_reconcile.py            # input: manual_value_overrides_LEGACY.csv
    # or: set R2KG_OV_IN=somefile.csv
============================================================
"""
import csv, os
from pathlib import Path
from r2k_step2_asfiled import extract_company, ALIAS, BASE_FOLDER

IN = BASE_FOLDER / os.environ.get("R2KG_OV_IN", "manual_value_overrides_LEGACY.csv")
CLEAN = BASE_FOLDER / "manual_value_overrides.csv"
REVIEW = BASE_FOLDER / "overrides_to_review.csv"
AUDIT = BASE_FOLDER / "overrides_reconciled.csv"
TOL = 0.01

# PLXS 2024 override carried forward into the clean file (GT-confirmed, this rebuild's own find)
SEED = [("0000785786", 2024, "revenue", 3960827000.0,
         "10-K FY2024 Net sales p.43", "RFCwC-excl mis-tagged > RFCwC-incl; no Revenues tag")]


def edgar(cik):
    return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={int(cik)}&type=10-K&count=40"


def num(x):
    try: return float(str(x).replace(",", "").replace("$", ""))
    except Exception: return None


def main():
    if not IN.exists():
        print(f"!! {IN.name} not found (rename your legacy file to that, or set R2KG_OV_IN)."); return
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    print(f"Reconciling {len(rows)} override rows against the as-filed engine...")
    cache = {}
    def asfiled_val(cik, fy, metric):
        c = str(cik).zfill(10)
        if c not in cache:
            try: cache[c] = extract_company(c)[1]
            except Exception: cache[c] = {}
        return cache[c].get(int(fy), {}).get(metric)

    audit, clean, review = [], list(SEED), []
    counts = {"REDUNDANT": 0, "KEEP": 0, "SIGN_CONFLICT": 0, "DIFFERS": 0, "BAD_ROW": 0}
    for r in rows:
        cik = str(r.get("cik", "")).strip().zfill(10)
        metric = ALIAS.get(str(r.get("metric", "")).strip().lower(), str(r.get("metric", "")).strip().lower())
        try: fy = int(r["fiscal_year"])
        except Exception: counts["BAD_ROW"] += 1; continue
        ov = num(r.get("value", ""))
        if ov is None: counts["BAD_ROW"] += 1; continue
        nv = asfiled_val(cik, fy, metric)
        if nv is None:
            cls = "KEEP"
            clean.append((cik, fy, metric, ov, r.get("source", ""), r.get("note", "")))
        elif abs(nv - ov) <= max(abs(ov), 1) * TOL:
            cls = "REDUNDANT"
        elif (nv > 0) != (ov > 0):
            cls = "SIGN_CONFLICT"
            review.append((cik, fy, metric, ov, nv, cls, edgar(cik), r.get("source",""), r.get("note","")))
        else:
            cls = "DIFFERS"
            review.append((cik, fy, metric, ov, nv, cls, edgar(cik), r.get("source",""), r.get("note","")))
        counts[cls] = counts.get(cls, 0) + 1
        audit.append([cik, fy, metric, ov, nv, cls, r.get("source", "")])

    with open(CLEAN, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "fiscal_year", "metric", "value", "source", "note"])
        for c, fy, m, v, s, n in clean: w.writerow([c, fy, m, v, s, n])
    with open(REVIEW, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "fiscal_year", "metric", "override_value",
            "asfiled_value", "classification", "edgar_url", "orig_source", "orig_note"])
        w.writerows(review)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "fiscal_year", "metric", "override_value",
            "asfiled_value", "classification", "orig_source"]); w.writerows(audit)

    print("\n==== RECONCILIATION ====")
    for k in ["REDUNDANT", "KEEP", "SIGN_CONFLICT", "DIFFERS", "BAD_ROW"]:
        print(f"  {k:<14} {counts.get(k,0)}")
    print(f"\n  clean override file  -> {CLEAN.name}   ({len(clean)} rows: KEEP + PLXS seed)")
    print(f"  to review (10-K)     -> {REVIEW.name}   ({len(review)} rows)")
    print(f"  full audit           -> {AUDIT.name}")
    print("\n  NEXT: open overrides_to_review.csv. For each SIGN_CONFLICT/DIFFERS, open the EDGAR")
    print("  10-K and decide which value is right. If the OVERRIDE is right (e.g. a filer XBRL")
    print("  sign mis-tag like Life Storage 944314), append that row to manual_value_overrides.csv.")
    print("  Then re-run r2k_step2_asfiled.py with the clean override file in place.")


if __name__ == "__main__":
    main()
