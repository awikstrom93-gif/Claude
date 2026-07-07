"""
r2k_report.py  --  ONE command to build the whole IC workbook, panel-first and self-checking.

It runs the pipeline in the correct order, starting from the canonical panel:
   1. r2k_build_analytics.py   -- rebuilds the panel + the panel-sourced ANALYTICS workbook
                                  (replaces the legacy r2k_step3_analytics.py)
   2. step4 performance, step5 attribution, step6 comparison, step8 concentration, step9 biotech
                                  (returns/comparison scripts -- already on the shared helpers)
   3. r2k_step7_consolidate.py -- assembles the IC workbook (incl. the Data Reliability tab)
Then a CONSISTENCY GUARD recomputes the R2000G headline straight off the panel and checks the
assembled workbook's panel-sourced tab (R2KG Quality Trends) AND the independently-built step6 tab
(Qual R2000G) BOTH agree with it -- the tripwire that would have caught the CZR step3-vs-step6 split.

RUN:  python r2k_report.py            # full build + guard
      python r2k_report.py --guard    # just re-run the consistency guard on the existing workbook
      python r2k_report.py --skip step4_performance,step5_cohort_attribution   # skip named steps
"""
import sys
import subprocess
import re
from pathlib import Path

from r2k_universe import BASE, get_panel, by_index_year
from r2k_step3_analytics import dedup_cik

WORKBOOK = BASE / "R2000G_SmallCapGrowth_Benchmark_Review.xlsx"
PIPELINE = [
    ("build analytics (panel)", "r2k_build_analytics.py"),
    ("step4 performance",       "r2k_step4_performance.py"),
    ("step5 cohort attribution", "r2k_step5_cohort_attribution.py"),
    ("step6 index comparison",  "r2k_step6_index_comparison.py"),
    ("step8 concentration",     "r2k_step8_concentration.py"),
    ("step9 biotech",           "r2k_step9_biotech.py"),
    ("step7 consolidate",       "r2k_step7_consolidate.py"),
]
GUARD_TOL = 0.15   # $B; panel vs workbook headline tolerance (rounding across the chain)


def run_pipeline(skip):
    for label, script in PIPELINE:
        stem = Path(script).stem
        if stem in skip:
            print(f"  -- SKIP {label} ({script})")
            continue
        print(f"\n===== {label}  ({script}) =====")
        r = subprocess.run([sys.executable, str(BASE / script)], cwd=str(BASE))
        if r.returncode != 0:
            print(f"\n!! {script} failed (exit {r.returncode}). Stopping.")
            return False
    return True


def _panel_headline():
    """{year: (tot_rev$B, tot_ni$B)} for R2000G straight off the panel."""
    grp = by_index_year(get_panel(index="R2KG"))
    out = {}
    for (ix, y), members in grp.items():
        # dedupe by cik so dual share classes / repeated holdings count each company's dollars once --
        # matches the deduped totals the workbook tabs now report (index_quality / quality-trends).
        cov = dedup_cik([r for r in members if r["covered"]])
        rev = sum(r["revenue"] for r in cov if r["revenue"]) / 1e9
        ni = sum(r["net_income"] for r in cov if r["net_income"] is not None) / 1e9
        out[y] = (round(rev, 1), round(ni, 1))
    return out


def _tab_headline(ws, rev_col, ni_col=None):
    """{year: (rev, ni)} read from the ANNUAL block of a consolidated tab; ni_col=None if the tab has no
    NI column. Reads ONLY rows whose first column is a bare 4-digit year -- the quality tabs now carry a
    QUARTERLY companion block below the annual one (labels like '2012 Q1'), and parsing those as year
    2012 would let the last quarter silently overwrite the annual headline (a false guard failure)."""
    out = {}
    for r in range(4, ws.max_row + 1):
        snap = ws.cell(r, 1).value
        if snap is None:
            continue
        s = str(snap).strip()
        if not re.fullmatch(r"\d{4}(\.0)?", s):   # bare year only; reject 'YYYY Qn' and dates
            continue
        y = int(float(s))
        if y in out:                              # keep the first (annual) occurrence
            continue
        rev = ws.cell(r, rev_col).value
        ni = ws.cell(r, ni_col).value if ni_col else None
        out[y] = (rev, ni)
    return out


def _dup_double_count():
    """{year: naive$B - deduped$B} for R2000G revenue: how much a per-row sum would double-count
    duplicate constituents (dual share classes / repeated holdings). Non-zero means duplicates exist and
    the reported tabs MUST dedupe -- the guard below then checks the tabs match the DEDUPED headline, so
    a tab that ever reverts to naive summing diverges and fails."""
    grp = by_index_year(get_panel(index="R2KG"))
    out = {}
    for (ix, y), members in grp.items():
        cov = [r for r in members if r["covered"]]
        naive = sum(r["revenue"] for r in cov if r["revenue"]) / 1e9
        ded = sum(r["revenue"] for r in dedup_cik(cov) if r["revenue"]) / 1e9
        out[y] = round(naive - ded, 2)
    return out


def guard():
    """Cross-check: panel headline == R2KG Quality Trends (panel-sourced) == Qual R2000G (step6).
    The panel headline and both tabs dedupe by cik, so a duplicate constituent's dollars count once and
    a tab that ever reverts to per-row summing would diverge from the deduped headline and FAIL here."""
    if not WORKBOOK.exists():
        print(f"  !! {WORKBOOK.name} not found -- run the full build first.")
        return False
    import openpyxl
    wb = openpyxl.load_workbook(WORKBOOK, data_only=True)
    panel = _panel_headline()
    ok = True

    dup = _dup_double_count()
    dup_years = {y: v for y, v in dup.items() if abs(v) > GUARD_TOL}
    if dup_years:
        worst = max(dup_years.items(), key=lambda kv: abs(kv[1]))
        print(f"  note: {len(dup_years)} year(s) carry duplicate constituents (dual share classes / repeated "
              f"holdings); deduped headline counts each company once (worst {worst[0]}: ${worst[1]:.1f}B "
              f"would be double-counted). Tabs are checked against the DEDUPED headline below.")

    # R2KG Quality Trends: col B = Total Rev $B, col C = Total NI $B
    if "R2KG Quality Trends" in wb.sheetnames:
        qt = _tab_headline(wb["R2KG Quality Trends"], rev_col=2, ni_col=3)
        for y, (prev, pni) in panel.items():
            if y in qt and qt[y][0] is not None:
                if abs(qt[y][0] - prev) > GUARD_TOL or abs((qt[y][1] or 0) - pni) > GUARD_TOL:
                    print(f"  FAIL  R2KG Quality Trends {y}: tab=({qt[y][0]},{qt[y][1]}) panel=({prev},{pni})")
                    ok = False
    else:
        print("  (R2KG Quality Trends tab missing)"); ok = False

    # Qual R2000G (step6, independent): Tot Rev $B is FULL_COLS index 12 (col L)
    if "Qual R2000G" in wb.sheetnames:
        qr = _tab_headline(wb["Qual R2000G"], rev_col=12)
        for y, (prev, _pni) in panel.items():
            if y in qr and qr[y][0] is not None and abs(qr[y][0] - prev) > GUARD_TOL:
                print(f"  FAIL  Qual R2000G {y}: tab rev={qr[y][0]} panel rev={prev}")
                ok = False
    else:
        print("  (Qual R2000G tab missing -- step6 not consolidated?)")

    if ok:
        print(f"  PASS  panel == R2KG Quality Trends == Qual R2000G  (all years within ${GUARD_TOL}B).")
        print("        the step3-vs-step6 divergence that started this work cannot recur silently.")
    return ok


def main():
    args = sys.argv[1:]
    if "--guard" in args:
        sys.exit(0 if guard() else 1)
    skip = set()
    if "--skip" in args:
        skip = set(args[args.index("--skip") + 1].split(","))
    print("R2000G IC workbook -- one-command build (panel-first)\n")
    if not run_pipeline(skip):
        sys.exit(1)
    print("\n===== CONSISTENCY GUARD =====")
    ok = guard()
    print(f"\n  DONE -> {WORKBOOK.name}" + ("" if ok else "   (guard FAILED -- inspect above)"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
