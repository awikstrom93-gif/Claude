"""
Confirm a build ran on the current code.

Two checks, either or both:

    python verify_pack_build.py                     # check the source files
    python verify_pack_build.py "2026 Q2/packs"     # also check the built packs

Every line prints OK or STALE. A STALE pack was written by an older
build_attribution_json.py whatever the source files now say, so rebuild.

Put this next to build_attribution_json.py and run it from there.
"""

import json
import sys
from pathlib import Path

CODE_CHECKS = (
    ("build_attribution_json.py", "TOP_N_SECURITIES = 5",
     "five movers a side"),
    ("build_attribution_json.py", "sector_contribution_total",
     "benchmark sector total renamed"),
    ("build_attribution_json.py", "Interaction is deliberately not a candidate",
     "interaction cannot be the driver"),
    ("build_quarterly_json.py", "selected by excess",
     "market lists filtered by excess"),
)


def check_code(root: Path) -> bool:
    print("CODE")
    ok = True
    for filename, marker, label in CODE_CHECKS:
        path = root / filename
        if not path.exists():
            print(f"  MISSING  {filename}")
            ok = False
            continue
        found = marker in path.read_text(encoding="utf-8")
        print(f"  {'OK   ' if found else 'STALE'}  {filename:26s} {label}")
        ok = ok and found
    return ok


def check_pack(path: Path) -> bool:
    doc = json.loads(path.read_text(encoding="utf-8"))
    name = doc.get("manager") or doc.get("strategy_name") or path.stem
    summary = doc.get("attribution_summary", {})
    trends = doc.get("attribution_trends", {})
    context = doc.get("benchmark_sector_context", {})

    problems = []
    for key in ("top_contributors", "top_detractors"):
        count = len(doc.get(key, []))
        if count > 5:
            problems.append(f"{count} {key.split('_')[1]}")
    if "primary_driver" in summary:
        problems.append("primary_driver present")
    if "driver_stability" in trends:
        problems.append("driver_stability present")
    if "benchmark_return" in context:
        problems.append("benchmark_sector_context.benchmark_return present")
    if summary.get("driver_label") == "interaction":
        problems.append("driver_label is interaction")

    print(f"  {'OK   ' if not problems else 'STALE'}  {str(name)[:38]:38s} "
          f"{'; '.join(problems)}")
    return not problems


def main() -> int:
    root = Path(__file__).resolve().parent
    ok = check_code(root)

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        packs = sorted(target.glob("*.json")) if target.is_dir() else [target]
        packs = [p for p in packs if not p.name.startswith("_")]
        print(f"\nPACKS ({len(packs)} under {target})")
        if not packs:
            print("  none found - check the path")
            ok = False
        for pack in packs:
            ok = check_pack(pack) and ok

    print("\n" + ("All current." if ok else "Something is stale - see above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
