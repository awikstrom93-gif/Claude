#!/usr/bin/env python3
"""
build_battle_book_data.py
=========================

One-command build of every Battle Book Agent data file for a quarter.

Runs the two phases in the only order that works:

    1. build_quarterly_json.py    - manager performance, history, market context
    2. build_attribution_json.py  - per-manager attribution, and the pointers
                                    that link the two together

Order matters. Phase 1 rewrites the asset-class JSON files from scratch, which
removes the attribution pointers Phase 2 adds. Running Phase 1 on its own
therefore leaves the agent unable to find attribution data even when the
attribution files are present on disk. This wrapper exists so that cannot
happen by accident.

Usage
-----
    python build_battle_book_data.py --quarter "2026 Q2"
    python build_battle_book_data.py --quarter latest
    python build_battle_book_data.py --quarter latest \
        --input-root "D:\\test\\in" --output-root "D:\\test\\out"

Exit codes
----------
    0  both phases succeeded
    1  a phase failed (Phase 2 is not run if Phase 1 fails)
    2  bad arguments, or a phase script is missing
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence

SCRIPT_VERSION = "1.0.0"

PHASE1_SCRIPT = "build_quarterly_json.py"
PHASE2_SCRIPT = "build_attribution_json.py"

LOG = logging.getLogger("build_battle_book_data")


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
        stream=sys.stdout,
    )


def resolve_quarter_label(quarter: str, input_root: Optional[str]) -> str:
    """
    Turn 'latest' into a concrete quarter label so both phases build the same one.

    Phase 1 and Phase 2 each resolve 'latest' independently and would normally
    agree, but pinning it once removes any chance of the two phases targeting
    different quarters, and lets the summary name the quarter that was built.
    Falls back to passing the value through untouched if resolution is not
    possible for any reason - the phases handle 'latest' perfectly well on
    their own.
    """
    if quarter.strip().lower() != "latest":
        return quarter

    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from build_quarterly_json import (  # noqa: WPS433 - deliberate late import
            DEFAULT_INPUT_ROOT,
            resolve_quarter,
        )

        root = Path(input_root or DEFAULT_INPUT_ROOT)
        resolved, _ = resolve_quarter(root, "latest")
        LOG.info("Resolved --quarter latest to '%s'", resolved.label)
        return resolved.label
    except Exception as error:  # noqa: BLE001 - resolution is a convenience
        LOG.debug("Could not pre-resolve 'latest' (%s); passing it through.", error)
        return quarter


def build_command(
    script: Path, quarter: str, input_root: Optional[str], output_root: Optional[str],
    verbose: bool,
) -> List[str]:
    command = [sys.executable, str(script), "--quarter", quarter]
    if input_root:
        command += ["--input-root", input_root]
    if output_root:
        command += ["--output-root", output_root]
    if verbose:
        command.append("--verbose")
    return command


def run_phase(name: str, command: Sequence[str]) -> int:
    """Run one phase, streaming its output straight through to the console."""
    LOG.info("%s", "=" * 72)
    LOG.info("%s", name)
    LOG.info("%s", "=" * 72)
    LOG.debug("Command: %s", " ".join(command))
    completed = subprocess.run(list(command), check=False)
    return completed.returncode


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_battle_book_data.py",
        description=(
            "Build every Battle Book Agent data file for a quarter by running "
            "the quarterly JSON builder and then the attribution builder."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python build_battle_book_data.py --quarter "2026 Q2"\n'
            "  python build_battle_book_data.py --quarter latest\n"
        ),
    )
    parser.add_argument(
        "--quarter",
        default="latest",
        help='Quarter to build, e.g. "2026 Q2", or "latest" (default: latest).',
    )
    parser.add_argument(
        "--input-root",
        default=None,
        help="Override the Quarterly Battle Book Inputs root (passed to both phases).",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Override the Quarterly Battle Book Data root (passed to both phases).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    script_dir = Path(__file__).resolve().parent
    phase1 = script_dir / PHASE1_SCRIPT
    phase2 = script_dir / PHASE2_SCRIPT

    missing = [p.name for p in (phase1, phase2) if not p.is_file()]
    if missing:
        LOG.error(
            "Missing phase script(s) in %s: %s\n"
            "       All three build scripts must sit in the same folder.",
            script_dir,
            ", ".join(missing),
        )
        return 2

    quarter = resolve_quarter_label(args.quarter, args.input_root)

    code = run_phase(
        "PHASE 1 - quarterly performance and market data",
        build_command(phase1, quarter, args.input_root, args.output_root, args.verbose),
    )
    if code != 0:
        LOG.error(
            "Phase 1 failed with exit code %d. Phase 2 was NOT run - it reads and "
            "patches Phase 1 output, so running it now would produce incomplete "
            "or stale data.",
            code,
        )
        return 1

    code = run_phase(
        "PHASE 2 - manager attribution",
        build_command(phase2, quarter, args.input_root, args.output_root, args.verbose),
    )
    if code != 0:
        LOG.error("Phase 2 failed with exit code %d.", code)
        return 1

    LOG.info("%s", "=" * 72)
    LOG.info("Battle book data build complete for %s.", quarter)
    LOG.info(
        "Review debug_layout_report.json and debug_attribution_report.json "
        "before publishing."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
