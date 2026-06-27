"""
============================================================
r2k_cleanup.py  --  safely declutter the project folder. Keeps the current DERA workflow,
its inputs/outputs/state, and your docs; MOVES everything else into a dated _archive/ folder
(recoverable); and (only when you ask) deletes the giant old caches.
============================================================
DRY-RUN BY DEFAULT -- prints exactly what it would archive/delete and the space freed, and
changes NOTHING. Review it, then re-run with --apply.

  python r2k_cleanup.py                 # dry run: show the plan
  python r2k_cleanup.py --apply         # move old top-level files into _archive_<date>\
  python r2k_cleanup.py --apply --delete-caches   # ALSO permanently delete the big old caches

Safety: it only ever MOVES top-level files (into _archive, same folder) -- nothing is deleted
unless you add --delete-caches, and even then only the named old cache folders. The KEEP list
is an allowlist; anything not explicitly kept is archived (recoverable), so a mistake costs a
move, not a loss.
============================================================
"""
from pathlib import Path
from datetime import datetime
import os, re, sys, shutil

BASE = Path(os.environ.get("R2KG_BASE", "."))
APPLY = "--apply" in sys.argv
DELETE_CACHES = "--delete-caches" in sys.argv

# ---- KEEP: the current workflow + inputs + outputs + current pipeline state + docs ----
KEEP_FILES = {
    # current pipeline scripts
    "r2k_morningstar_parse.py", "r2k_build_maps.py", "r2k_dera_index.py", "r2k_dera_extract.py",
    "r2k_dera_classify.py", "r2k_dera_to_fundamentals.py", "r2k_step3_analytics.py",
    "r2k_step4_performance.py", "r2k_step5_cohort_attribution.py", "r2k_step6_index_comparison.py",
    "r2k_step8_concentration.py", "r2k_step9_biotech.py", "r2k_step7_consolidate.py",
    "r2k_refresh_charts_data.py", "r2k_perf_io.py", "WORKFLOW.md",
    # current QA tools + these utilities
    "r2k_reconcile_sources.py", "r2k_calibrate_tags.py", "r2k_dera_inspect.py",
    "r2k_identity_backstop.py", "r2k_inventory.py", "r2k_cleanup.py", "r2k_snapshot_charts.py",
    # current pipeline STATE (regenerable, but keep so you don't re-run the long extract)
    "dera_facts.csv", "dera_filing_index.csv", "fundamentals_dera.csv", "tieout_report.csv",
    "edgar_annual_fundamentals_ASFILED.csv", "morningstar_long.csv", "securities_crosswalk.csv",
    "cusip2cik.json", "ticker2cik.json", "security_cik_map.json", "temporal_cik_map.json",
    "universe_ciks.csv", "Russell2000Growth_Analytics.xlsx", "R2000G_vs_SP600G_Performance.xlsx",
    "R2000G_vs_SP600G_Quality.xlsx", "R2000G_Cohort_Attribution.xlsx", "R2000G_Concentration.xlsx",
    "R2000G_Biotech.xlsx",
    # final outputs + your charts backup (NOT regenerable)
    "R2000G_SmallCapGrowth_Benchmark_Review.xlsx", "R2000G_Benchmark_Review_charted.xlsx",
    "R2000G_charts_backup.xlsx",
    # small hand-curated work -> keep (cheap, may want later)
    "manual_value_overrides.csv", "manual_cik_overrides.json", "manual_temporal_overrides.json",
    "ground_truth_template.csv", "regression_set.csv", "blacklist_ciks.json",
    "ni_sign_ground_truth.xlsx", "ni_sign_ground_truth_enriched.xlsx",
    "inventory_report.txt", ".gitignore",
}
KEEP_PATTERNS = [
    r"[Mm]orningstar.*\.xlsx$", r"[Hh]olding.*\.xlsx$", r"[Pp]erformance.*\.xlsx$",
    r"[Ff]act[Ss]et.*\.xlsx$", r"\.md$",          # all markdown docs
]
KEEP_DIRS = {"financial_statement_data_sets", ".git", ".claude", "__pycache__"}  # pycache harmless
CACHE_DIRS = {  # big old caches/diagnostic folders -> delete only with --delete-caches
    "companyfacts_cache", "submissions_cache", "sec_cache", "companyfacts",
    "transient_revenue_check", "audit_statements", "review_filings", "Txt Files",
    "Financials Validation - Morningstar",
}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def dirsize(p):
    t = 0
    for r, _, fs in os.walk(p):
        for f in fs:
            try: t += (Path(r) / f).stat().st_size
            except OSError: pass
    return t


def keep_file(name):
    if name in KEEP_FILES: return True
    return any(re.search(p, name) for p in KEEP_PATTERNS)


def main():
    files = [f for f in BASE.iterdir() if f.is_file()]
    dirs = [d for d in BASE.iterdir() if d.is_dir() and not d.name.startswith("_archive")]
    to_archive = [f for f in files if not keep_file(f.name)]
    kept = [f for f in files if keep_file(f.name)]
    caches = [d for d in dirs if d.name in CACHE_DIRS]
    other_dirs = [d for d in dirs if d.name not in CACHE_DIRS and d.name not in KEEP_DIRS]

    arch_sz = sum(f.stat().st_size for f in to_archive)
    cache_sz = sum(dirsize(d) for d in caches)
    mode = "APPLY" if APPLY else "DRY-RUN (nothing changed)"
    print(f"  === r2k_cleanup [{mode}] ===")
    print(f"  KEEP: {len(kept)} top-level files + folders {sorted(KEEP_DIRS & {d.name for d in dirs})}")

    print(f"\n  ARCHIVE -> _archive\\  ({len(to_archive)} files, {human(arch_sz)}):")
    for f in sorted(to_archive, key=lambda x: -x.stat().st_size)[:40]:
        print(f"     {f.name:<52}{human(f.stat().st_size):>9}")
    if len(to_archive) > 40:
        print(f"     ... and {len(to_archive)-40} more")

    print(f"\n  OLD CACHES ({len(caches)} folders, {human(cache_sz)}):  "
          f"{'WILL DELETE' if (APPLY and DELETE_CACHES) else 'delete only with --delete-caches'}")
    for d in caches:
        print(f"     {d.name+'/':<52}{human(dirsize(d)):>9}")
    if other_dirs:
        print(f"\n  LEFT ALONE (unrecognized folders -- tell me): {[d.name for d in other_dirs]}")

    if not APPLY:
        print(f"\n  Dry run only. To execute:  python r2k_cleanup.py --apply [--delete-caches]")
        print(f"  Would free ~{human(arch_sz + (cache_sz if DELETE_CACHES else 0))} "
              f"(archive {human(arch_sz)}{', caches '+human(cache_sz) if DELETE_CACHES else ''}).")
        return

    arch = BASE / f"_archive_{datetime.now():%Y%m%d}"
    arch.mkdir(exist_ok=True)
    moved = 0
    for f in to_archive:
        try:
            shutil.move(str(f), str(arch / f.name)); moved += 1
        except Exception as e:
            print(f"     !! could not move {f.name}: {e}")
    print(f"\n  moved {moved} files -> {arch.name}\\  ({human(arch_sz)})")
    if DELETE_CACHES:
        freed = 0
        for d in caches:
            sz = dirsize(d)
            try:
                shutil.rmtree(d); freed += sz; print(f"     deleted {d.name}/  ({human(sz)})")
            except Exception as e:
                print(f"     !! could not delete {d.name}: {e}")
        print(f"  deleted caches, freed {human(freed)}")
    print(f"\n  Done. Verify the workflow still runs, then delete {arch.name}\\ when comfortable.")
    print(f"  (Everything archived is recoverable until you delete that folder.)")


if __name__ == "__main__":
    main()
