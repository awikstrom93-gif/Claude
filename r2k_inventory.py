"""
============================================================
r2k_inventory.py  --  list every file in the project folder and classify it: what's in the
current workflow (KEEP), what's safely regenerable (DELETE-OK), what's the old/deprecated
engine, and what I don't recognize (REVIEW).  It DELETES NOTHING -- it only reports.
============================================================
Run in the project folder. It prints a categorized inventory and writes inventory_report.txt;
send me that file and I'll confirm the REVIEW items and give a final delete list.

RUN:  python r2k_inventory.py
============================================================
"""
from pathlib import Path
import os, re, sys

BASE = Path(os.environ.get("R2KG_BASE", "."))

# ---- exact-name knowledge (filename -> (verdict, note)) ----
PIPELINE = {  # the current workflow, in run order
    "r2k_morningstar_parse.py": "A1 parse Morningstar -> crosswalk + long table",
    "r2k_build_maps.py": "A2 build ticker->CIK maps + universe_ciks.csv (both indices)",
    "r2k_dera_index.py": "A3 index every annual DERA filing",
    "r2k_dera_extract.py": "A4 reconstruct statements from DERA",
    "r2k_dera_classify.py": "A5 identity cascade -> fundamentals_dera.csv + tieout",
    "r2k_dera_to_fundamentals.py": "A6 adapter -> edgar_annual_fundamentals_ASFILED.csv",
    "r2k_step3_analytics.py": "B1 analytics core (also imported by step 6)",
    "r2k_step4_performance.py": "B2 performance",
    "r2k_step5_cohort_attribution.py": "B3 cohort attribution",
    "r2k_step6_index_comparison.py": "B4 R2000G vs S&P600G",
    "r2k_step8_concentration.py": "B5 concentration",
    "r2k_step9_biotech.py": "B6 biotech",
    "r2k_step7_consolidate.py": "B7 consolidate -> IC workbook",
    "r2k_refresh_charts_data.py": "C  preserve hand-formatted charts",
    "r2k_perf_io.py": "shared library (imported by several steps)",
    "WORKFLOW.md": "the run manual",
}
TOOLS = {  # current, optional (QA / diagnostics you may re-run)
    "r2k_reconcile_sources.py": "QA: reconcile pipeline vs Morningstar",
    "r2k_calibrate_tags.py": "QA: tag calibration vs Morningstar (uses companyfacts_cache)",
    "r2k_dera_inspect.py": "QA: inspect DERA schema / reconstruct a few filings",
    "r2k_identity_backstop.py": "QA: identity checks over the dataset",
    "r2k_inventory.py": "this script",
}
DEPRECATED = {  # old companyfacts/tag-priority engine -- NOT in the current workflow
    "r2k_step2_asfiled.py": "OLD extraction engine (replaced by the DERA pipeline)",
    "r2k_step2b_asfiled_pilot.py": "OLD extraction pilot",
    "r2k_build_sp600g_universe.py": "OLD 600G map (replaced by r2k_build_maps.py)",
    "r2k_recover_missing.py": "OLD recovery patch",
    "r2k_override_reconcile.py": "OLD override patch",
    "r2k_completeness_check.py": "OLD completeness QA",
    "r2k_probe_company.py": "OLD per-company probe",
    "r2k_unknown_diagnostic.py": "OLD unknown-cohort diagnostic",
    "r2k_biotech_check.py": "OLD biotech diagnostic",
    "r2k_inspect_perf.py": "OLD performance-file inspector",
}
REVIEW_NAMES = {  # I want to confirm before you delete
    "r2k_snapshot_charts.py": "may be how R2000G_charts_backup.xlsx was first built -- keep if so",
    "ground_truth_template.csv": "possibly hand-curated ground truth (not regenerable?)",
    "manual_value_overrides.csv": "possibly hand-entered overrides (not regenerable?)",
    "regression_set.csv": "possibly hand-curated regression set (not regenerable?)",
}
KEEP_OUTPUT = {
    "R2000G_SmallCapGrowth_Benchmark_Review.xlsx": "THE IC workbook (final)",
    "R2000G_Benchmark_Review_charted.xlsx": "charted IC workbook (final)",
    "R2000G_charts_backup.xlsx": "your hand-formatted charts (NOT regenerable -- keep!)",
}
# regenerable intermediates (recreated by the workflow) -> safe to delete
REGEN_EXACT = {
    "morningstar_long.csv", "securities_crosswalk.csv", "cusip2cik.json", "ticker2cik.json",
    "security_cik_map.json", "temporal_cik_map.json", "universe_ciks.csv", "dera_filing_index.csv",
    "dera_facts.csv", "fundamentals_dera.csv", "tieout_report.csv",
    "edgar_annual_fundamentals_ASFILED.csv", "asfiled_provenance.csv", "asfiled_vs_legacy_diff.csv",
    "Russell2000Growth_Analytics.xlsx", "R2000G_vs_SP600G_Performance.xlsx",
    "R2000G_vs_SP600G_Quality.xlsx", "R2000G_Cohort_Attribution.xlsx", "R2000G_Concentration.xlsx",
    "R2000G_Biotech.xlsx", "sp600g_cik_map.json", "identity_check_report.csv",
    "recovery_report.csv", "ni_recovery_report.csv", "fundamentals_completeness.csv",
    "unknown_cohort_diagnostic.csv",
}
REGEN_PATTERNS = [r"^reconciliation_.*\.csv$", r"^tag_.*\.(csv|txt)$", r"^dera_inspect_.*\.(txt|csv)$",
                  r"\.csv\.bak$", r"^__pycache__$"]
INPUT_PATTERNS = [(r"[Mm]orningstar.*\.xlsx$", "SOURCE: Morningstar statement download"),
                  (r"[Hh]olding.*\.xlsx$", "SOURCE: index/fund holdings"),
                  (r"[Pp]erformance.*\.xlsx$", "SOURCE: performance data"),
                  (r"[Ff]act[Ss]et.*\.xlsx$", "SOURCE: FactSet validation data"),
                  (r"[Mm]emo.*\.md$", "the IC memo (final)")]
CACHE_DIRS = {"companyfacts_cache": "OLD-engine XBRL cache (NOT used by DERA workflow)",
              "submissions_cache": "OLD-engine submissions cache (NOT used by DERA workflow)"}
DATA_DIRS = {"financial_statement_data_sets": "SOURCE: DERA datasets (KEEP -- the foundation)"}


def human(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024: return f"{n:.0f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def dir_size(p):
    tot = 0
    for r, _, fs in os.walk(p):
        for f in fs:
            try: tot += (Path(r) / f).stat().st_size
            except OSError: pass
    return tot


def classify(name):
    if name in PIPELINE: return ("KEEP-WORKFLOW", PIPELINE[name])
    if name in TOOLS: return ("KEEP-TOOL", TOOLS[name])
    if name in KEEP_OUTPUT: return ("KEEP-OUTPUT", KEEP_OUTPUT[name])
    if name in DEPRECATED: return ("DEPRECATED", DEPRECATED[name])
    if name in REVIEW_NAMES: return ("REVIEW", REVIEW_NAMES[name])
    if name in REGEN_EXACT: return ("DELETE-OK (regenerable)", "recreated by the workflow")
    for pat in REGEN_PATTERNS:
        if re.search(pat, name): return ("DELETE-OK (regenerable)", "diagnostic/intermediate output")
    for pat, note in INPUT_PATTERNS:
        if re.search(pat, name): return ("KEEP-INPUT", note)
    if name.endswith(".md"): return ("KEEP-DOC", "project documentation/notes")
    if name.endswith(".pyc"): return ("DELETE-OK (regenerable)", "compiled python")
    if name.endswith(".py"): return ("REVIEW", "a python file I don't recognize -- show me its header")
    return ("REVIEW", "unrecognized -- tell me what it is")


ORDER = ["KEEP-WORKFLOW", "KEEP-TOOL", "KEEP-INPUT", "KEEP-OUTPUT", "KEEP-DOC",
         "DELETE-OK (regenerable)", "DEPRECATED", "REVIEW"]


def main():
    lines = []
    def out(s=""):
        print(s); lines.append(s)

    buckets = {k: [] for k in ORDER}
    # subfolders first
    out("=" * 78)
    out("FOLDERS")
    for p in sorted([d for d in BASE.iterdir() if d.is_dir()]):
        nm = p.name
        if nm in CACHE_DIRS:
            out(f"  DELETE-OK  {nm+'/':<34}{human(dir_size(p)):>10}   {CACHE_DIRS[nm]}")
        elif nm in DATA_DIRS:
            out(f"  KEEP       {nm+'/':<34}{human(dir_size(p)):>10}   {DATA_DIRS[nm]}")
        elif nm == "__pycache__":
            out(f"  DELETE-OK  {nm+'/':<34}{human(dir_size(p)):>10}   compiled python")
        elif nm == ".git":
            out(f"  KEEP       {nm+'/':<34}{human(dir_size(p)):>10}   git history")
        else:
            out(f"  REVIEW     {nm+'/':<34}{human(dir_size(p)):>10}   unrecognized folder -- tell me")

    files = sorted([f for f in BASE.iterdir() if f.is_file()])
    for f in files:
        try: sz = f.stat().st_size
        except OSError: sz = 0
        verdict, note = classify(f.name)
        buckets.setdefault(verdict, []).append((f.name, sz, note))

    for cat in ORDER:
        items = buckets.get(cat, [])
        if not items: continue
        tot = sum(s for _, s, _ in items)
        out("\n" + "=" * 78)
        out(f"{cat}   ({len(items)} files, {human(tot)})")
        for nm, sz, note in sorted(items, key=lambda x: -x[1]):
            out(f"  {nm:<46}{human(sz):>9}   {note}")

    n = len(files)
    regen = sum(s for _, s, _ in buckets.get("DELETE-OK (regenerable)", []))
    out("\n" + "=" * 78)
    out(f"TOTAL: {n} files (+folders).  Safe-to-delete regenerable files: {human(regen)}.")
    out("DEPRECATED scripts can be archived/deleted (old engine, not in the workflow).")
    out("REVIEW items: send me this report and I'll tell you which to keep/delete.")
    (BASE / "inventory_report.txt").write_text("\n".join(lines), encoding="utf-8")
    out(f"\n-> wrote inventory_report.txt  (send me this file)")


if __name__ == "__main__":
    main()
