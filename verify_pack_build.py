"""
Confirm a build ran on the current code.

Two checks, either or both:

    python verify_pack_build.py                     # find the packs and check them
    python verify_pack_build.py "2026 Q2/packs"     # or point it at them

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
    ("build_attribution_json.py", "def resolve_effect_credit",
     "sector credit / worked_against"),
    ("build_quarterly_json.py", "selected by excess",
     "market lists filtered by excess"),
    ("build_quarterly_json.py", "quarterly_excess_direction",
     "trend field renamed"),
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
    doc = json.loads(path.read_text(encoding="utf-8-sig"))
    name = path.name
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
    sectors = doc.get("sector_attribution", [])
    if sectors and "credit" not in sectors[0]:
        problems.append("sector credit missing")
    if "trend_direction" in doc.get("performance_trends", {}):
        problems.append("trend_direction present")

    print(f"  {'OK   ' if not problems else 'STALE'}  {name[:44]:44s} "
          f"{'; '.join(problems)}")
    return not problems


def find_pack_dirs(root: Path) -> list:
    """Any directory named packs within three levels of the script."""
    found = set()
    for pattern in ("packs", "*/packs", "*/*/packs"):
        found.update(p for p in root.glob(pattern) if p.is_dir())
    return sorted(found)


def packs_in(directory: Path) -> list:
    return [
        p for p in sorted(directory.glob("*.json")) if not p.name.startswith("_")
    ]


def main() -> int:
    root = Path(__file__).resolve().parent
    ok = check_code(root)

    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        if not target.exists():
            print(f"\nPACKS\n  {target} does not exist.")
            candidates = find_pack_dirs(root)
            if candidates:
                print("  Pack directories found under this folder:")
                for directory in candidates:
                    count = len(packs_in(directory))
                    print(f"    {directory.relative_to(root)}  ({count} packs)")
            else:
                print("  No directory named 'packs' found under this folder either.")
                print("  Phase 2 may not have written any - check the build output.")
            return 1
        directories = [target] if target.is_dir() else []
        singles = [] if target.is_dir() else [target]
    else:
        directories = find_pack_dirs(root)
        singles = []
        if not directories:
            print("\nPACKS\n  No directory named 'packs' found under this folder.")
            print("  Phase 2 may not have written any - check the build output.")
            return 1

    for directory in directories:
        found = packs_in(directory)
        print(f"\nPACKS ({len(found)} in {directory})")
        if not found:
            print("  directory is empty")
            ok = False
        for pack in found:
            ok = check_pack(pack) and ok

    for pack in singles:
        print(f"\nPACKS (1 file)")
        ok = check_pack(pack) and ok

    print("\n" + ("All current." if ok else "Something is stale - see above."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
