"""
============================================================
r2k_recover_missing.py  --  recover ALL metrics the strict period-end match missed
(generalizes r2k_recover_missing_ni.py to the whole income statement / cash flow).
============================================================
The strict engine requires an EXACT period-end match; for 52/53-week filers the FYE
drifts a few days, so the match fails and the ENTIRE income statement + cash-flow
statement blanks out (net income, revenue, operating income, gross profit, pretax,
tax, CFO, capex), while the balance sheet survives.

This recovers them WITHOUT re-implementing any selection logic: it re-runs the real
engine (extract_company) over only the suspect companies with a relaxed period-end
tolerance (R2KG_END_TOL days), then FILLS ONLY BLANK cells in
edgar_annual_fundamentals_ASFILED.csv (strict values are never overwritten). Filled
cells are also appended to manual_value_overrides.csv for durability.

Targeted (suspect companies only, from cached SEC data), from-source (the engine's own
relaxed-end extraction), and non-destructive (blanks only; a .bak backup is written).

RUN
    python r2k_recover_missing.py --dry-run    # report what would be filled, change nothing
    python r2k_recover_missing.py              # fill blanks + append overrides (R2KG_END_TOL=7)
    set R2KG_END_TOL=5 & python r2k_recover_missing.py   # tighter tolerance
============================================================
"""
import os, sys, csv, shutil
from collections import Counter

if "R2KG_END_TOL" not in os.environ:           # must be set BEFORE importing the engine
    os.environ["R2KG_END_TOL"] = "7"
from r2k_step2_asfiled import extract_company, OUTPUT_FIELDS, BASE_FOLDER, END_TOL_DAYS

FUND = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
COMP = BASE_FOLDER / "fundamentals_completeness.csv"
OVERRIDES = BASE_FOLDER / "manual_value_overrides.csv"
REPORT = BASE_FOLDER / "recovery_report.csv"
DRY = "--dry-run" in sys.argv

IDFIELDS = {"cik", "ticker", "name", "fiscal_year", "fye_date", "filed_date",
            "reporting_basis", "currency", "sector"}
NUM = [f for f in OUTPUT_FIELDS if f not in IDFIELDS]
CORE = ["revenue", "net_income", "operating_income"]


def blank(v): return v is None or str(v).strip() == ""
def ci(c):
    try: return str(int(float(c)))
    except (TypeError, ValueError): return None


def main():
    if not FUND.exists(): raise SystemExit(f"!! {FUND.name} not found.")
    print(f"  engine end-tolerance R2KG_END_TOL = {END_TOL_DAYS} days")
    rows = list(csv.DictReader(open(FUND, encoding="utf-8-sig")))
    flds = list(rows[0].keys()) if rows else []
    idx = {}
    for r in rows:
        c = ci(r.get("cik"));
        try: y = int(float(r["fiscal_year"]))
        except (TypeError, ValueError): continue
        if c: idx[(c, y)] = r

    # suspect CIKs: from the completeness file if present, else income-statement-incomplete rows
    suspects = set()
    if COMP.exists():
        for r in csv.DictReader(open(COMP, encoding="utf-8-sig")):
            c = ci(r.get("cik"));
            if c: suspects.add(c)
    else:
        for r in rows:
            if blank(r.get("total_assets")): continue
            if any(blank(r.get(m)) for m in CORE):
                c = ci(r.get("cik"));
                if c: suspects.add(c)
    print(f"  {len(suspects)} suspect companies to re-extract (relaxed end match)...")

    filled = []          # (cik, year, metric, value)
    failed = []
    for i, c in enumerate(sorted(suspects), 1):
        cik10 = c.zfill(10)
        try:
            _, annual, _, status = extract_company(cik10)
        except Exception as e:
            failed.append([c, f"{type(e).__name__}: {str(e)[:50]}"]); continue
        if not annual: continue
        for y, d in annual.items():
            row = idx.get((c, int(y)))
            if not row: continue
            for m in NUM:
                if blank(row.get(m)) and d.get(m) is not None:
                    filled.append([c, int(y), m, d[m]])
        if i % 50 == 0: print(f"    {i}/{len(suspects)}  (filled so far {len(filled)})")

    # report
    bym = Counter(f[2] for f in filled)
    print(f"\n  recoverable blank cells: {len(filled)} across {len(set((f[0],f[1]) for f in filled))} company-years")
    for m, k in sorted(bym.items(), key=lambda x: -x[1]):
        print(f"    {m:<22}{k}")
    if failed: print(f"  {len(failed)} companies errored (see report)")
    with open(REPORT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "fiscal_year", "metric", "recovered_value"]); w.writerows(filled)
        if failed:
            w.writerow([]); w.writerow(["-- ERRORED --"]); w.writerow(["cik", "reason"]); w.writerows(failed)
    print(f"  -> {REPORT.name}")

    if DRY:
        print("\n  --dry-run: nothing changed. Review recovery_report.csv, then run without --dry-run.")
        return
    if not filled:
        print("\n  nothing to fill."); return

    # 1) patch the CSV in place (blanks only), with backup
    shutil.copy(FUND, FUND.with_suffix(".csv.bak"))
    fillmap = {(c, y, m): v for c, y, m, v in filled}
    patched = 0
    for r in rows:
        c = ci(r.get("cik"))
        try: y = int(float(r["fiscal_year"]))
        except (TypeError, ValueError): continue
        for m in NUM:
            if (c, y, m) in fillmap and blank(r.get(m)):
                r[m] = fillmap[(c, y, m)]; patched += 1
    with open(FUND, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=flds); w.writeheader(); w.writerows(rows)
    print(f"\n  patched {patched} blank cells in {FUND.name} (backup: {FUND.name}.bak)")

    # 2) append durable overrides (deduped)
    out_rows, existing = [], set()
    if OVERRIDES.exists():
        for r in csv.DictReader(open(OVERRIDES, encoding="utf-8-sig")):
            out_rows.append([r.get("cik",""), r.get("fiscal_year",""), r.get("metric",""),
                             r.get("value",""), r.get("source",""), r.get("note","")])
            existing.add((ci(r.get("cik")), str(r.get("fiscal_year","")).strip(),
                          str(r.get("metric","")).strip().lower()))
    added = 0
    for c, y, m, v in filled:
        if (c, str(y), m) in existing: continue
        out_rows.append([int(c), y, m, v, f"as-filed 10-K (relaxed end match +/-{END_TOL_DAYS}d)",
                         "recovered missed duration metric (52/53-week FYE drift)"])
        added += 1
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","fiscal_year","metric","value","source","note"]); w.writerows(out_rows)
    print(f"  appended {added} overrides -> {OVERRIDES.name}")
    print("  -> re-run r2k_step3/5/6/9 + r2k_step7 (and r2k_unknown_diagnostic) to refresh with the recoveries.")


if __name__ == "__main__":
    main()
