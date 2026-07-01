"""
r2k_recover_all.py  --  one command for the recover-and-verify chain, in the correct order.

Runs each step as its own process (exactly as if you typed it), streams the output live, STOPS on the
first failure, and at the end surfaces the cross-check regression-gate verdict (PASS / WARNING).

DEFAULT ORDER (what you asked for):
    1. r2k_revenue_recover.py     recover blank revenue as-filed (segment sums, sector tags)
    2. r2k_metric_recover.py      recover blank COGS/GP/op-inc/equity/cash/capex/FCF (identity-first)
    3. r2k_metric_crosscheck.py   verify present values vs Morningstar + the REGRESSION GATE

FLAGS:
    --classify   run r2k_dera_classify.py FIRST (rebuild fundamentals_dera.csv, fold in learned tags)
    --gaps       run r2k_gap_tags.py before the cross-check (blank diagnostic; needs dera_facts.csv;
                 auto-skipped if that file isn't present)
    --full       the whole refresh: classify -> revenue_recover -> metric_recover ->
                 dera_to_fundamentals -> plausibility -> report -> crosscheck
    --dry-run    print the plan and exit

TYPICAL USE:
    python r2k_dera_classify.py           # (or add --classify below to include it)
    python r2k_recover_all.py             # revenue_recover -> metric_recover -> crosscheck
    python r2k_recover_all.py --classify  # also rebuild the base first (folds in newly-learned tags)
    python r2k_recover_all.py --gaps      # also emit gap_tag_queue.csv for tag review
"""
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path(os.environ.get("R2KG_BASE", HERE))

SCRIPTS = {
    "classify":        "r2k_dera_classify.py",
    "revenue_recover": "r2k_revenue_recover.py",
    "metric_recover":  "r2k_metric_recover.py",
    "gaps":            "r2k_gap_tags.py",
    "to_fundamentals": "r2k_dera_to_fundamentals.py",
    "plausibility":    "r2k_plausibility.py",
    "report":          "r2k_report.py",
    "crosscheck":      "r2k_metric_crosscheck.py",
}
LABEL = {
    "classify":        "rebuild fundamentals_dera.csv (folds in learned tags)",
    "revenue_recover": "recover blank revenue as-filed",
    "metric_recover":  "recover blank COGS/GP/op-inc/equity/cash/capex/FCF (identity-first)",
    "gaps":            "emit gap_tag_queue.csv (blank diagnostic)",
    "to_fundamentals": "write edgar_annual_fundamentals_ASFILED.csv",
    "plausibility":    "reliability tiers (clean/watch/review)",
    "report":          "build the IC workbook",
    "crosscheck":      "verify vs Morningstar + REGRESSION GATE",
}


def plan(args):
    if args.full:
        return ["classify", "revenue_recover", "metric_recover", "to_fundamentals",
                "plausibility", "report", "crosscheck"]
    steps = []
    if args.classify:
        steps.append("classify")
    steps += ["revenue_recover", "metric_recover"]
    if args.gaps:
        steps.append("gaps")
    steps.append("crosscheck")
    return steps


def run_step(key):
    """Run one script as a subprocess, streaming its output. Returns (ok, seconds)."""
    script = HERE / SCRIPTS[key]
    if not script.exists():
        print(f"  !! {script.name} not found next to this runner -- skipping.")
        return False, 0.0
    if key == "gaps" and not (BASE / "dera_facts.csv").exists():
        print("  (skipping gap_tags: dera_facts.csv not present in the folder)")
        return True, 0.0
    print(f"\n{'='*78}\n>>> {key}  --  {LABEL[key]}\n    python {script.name}\n{'='*78}")
    t0 = time.time()
    rc = subprocess.run([sys.executable, str(script)], cwd=str(HERE)).returncode
    dt = time.time() - t0
    if rc != 0:
        print(f"\n  !! {script.name} exited with code {rc} after {dt:.0f}s -- STOPPING the chain.")
        return False, dt
    print(f"    ({key} done in {dt:.0f}s)")
    return True, dt


def crosscheck_verdict():
    """Pull the OVERALL line from the cross-check report so the runner can headline PASS / WARNING."""
    rpt = BASE / "metric_crosscheck_report.txt"
    if not rpt.exists():
        return None
    for line in rpt.read_text(encoding="utf-8", errors="replace").splitlines():
        if "OVERALL:" in line:
            return line.strip()
    return None


def main():
    ap = argparse.ArgumentParser(description="Run the recover-and-verify chain in order.")
    ap.add_argument("--classify", action="store_true", help="run r2k_dera_classify.py first")
    ap.add_argument("--gaps", action="store_true", help="also run r2k_gap_tags.py (needs dera_facts.csv)")
    ap.add_argument("--full", action="store_true", help="classify->recover->to_fundamentals->plausibility->report->crosscheck")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args()

    steps = plan(args)
    print("R2KG recover-and-verify chain")
    print("  folder :", HERE)
    print("  plan   :", " -> ".join(steps))
    if args.dry_run:
        return

    results = []
    total = 0.0
    for key in steps:
        ok, dt = run_step(key)
        results.append((key, ok, dt))
        total += dt
        if not ok and key != "gaps":            # a missing optional gaps step is not fatal
            break

    print(f"\n{'='*78}\nSUMMARY  (total {total:.0f}s)")
    for key, ok, dt in results:
        print(f"  {'OK ' if ok else 'FAIL':>4}  {key:>16}  {dt:>5.0f}s  {LABEL[key]}")
    ran_all = all(ok for key, ok, _dt in results if key != "gaps") and len(results) == len(steps)
    if "crosscheck" in [k for k, ok, _dt in results if ok]:
        v = crosscheck_verdict()
        if v:
            print(f"\n  cross-check {v}")
            if "WARNING" in v:
                print("  -> a tag change may be over/understating a metric. Inspect metric_crosscheck.csv "
                      "(worklist) and metric_crosscheck_report.txt (prior->now per metric).")
    if not ran_all:
        print("\n  chain stopped early -- fix the failing step above and re-run.")
        sys.exit(1)
    print("\n  done. If metric_recover reported newly-validated tags, re-run with --classify to fold them "
          "in at the source, then run the normal dera_to_fundamentals -> plausibility -> report.")


if __name__ == "__main__":
    main()
