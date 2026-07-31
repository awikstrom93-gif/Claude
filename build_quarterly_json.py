#!/usr/bin/env python3
"""
build_quarterly_json.py
=======================

V1 quarterly JSON builder for the Battle Book Commentary Agent.

Converts reusable Morningstar Direct quarterly Excel exports into clean,
Copilot-Studio-friendly JSON files.

Usage
-----
    python build_quarterly_json.py --quarter "2026 Q2"
    python build_quarterly_json.py --quarter latest
    python build_quarterly_json.py --quarter latest --list-quarters
    python build_quarterly_json.py --quarter "2026 Q2" \
        --input-root "D:\\test\\in" --output-root "D:\\test\\out"

Inputs (one quarter folder)
---------------------------
    US Large Growth.xlsx
    US Mid Growth.xlsx
    US Small Growth.xlsx
    US Growth Sector Industry Factor.xlsx

Outputs
-------
    <output-root>/<quarter>/large_growth.json
    <output-root>/<quarter>/mid_growth.json
    <output-root>/<quarter>/small_growth.json
    <output-root>/<quarter>/manifest.json
    <output-root>/<quarter>/debug_layout_report.json

Design notes
------------
* Nothing about the sheet layout is hardcoded by cell address. Header rows,
  identity columns, period blocks and metric columns are all *discovered*
  by scanning with openpyxl (data_only=True). This matters because the
  Morningstar exports are NOT identically shaped:
      - US Large Growth / US Mid Growth : 3-column period blocks,
        column B is "Calculation Benchmark".
      - US Small Growth                 : 4-column period blocks,
        NO "Calculation Benchmark" column, and multiple group sections
        ("Recommended", "US Small Growth") each with their own
        "Benchmark 1:" and "Peer Group ..." rows.
* The correct period block is selected by matching the block's start and
  end dates against the requested calendar quarter, not by matching the
  label text ("Last Quarter"), because which label holds the completed
  quarter depends on the export date.
* Optimised for retrieval quality by a Copilot Studio agent, not for BI.
  Each asset-class file is self-contained: managers + market context +
  summaries, so the agent only ever needs to open one file.

Author: senior data engineering, V1.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

try:
    import openpyxl
    from openpyxl.utils import get_column_letter
    from openpyxl.worksheet.worksheet import Worksheet
except ImportError:  # pragma: no cover - guidance for a fresh machine
    sys.stderr.write(
        "ERROR: openpyxl is not installed.\n"
        "       Run:  pip install -r requirements.txt\n"
    )
    raise


# =============================================================================
# SECTION 1 - CONSTANTS / DISCOVERED LAYOUT ASSUMPTIONS
# -----------------------------------------------------------------------------
# Everything in this section was derived by inspecting real Morningstar Direct
# exports. If Morningstar changes an export template, this is the ONLY section
# that should need editing.
# =============================================================================

SCRIPT_VERSION = "1.0.0"

# --- Default OneDrive roots ---------------------------------------------------
DEFAULT_INPUT_ROOT = (
    r"C:\Users\alex.wikstrom\OneDrive - NFP Corp\Research\Battle Book Agent"
    r"\Quarterly Battle Book Inputs"
)
DEFAULT_OUTPUT_ROOT = (
    r"C:\Users\alex.wikstrom\OneDrive - NFP Corp\Research\Battle Book Agent"
    r"\Quarterly Battle Book Data"
)

# --- Quarter folder naming ----------------------------------------------------
# Folders are named like "2026 Q2". Also tolerate "2026Q2" and "Q2 2026".
QUARTER_FOLDER_PATTERNS = (
    re.compile(r"^\s*(?P<year>\d{4})\s*[-_ ]?\s*[Qq](?P<quarter>[1-4])\s*$"),
    re.compile(r"^\s*[Qq](?P<quarter>[1-4])\s*[-_ ]?\s*(?P<year>\d{4})\s*$"),
)
QUARTER_FOLDER_TEMPLATE = "{year} Q{quarter}"

# --- Expected input workbooks -------------------------------------------------
# Matching is tolerant: filenames are normalised to lowercase alphanumerics
# before comparison, so "US_Large_Growth.xlsx" and "US Large Growth.xlsx" both
# resolve. Every token in `tokens` must appear in the normalised filename.
def strip_peer_universe(payload: Dict[str, Any]) -> None:
    """
    Remove every percentile-derived field from an asset-class document.

    Used where Morningstar's peer group is too thin to rank against. It still
    returns numbers - the SMID universe holds six funds - and a number that is
    present but meaningless is the worst case: the commentary agent has no way
    to tell it apart from a real one and will quote it. Deleting the fields is
    the only reliable answer, and it matches how the pack handles every other
    figure the commentary must not use.

    Peer-relative context for these managers comes from eVestment by hand.
    """
    for manager in payload.get("managers", []):
        manager.pop("peer_percentile", None)
        manager.pop("ranking_trend", None)
        for period in (manager.get("performance_periods") or {}).values():
            if isinstance(period, dict):
                period.pop("peer_percentile", None)
    for index in payload.get("reference_indexes", []):
        index.pop("peer_percentile", None)
    payload["peer_group_stats"] = {}


ASSET_CLASSES: Tuple[Dict[str, Any], ...] = (
    {
        "key": "large_growth",
        "asset_class": "US Large Growth",
        "output_file": "large_growth.json",
        "expected_file": "US Large Growth.xlsx",
        "tokens": ("us", "large", "growth"),
        "exclude_tokens": ("sector", "industry", "factor", "mid", "small"),
        "default_benchmark": "Russell 1000 Growth TR USD",
    },
    {
        "key": "mid_growth",
        "asset_class": "US Mid Growth",
        "output_file": "mid_growth.json",
        "expected_file": "US Mid Growth.xlsx",
        # "smid" is a separate word, not a variant of "mid": whole-word matching
        # in filename_words is what keeps US SMID Growth.xlsx out of here.
        "tokens": ("us", "mid", "growth"),
        "exclude_tokens": ("sector", "industry", "factor"),
        "default_benchmark": "Russell Mid Cap Growth TR USD",
    },
    {
        "key": "small_growth",
        "asset_class": "US Small Growth",
        "output_file": "small_growth.json",
        "expected_file": "US Small Growth.xlsx",
        "tokens": ("us", "small", "growth"),
        "exclude_tokens": ("sector", "industry", "factor"),
        "default_benchmark": "Russell 2000 Growth TR USD",
    },
    {
        "key": "smid_growth",
        "asset_class": "US SMID Growth",
        "output_file": "smid_growth.json",
        "expected_file": "US SMID Growth.xlsx",
        "tokens": ("us", "smid", "growth"),
        "exclude_tokens": ("sector", "industry", "factor"),
        "default_benchmark": "Russell 2500 Growth TR USD",
        # Morningstar populates peer percentiles for this universe but it is too
        # thin to rank against, and a plausible-looking number is worse than no
        # number: every percentile-derived field is stripped from the output.
        "peer_universe_reliable": False,
    },
)

MARKET_WORKBOOK = {
    "expected_file": "US Growth Sector Industry Factor.xlsx",
    "tokens": ("growth", "sector", "industry", "factor"),
    "exclude_tokens": (),
}

# --- Header / anchor detection ------------------------------------------------
# The manager grid header row is the row whose first column equals this value.
HEADER_ANCHOR_LABEL = "Group/Investment"
# How far down to scan before giving up on finding the header row.
HEADER_SEARCH_MAX_ROW = 40
# The period label row / start-date row / end-date row sit just above the
# header row. Offsets are relative to the header row and are *validated*
# (the start/end rows must contain parsable dates); if validation fails the
# parser searches upward within this window.
PERIOD_ROW_SEARCH_WINDOW = 6

# --- Identity columns (matched by header text, case/whitespace insensitive) ---
IDENTITY_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "name": ("group/investment", "group / investment", "investment", "name"),
    "calculation_benchmark": ("calculation benchmark",),
    "ticker": ("ticker",),
    "strategy_name": ("strategy name",),
    "portfolio_managers": ("manager name",),
}

# --- Metric columns inside a period block (matched by header text) ------------
METRIC_COLUMN_ALIASES: Dict[str, Tuple[str, ...]] = {
    "return_cumulative": ("return (cumulative)", "return (annualized)", "return"),
    "peer_percentile": ("peer group percentile", "peer percentile"),
    "excess_return_cumulative": (
        "excess return (cumulative)",
        "excess return (annualized)",
        "excess return",
    ),
}

# --- Logical periods ----------------------------------------------------------
# The workbook exposes ~26 period blocks. These are the ones the commentary
# agent needs. Each is resolved against the *selected quarter* by date first
# and by label only as a fallback, so the mapping stays correct no matter which
# block the selected quarter landed in.
#
#   kind="selected"        the already-selected quarter block
#   kind="quarter_offset"  N calendar quarters before the selected quarter
#   kind="ytd"             1 Jan of the selected quarter's year -> quarter end
#   kind="trailing"        N years back from the selected quarter end
#
# NOTE ON NAMING: `two_quarters_ago` follows Morningstar's own labelling, which
# counts back from the *current* quarter. With the selected quarter being
# "Last Quarter", `two_quarters_ago` is the quarter immediately BEFORE the
# selected quarter. Every period record carries explicit start/end dates so the
# agent never has to infer this.
LOGICAL_PERIOD_DEFINITIONS: Tuple[Dict[str, Any], ...] = (
    {"key": "selected_quarter", "kind": "selected", "labels": ("last quarter",)},
    {
        "key": "ytd",
        "kind": "ytd",
        "labels": ("ytd thru last q end", "ytd thru last quarter end", "ytd"),
    },
    {"key": "two_quarters_ago", "kind": "quarter_offset", "offset": 1,
     "labels": ("2 quarters ago",)},
    {"key": "three_quarters_ago", "kind": "quarter_offset", "offset": 2,
     "labels": ("3 quarters ago",)},
    {"key": "four_quarters_ago", "kind": "quarter_offset", "offset": 3,
     "labels": ("4 quarters ago",)},
    {"key": "trailing_1_year", "kind": "trailing", "years": 1,
     "labels": ("1 year", "1 yr", "1 years")},
    {"key": "trailing_3_year", "kind": "trailing", "years": 3,
     "labels": ("3 years", "3 year", "3 yrs")},
    {"key": "trailing_5_year", "kind": "trailing", "years": 5,
     "labels": ("5 years", "5 year", "5 yrs")},
    {"key": "trailing_10_year", "kind": "trailing", "years": 10,
     "labels": ("10 years", "10 year", "10 yrs")},
)

# Periods embedded in the asset-class `market_data_periods` object. The market
# workbook carries fewer blocks than the manager workbooks, so this is a subset.
MARKET_LOGICAL_PERIODS: Tuple[str, ...] = ("selected_quarter", "ytd", "trailing_1_year")

# Quarter chain used for streak and trend maths, NEWEST FIRST.
QUARTER_TREND_SEQUENCE: Tuple[str, ...] = (
    "selected_quarter",
    "two_quarters_ago",
    "three_quarters_ago",
    "four_quarters_ago",
)
# Minimum quarters with excess-return data before a trend direction is claimed.
TREND_MIN_QUARTERS = 3
# Percentage-point move in average excess return needed to call a non-monotonic
# sequence "improving" or "deteriorating" rather than "mixed".
TREND_DIRECTION_THRESHOLD = 0.5

# Periods reported in `ranking_trend`.
RANKING_TREND_PERIODS: Tuple[str, ...] = (
    "selected_quarter",
    "ytd",
    "trailing_1_year",
    "trailing_3_year",
    "trailing_5_year",
    "trailing_10_year",
)

# Periods that get an `underperformed_*` flag in `performance_trends`.
UNDERPERFORMANCE_FLAG_PERIODS: Tuple[str, ...] = (
    "selected_quarter",
    "ytd",
    "trailing_1_year",
    "trailing_3_year",
    "trailing_5_year",
    "trailing_10_year",
)

# Morningstar reports 3/5/10-year blocks annualized and shorter blocks
# cumulatively. Detected from the return column's header text.
BASIS_ANNUALIZED = "annualized"
BASIS_CUMULATIVE = "cumulative"

# --- Row classification -------------------------------------------------------
BENCHMARK_ROW_RE = re.compile(r"^\s*benchmark\s*\d*\s*:\s*", re.IGNORECASE)
PEER_STAT_ROW_RE = re.compile(r"^\s*peer\s+group\s+", re.IGNORECASE)

# --- Market workbook: section -> asset-class mapping -------------------------
# Sections are listed in PREFERENCE ORDER. The first section present in the
# workbook wins for that (asset class, category) pair; remaining sections are
# still recorded in the debug report so nothing is silently lost.
#
# Discovered sections in "US Growth Sector Industry Factor.xlsx":
#   Large Cap Factors / Russell 1000 Growth Sectors / S&P 500 Sectors /
#   S&P 500 Industry Group / S&P 500 Industry / S&P 500 Sub Industry /
#   S&P MidCap 400 Factors / Russell Midcap Sectors /
#   Russell Midcap Growth Sectors / S&P MidCap 400 Sectors /
#   S&P MidCap 400 Sub-Industries / Small Cap Factors / Russell 2000 Sectors /
#   S&P 600 Sectors / Russell 2000 Growth Sectors
MARKET_SECTION_PREFERENCES: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "large_growth": {
        "factors": ("Large Cap Factors",),
        "sectors": ("Russell 1000 Growth Sectors", "S&P 500 Sectors"),
        "industries": (
            "S&P 500 Industry Group",
            "S&P 500 Industry",
            "S&P 500 Sub Industry",
        ),
    },
    "mid_growth": {
        # The export carries no mid-cap *growth* factor block; the S&P MidCap
        # 400 factor block is the closest available mid-cap proxy.
        "factors": ("S&P MidCap 400 Factors",),
        "sectors": (
            "Russell Midcap Growth Sectors",
            "Russell Midcap Sectors",
            "S&P MidCap 400 Sectors",
        ),
        "industries": ("S&P MidCap 400 Sub-Industries",),
    },
    "small_growth": {
        # No small-cap *growth* factor block exists; use the small-cap block.
        "factors": ("Small Cap Factors",),
        "sectors": (
            "Russell 2000 Growth Sectors",
            "Russell 2000 Sectors",
            "S&P 600 Sectors",
        ),
        # No dedicated small-cap industry section exists in this export. The
        # small-cap sector sections are scanned instead; only their row-level
        # "Sub/..." series are harvested as industries because harvesting is
        # scoped to the category a section was selected for.
        "industries": ("Russell 2000 Sectors", "S&P 600 Sectors"),
    },
    "smid_growth": {
        # The export carries no Russell 2500 factor or industry block. Sectors
        # are genuinely SMID; factors and industries are borrowed from the mid-
        # and small-cap blocks either side of the range, and every series keeps
        # its `section` so the commentary can say which universe it came from.
        "factors": ("S&P MidCap 400 Factors", "Small Cap Factors"),
        "sectors": ("Russell 2500 Growth Sectors", "Russell 2500 Sectors"),
        "industries": (
            "S&P MidCap 400 Sub-Industries",
            "Russell 2000 Sectors",
            "S&P 600 Sectors",
        ),
    },
}

# How many preferred sections to harvest per category. 1 = strictly the first
# available section (the default, so sector granularity is never blended).
# Override per (asset class, category) where merging is the right call.
MARKET_SECTIONS_PER_CATEGORY = 1
MARKET_SECTIONS_PER_CATEGORY_OVERRIDES: Dict[Tuple[str, str], int] = {
    # Small-cap industry coverage is sparse; sweep both small-cap sections.
    ("small_growth", "industries"): 2,
    # SMID has no factor or industry block of its own, so both flanking
    # universes are swept rather than one arbitrarily winning.
    ("smid_growth", "factors"): 2,
    ("smid_growth", "industries"): 3,
}

# Section-title -> category classification, evaluated in order.
MARKET_CATEGORY_RULES: Tuple[Tuple[str, str], ...] = (
    ("factor", "factors"),
    ("sub-industr", "industries"),
    ("sub industr", "industries"),
    ("industr", "industries"),
    ("sector", "sectors"),
)

# Row-level override: rows named "... Sub/Something" or "... Ig/Something" are
# industry-level series even when they appear inside a sector section. The second
# pattern catches industry series that carry no slash prefix, such as
# "S&P Biotechnology Select Industry TR USD", which would otherwise be dropped.
# It is applied to member rows only, so section titles like "S&P 500 Industry
# Group" are unaffected.
MARKET_ROW_INDUSTRY_RE = re.compile(r"\b(sub|ig)/|\bindustry\b", re.IGNORECASE)

# --- Display-name cleanup for market series ----------------------------------
# Morningstar index names are terse ("Russell 1000 Growth Ind/Tech TR USD").
# A readable display_name materially improves commentary quality.
MARKET_NAME_SUFFIXES = (
    " TR USD", " PR USD", " TRUSD", " TR", " PR", " USD",
)
MARKET_NAME_EXPANSIONS: Dict[str, str] = {
    "tech": "Technology",
    "hc": "Health Care",
    "cons dis": "Consumer Discretionary",
    "consdis": "Consumer Discretionary",
    "cons disc": "Consumer Discretionary",
    "cons stp": "Consumer Staples",
    "consstp": "Consumer Staples",
    "cons staples": "Consumer Staples",
    "fncls": "Financials",
    "bsc mtrls": "Basic Materials",
    "indstrls": "Industrials",
    "info technology": "Information Technology",
    "information technology": "Information Technology",
    "commun services": "Communication Services",
    "div telecom svcs": "Diversified Telecom Services",
    "semicon&equip": "Semiconductors & Equipment",
    "semicon&semicon equip": "Semiconductors & Semiconductor Equipment",
    "software&svcs": "Software & Services",
    "tech hardware&equip": "Technology Hardware & Equipment",
    "pharm&biotech": "Pharmaceuticals & Biotechnology",
    "health care pvdrs&svcs": "Health Care Providers & Services",
    "health care providers&svcs": "Health Care Providers & Services",
    "health care equip&supl": "Health Care Equipment & Supplies",
    "capital market": "Capital Markets",
    "div financial svcs": "Diversified Financial Services",
    "food beverage&tobacco": "Food, Beverage & Tobacco",
    "food&staples retailing": "Food & Staples Retailing",
    "household&personal prods": "Household & Personal Products",
    "consumer durables&apparel": "Consumer Durables & Apparel",
    "commercial & profe service": "Commercial & Professional Services",
    "commercial svcs&supplies": "Commercial Services & Supplies",
    "trading coms&distributors": "Trading Companies & Distributors",
    "electrical equip": "Electrical Equipment",
    "const&engineering": "Construction & Engineering",
    "aerospace&defence": "Aerospace & Defense",
    "aerospace&defense": "Aerospace & Defense",
    "life sciences tool & svcs": "Life Sciences Tools & Services",
    "air freight&logistics": "Air Freight & Logistics",
    "road&rail": "Road & Rail",
    "oil&gas": "Oil & Gas",
    "energy equip&svcs": "Energy Equipment & Services",
    "hotels restaurants&leis": "Hotels, Restaurants & Leisure",
    "textiles&apparel": "Textiles & Apparel",
    "it services": "IT Services",
}

# Placeholder used to protect brand ampersands (S&P) while separator
# ampersands ("Semicon&Equip") are spaced out.
AMPERSAND_GUARD = "\x00SANDP\x00"

# Token-level expansion applied to anything the whole-name map above misses.
# Morningstar truncates aggressively ("Electl Compnts&Eq"); expanding token by
# token keeps the long tail of sub-industry names readable in commentary.
MARKET_TOKEN_EXPANSIONS: Dict[str, str] = {
    "aerosp": "Aerospace", "agrcl": "Agricultural", "agri": "Agricultural",
    "bkg": "Banking", "bks": "Banks", "broker": "Brokerage", "bsc": "Basic",
    "chems": "Chemicals", "cnst": "Construction", "commun": "Communication",
    "compnts": "Components", "const": "Construction", "cons": "Consumer",
    "csdy": "Custody", "dis": "Discretionary", "disc": "Discretionary",
    "distrbtr": "Distributors", "div": "Diversified", "dvlpmt": "Development",
    "electl": "Electrical", "electrnc": "Electronic", "elctrnc": "Electronic",
    "eelctrnc": "Electronic", "eng": "Engineering", "entrtmnt": "Entertainment",
    "eq": "Equipment", "equip": "Equipment", "explor": "Exploration",
    "fctr": "Factor", "fertlz": "Fertilizers", "fertlzrs": "Fertilizers",
    "fncls": "Financials", "hc": "Health Care", "indp": "Independent",
    "indstrls": "Industrials", "instr": "Instruments", "insucr": "Insurance",
    "invstmnt": "Investment", "leis": "Leisure", "machry": "Machinery",
    "manfctg": "Manufacturing", "manfufacturers": "Manufacturers",
    "mgmt": "Management", "mngm": "Management", "mktg": "Marketing",
    "mtrls": "Materials", "oth": "Other", "outsc": "Outsourcing",
    "pkgd": "Packaged", "prdocsg": "Processing", "procsg": "Processing",
    "prodtn": "Production", "prods": "Products", "pvdrs": "Providers",
    "pwr": "Power", "rfg": "Refining", "rnw": "Renewable", "sto": "Storage",
    "stp": "Staples", "supl": "Supplies", "svcs": "Services", "swre": "Software",
    "tech": "Technology", "transpt": "Transport", "trdg": "Trading",
    "trks": "Trucks", "unreg": "Unregulated", "utils": "Utilities",
}

# --- Summary sizes ------------------------------------------------------------
SUMMARY_TOP_N = 10

# --- Output shaping -----------------------------------------------------------
ROUND_DECIMALS = 4
# Also index managers by ticker and strategy name (never overwriting a real
# manager-name key). Improves Copilot Studio hit rate on paraphrased asks.
INCLUDE_TICKER_AND_STRATEGY_IN_LOOKUP = True

LOG = logging.getLogger("build_quarterly_json")


# =============================================================================
# SECTION 2 - SMALL UTILITIES
# =============================================================================


def normalize_header(value: Any) -> str:
    """Normalise a header cell for alias matching."""
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def normalize_lookup_name(value: Any) -> str:
    """
    Normalise a manager name for `manager_lookup` keys.

    Rules (per spec): lowercase, trim, collapse duplicate spaces, and remove
    periods (for lookup only - the stored `manager` value keeps its periods).
    """
    if value is None:
        return ""
    text = str(value).replace("\n", " ").replace("\r", " ")
    text = text.replace(".", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def normalize_filename(name: str) -> str:
    """Lowercase alphanumeric-only form of a filename stem, for tolerant matching."""
    return re.sub(r"[^a-z0-9]+", "", name.lower())


def filename_words(name: str) -> Set[str]:
    """
    A filename stem split into words, for whole-word token matching.

    normalize_filename strips separators entirely, which is tolerant but cannot
    tell "US Mid Growth" from "US SMID Growth": both collapse to a form
    containing "mid" AND "smid", so each file satisfies the other's tokens and
    the SMID workbook can be written out as mid_growth.json - or the reverse.
    No choice of substring token separates them; the word boundary is the only
    thing that does. Separators and camelCase both split, so US_SMID_Growth,
    "US SMID Growth" and USSmidGrowth all resolve.
    """
    spaced = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", spaced)
    return set(re.sub(r"[^a-z0-9]+", " ", spaced.lower()).split())


def to_float(value: Any) -> Optional[float]:
    """Coerce a cell value to float, returning None for blanks and junk."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return None if result != result else result  # drop NaN
    text = str(value).strip()
    if not text or text in {"-", "--", "n/a", "N/A", "NA", "nan"}:
        return None
    text = text.replace(",", "").replace("%", "")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    try:
        result = float(text)
    except ValueError:
        return None
    return -result if negative else result


def to_int(value: Any) -> Optional[int]:
    """Coerce a cell value to int (used for peer percentile)."""
    number = to_float(value)
    if number is None:
        return None
    return int(round(number))


def round_or_none(value: Optional[float], digits: int = ROUND_DECIMALS) -> Optional[float]:
    return None if value is None else round(value, digits)


def to_date(value: Any) -> Optional[date]:
    """Parse a Morningstar header date cell (datetime or US-format string)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d/%m/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def cell_text(worksheet: Worksheet, row: int, column: int) -> Optional[str]:
    """Return a stripped string cell value, or None when blank."""
    value = worksheet.cell(row=row, column=column).value
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def shift_years(day: date, years: int) -> date:
    """Move a date back N years, tolerating 29 February."""
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb -> 28 Feb
        return day.replace(year=day.year - years, day=28)


def iso_or_empty(day: Optional[date]) -> str:
    return day.isoformat() if day else ""


# =============================================================================
# SECTION 3 - QUARTER RESOLUTION
# =============================================================================


@dataclass(frozen=True)
class Quarter:
    """A calendar quarter plus its folder label and date bounds."""

    year: int
    quarter: int

    @property
    def label(self) -> str:
        return QUARTER_FOLDER_TEMPLATE.format(year=self.year, quarter=self.quarter)

    @property
    def start_date(self) -> date:
        return date(self.year, 3 * (self.quarter - 1) + 1, 1)

    @property
    def end_date(self) -> date:
        month = 3 * self.quarter
        last_day = {3: 31, 6: 30, 9: 30, 12: 31}[month]
        return date(self.year, month, last_day)

    def contains(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date

    def shifted(self, quarters_back: int) -> "Quarter":
        """Return the quarter `quarters_back` calendar quarters earlier."""
        absolute = self.year * 4 + (self.quarter - 1) - quarters_back
        return Quarter(absolute // 4, absolute % 4 + 1)

    def __str__(self) -> str:  # pragma: no cover - convenience
        return self.label


def parse_quarter_token(token: str) -> Optional[Quarter]:
    """Parse '2026 Q2' / '2026Q2' / 'Q2 2026' into a Quarter."""
    for pattern in QUARTER_FOLDER_PATTERNS:
        match = pattern.match(token)
        if match:
            return Quarter(int(match.group("year")), int(match.group("quarter")))
    return None


def discover_quarter_folders(input_root: Path) -> List[Tuple[Quarter, Path]]:
    """Return every quarter folder under the input root, oldest first."""
    if not input_root.is_dir():
        raise FileNotFoundError(
            f"Input root folder does not exist: {input_root}\n"
            f"       Check the --input-root argument or the DEFAULT_INPUT_ROOT constant."
        )
    found: List[Tuple[Quarter, Path]] = []
    for child in sorted(input_root.iterdir()):
        if not child.is_dir():
            continue
        quarter = parse_quarter_token(child.name)
        if quarter is not None:
            found.append((quarter, child))
        else:
            LOG.debug("Skipping non-quarter folder: %s", child.name)
    found.sort(key=lambda item: (item[0].year, item[0].quarter))
    return found


def resolve_quarter(input_root: Path, requested: str) -> Tuple[Quarter, Path]:
    """Resolve the --quarter argument to a (Quarter, folder) pair."""
    available = discover_quarter_folders(input_root)
    if not available:
        raise FileNotFoundError(
            f"No quarter folders found under: {input_root}\n"
            f"       Expected folders named like '2026 Q2'."
        )

    if requested.strip().lower() == "latest":
        quarter, folder = available[-1]
        LOG.info("--quarter latest resolved to '%s'", quarter.label)
        return quarter, folder

    quarter = parse_quarter_token(requested)
    if quarter is None:
        raise ValueError(
            f"Could not understand --quarter {requested!r}.\n"
            f"       Use a value like \"2026 Q2\" or the keyword \"latest\"."
        )

    for candidate, folder in available:
        if candidate == quarter:
            return quarter, folder

    known = ", ".join(q.label for q, _ in available)
    raise FileNotFoundError(
        f"Quarter folder '{quarter.label}' not found under: {input_root}\n"
        f"       Available quarters: {known}"
    )


# =============================================================================
# SECTION 4 - WORKBOOK DISCOVERY
# =============================================================================


def find_workbook(folder: Path, spec: Dict[str, Any]) -> Optional[Path]:
    """
    Locate a workbook in the quarter folder using tolerant token matching.

    Prefers an exact filename match; otherwise the first .xlsx whose normalised
    stem contains every required token and none of the excluded tokens.
    """
    exact = folder / spec["expected_file"]
    if exact.is_file():
        return exact

    tokens = spec["tokens"]
    excluded = spec.get("exclude_tokens", ())
    candidates: List[Path] = []
    for path in sorted(folder.glob("*.xls*")):
        if path.name.startswith("~$"):  # Excel lock file
            continue
        words = filename_words(path.stem)
        if all(token in words for token in tokens) and not any(
            token in words for token in excluded
        ):
            candidates.append(path)

    if not candidates:
        return None
    if len(candidates) > 1:
        LOG.warning(
            "Multiple candidate files matched %s: %s - using %s",
            spec["expected_file"],
            [c.name for c in candidates],
            candidates[0].name,
        )
    return candidates[0]


# =============================================================================
# SECTION 5 - LAYOUT DETECTION (header row, identity columns, period blocks)
# =============================================================================


@dataclass
class PeriodBlock:
    """One wide period block (e.g. 'Last Quarter' -> columns AC:AE)."""

    label: Optional[str]
    first_column: int
    last_column: int
    start_date: Optional[date]
    end_date: Optional[date]
    metric_columns: Dict[str, int] = field(default_factory=dict)
    # Morningstar reports 3/5/10-year blocks annualized and shorter blocks
    # cumulatively; taken from the return column's own header text.
    basis: str = BASIS_CUMULATIVE
    return_header: str = ""

    @property
    def column_range(self) -> str:
        return f"{get_column_letter(self.first_column)}:{get_column_letter(self.last_column)}"

    def describe(self) -> Dict[str, Any]:
        return {
            "label": self.label,
            "columns": self.column_range,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "basis": self.basis,
            "return_header": self.return_header,
            "metric_columns": {
                name: get_column_letter(col) for name, col in sorted(
                    self.metric_columns.items(), key=lambda kv: kv[1]
                )
            },
        }


@dataclass
class SheetLayout:
    """Everything the parsers need to know about a worksheet's shape."""

    worksheet_title: str
    header_row: int
    label_row: Optional[int]
    start_date_row: Optional[int]
    end_date_row: Optional[int]
    identity_columns: Dict[str, int]
    first_metric_column: int
    period_blocks: List[PeriodBlock]
    max_row: int
    max_column: int

    def describe(self) -> Dict[str, Any]:
        return {
            "worksheet": self.worksheet_title,
            "header_row": self.header_row,
            "period_label_row": self.label_row,
            "period_start_date_row": self.start_date_row,
            "period_end_date_row": self.end_date_row,
            "identity_columns": {
                name: get_column_letter(col)
                for name, col in sorted(self.identity_columns.items(), key=lambda kv: kv[1])
            },
            "first_metric_column": get_column_letter(self.first_metric_column),
            "row_count": self.max_row,
            "column_count": self.max_column,
            "period_block_count": len(self.period_blocks),
            "period_blocks": [block.describe() for block in self.period_blocks],
        }


def find_header_row(worksheet: Worksheet) -> int:
    """Find the manager grid header row by its first-column anchor label."""
    anchor = normalize_header(HEADER_ANCHOR_LABEL)
    limit = min(worksheet.max_row, HEADER_SEARCH_MAX_ROW)
    for row in range(1, limit + 1):
        if normalize_header(worksheet.cell(row=row, column=1).value) == anchor:
            return row
    # Fallback: any row whose first cell is non-empty and that also contains a
    # recognisable metric header somewhere to the right.
    metric_aliases = {
        alias for aliases in METRIC_COLUMN_ALIASES.values() for alias in aliases
    }
    for row in range(1, limit + 1):
        if worksheet.cell(row=row, column=1).value is None:
            continue
        for column in range(2, min(worksheet.max_column, 60) + 1):
            if normalize_header(worksheet.cell(row=row, column=column).value) in metric_aliases:
                return row
    raise ValueError(
        f"Could not locate the header row (expected {HEADER_ANCHOR_LABEL!r} in column A) "
        f"in worksheet {worksheet.title!r}. The export template may have changed."
    )


def _row_date_score(worksheet: Worksheet, row: int, max_column: int) -> int:
    """How many parsable dates a row contains (used to find the date rows)."""
    score = 0
    for column in range(1, min(max_column, 200) + 1):
        if to_date(worksheet.cell(row=row, column=column).value) is not None:
            score += 1
    return score


def find_period_rows(
    worksheet: Worksheet, header_row: int
) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """
    Locate the (label_row, start_date_row, end_date_row) above the header row.

    The expected layout is header-3 / header-2 / header-1, but this validates by
    counting parsable dates rather than trusting the offsets.
    """
    lowest = max(1, header_row - PERIOD_ROW_SEARCH_WINDOW)
    scored = [
        (row, _row_date_score(worksheet, row, worksheet.max_column))
        for row in range(lowest, header_row)
    ]
    date_rows = [row for row, score in scored if score >= 3]
    if len(date_rows) < 2:
        LOG.warning(
            "Worksheet %r: could not find two date rows above header row %d; "
            "period blocks will be detected from labels only.",
            worksheet.title,
            header_row,
        )
        label_row = header_row - 1 if header_row > 1 else None
        return label_row, None, None

    start_date_row, end_date_row = date_rows[-2], date_rows[-1]
    label_row = start_date_row - 1 if start_date_row - 1 >= 1 else None
    return label_row, start_date_row, end_date_row


def find_identity_columns(worksheet: Worksheet, header_row: int) -> Dict[str, int]:
    """Map logical identity fields to column indexes using header text."""
    found: Dict[str, int] = {}
    scan_limit = min(worksheet.max_column, 60)
    for column in range(1, scan_limit + 1):
        header = normalize_header(worksheet.cell(row=header_row, column=column).value)
        if not header:
            continue
        for field_name, aliases in IDENTITY_COLUMN_ALIASES.items():
            if field_name in found:
                continue
            if header in aliases:
                found[field_name] = column
                break
    if "name" not in found:
        found["name"] = 1  # column A is always the investment name in these exports
    return found


def _classify_metric(header: str) -> Optional[str]:
    """Map a header string to a logical metric name."""
    for metric, aliases in METRIC_COLUMN_ALIASES.items():
        if header in aliases:
            return metric
    return None


def detect_period_blocks(
    worksheet: Worksheet,
    header_row: int,
    label_row: Optional[int],
    start_date_row: Optional[int],
    end_date_row: Optional[int],
    first_metric_column: int,
) -> List[PeriodBlock]:
    """
    Discover every wide period block on the sheet.

    A block starts at any column with a value in the start-date row (that row
    is populated for *all* blocks, including the unlabelled trailing 'as of'
    blocks) and runs until the column before the next block start.
    """
    max_column = worksheet.max_column
    boundaries: List[int] = []

    if start_date_row is not None:
        for column in range(first_metric_column, max_column + 1):
            if worksheet.cell(row=start_date_row, column=column).value is not None:
                boundaries.append(column)

    if not boundaries and label_row is not None:
        for column in range(first_metric_column, max_column + 1):
            if worksheet.cell(row=label_row, column=column).value is not None:
                boundaries.append(column)

    if not boundaries:
        # Last resort: treat every run of metric headers as one block.
        boundaries = [first_metric_column]

    blocks: List[PeriodBlock] = []
    for index, first_column in enumerate(boundaries):
        last_column = (
            boundaries[index + 1] - 1 if index + 1 < len(boundaries) else max_column
        )
        label = (
            cell_text(worksheet, label_row, first_column) if label_row else None
        )
        start_date = (
            to_date(worksheet.cell(row=start_date_row, column=first_column).value)
            if start_date_row
            else None
        )
        end_date = (
            to_date(worksheet.cell(row=end_date_row, column=first_column).value)
            if end_date_row
            else None
        )

        metric_columns: Dict[str, int] = {}
        return_header = ""
        for column in range(first_column, last_column + 1):
            header = normalize_header(worksheet.cell(row=header_row, column=column).value)
            if not header:
                continue
            metric = _classify_metric(header)
            if metric and metric not in metric_columns:
                metric_columns[metric] = column
                if metric == "return_cumulative":
                    return_header = header

        basis = BASIS_ANNUALIZED if "annualized" in return_header else BASIS_CUMULATIVE

        blocks.append(
            PeriodBlock(
                label=label,
                first_column=first_column,
                last_column=last_column,
                start_date=start_date,
                end_date=end_date,
                metric_columns=metric_columns,
                basis=basis,
                return_header=return_header,
            )
        )
    return blocks


def build_sheet_layout(worksheet: Worksheet) -> SheetLayout:
    """Run full layout discovery for one worksheet."""
    header_row = find_header_row(worksheet)
    label_row, start_date_row, end_date_row = find_period_rows(worksheet, header_row)
    identity_columns = find_identity_columns(worksheet, header_row)
    first_metric_column = max(identity_columns.values()) + 1 if identity_columns else 2

    period_blocks = detect_period_blocks(
        worksheet,
        header_row,
        label_row,
        start_date_row,
        end_date_row,
        first_metric_column,
    )
    return SheetLayout(
        worksheet_title=worksheet.title,
        header_row=header_row,
        label_row=label_row,
        start_date_row=start_date_row,
        end_date_row=end_date_row,
        identity_columns=identity_columns,
        first_metric_column=first_metric_column,
        period_blocks=period_blocks,
        max_row=worksheet.max_row,
        max_column=worksheet.max_column,
    )


def select_period_block(
    layout: SheetLayout, quarter: Quarter, warnings: List[str], context: str
) -> PeriodBlock:
    """
    Pick the period block that represents the requested calendar quarter.

    Selection order:
      1. Exact match on both start date and end date (the completed quarter).
      2. Start date matches and end date falls inside the quarter (partial /
         quarter-to-date export) - recorded as a warning.
      3. Label heuristics ('Last Quarter', then 'Current Quarter').
    """
    usable = [b for b in layout.period_blocks if b.metric_columns.get("return_cumulative")]
    if not usable:
        raise ValueError(
            f"{context}: no period block exposes a 'Return (Cumulative)' column. "
            f"The export template may have changed."
        )

    for block in usable:
        if block.start_date == quarter.start_date and block.end_date == quarter.end_date:
            LOG.info(
                "%s: matched period block %r (%s) to %s",
                context,
                block.label or "<unlabelled>",
                block.column_range,
                quarter.label,
            )
            return block

    # Partial / quarter-to-date. Several blocks can share the same dates (MTD
    # and 'Current Quarter' coincide early in a quarter), so prefer the one
    # whose label actually names a quarter.
    partial = [
        block
        for block in usable
        if block.start_date == quarter.start_date
        and block.end_date is not None
        and quarter.contains(block.end_date)
    ]
    partial.sort(key=lambda b: 0 if b.label and "quarter" in b.label.lower() else 1)
    if partial:
        block = partial[0]
        message = (
            f"{context}: quarter {quarter.label} is only partially complete in this "
            f"export (block {block.label or '<unlabelled>'} ends {block.end_date}). "
            f"Returns are quarter-to-date, not full-quarter."
        )
        LOG.warning(message)
        warnings.append(message)
        return block

    for wanted in ("last quarter", "current quarter"):
        for block in usable:
            if block.label and block.label.strip().lower() == wanted:
                message = (
                    f"{context}: no period block matched {quarter.label} "
                    f"({quarter.start_date} - {quarter.end_date}); fell back to the "
                    f"{block.label!r} block. Verify the quarter folder holds the right export."
                )
                LOG.warning(message)
                warnings.append(message)
                return block

    available = "; ".join(
        f"{b.label or '<unlabelled>'} [{b.start_date} -> {b.end_date}]" for b in usable
    )
    raise ValueError(
        f"{context}: could not find a period block for {quarter.label} "
        f"({quarter.start_date} - {quarter.end_date}).\n"
        f"       Blocks present: {available}"
    )


def _find_block_by_dates(
    blocks: Sequence[PeriodBlock], start: date, end: date
) -> Optional[PeriodBlock]:
    for block in blocks:
        if block.start_date == start and block.end_date == end:
            return block
    return None


def _find_block_by_label(
    blocks: Sequence[PeriodBlock], labels: Sequence[str]
) -> Optional[PeriodBlock]:
    """Match on label aliases, honouring the alias order as a preference."""
    by_label = {}
    for block in blocks:
        if block.label:
            by_label.setdefault(normalize_header(block.label), block)
    for alias in labels:
        match = by_label.get(alias)
        if match is not None:
            return match
    return None


def resolve_logical_periods(
    layout: SheetLayout,
    quarter: Quarter,
    selected_block: PeriodBlock,
    wanted_keys: Sequence[str],
    warnings: List[str],
    context: str,
) -> Tuple[Dict[str, Optional[PeriodBlock]], Dict[str, Any]]:
    """
    Map each logical period key onto a workbook period block.

    Resolution is by date wherever a date can be derived from the selected
    quarter, falling back to Morningstar's label text. Both the resolved block
    and *how* it was resolved are recorded for the debug report.
    """
    usable = [b for b in layout.period_blocks if b.metric_columns.get("return_cumulative")]
    resolved: Dict[str, Optional[PeriodBlock]] = {}
    report: Dict[str, Any] = {}

    selected_end = selected_block.end_date or quarter.end_date

    for definition in LOGICAL_PERIOD_DEFINITIONS:
        key = definition["key"]
        if key not in wanted_keys:
            continue

        block: Optional[PeriodBlock] = None
        method = "not_found"
        target_start: Optional[date] = None
        target_end: Optional[date] = None

        if definition["kind"] == "selected":
            block, method = selected_block, "selected_quarter_block"
        else:
            if definition["kind"] == "quarter_offset":
                target = quarter.shifted(definition["offset"])
                target_start, target_end = target.start_date, target.end_date
            elif definition["kind"] == "ytd":
                target_start, target_end = date(selected_end.year, 1, 1), selected_end
            elif definition["kind"] == "trailing":
                target_end = selected_end
                # Morningstar trailing windows start the day after the
                # anniversary: 1 YEAR = 2025-07-01 -> 2026-06-30.
                target_start = shift_years(selected_end, definition["years"]) + timedelta(
                    days=1
                )

            if target_start and target_end:
                block = _find_block_by_dates(usable, target_start, target_end)
                if block is not None:
                    method = "matched_by_date"

            if block is None:
                block = _find_block_by_label(usable, definition["labels"])
                if block is not None:
                    method = "matched_by_label"

        if block is None:
            message = (
                f"{context}: no period block found for '{key}' "
                f"(wanted {target_start} -> {target_end}); it will be null."
            )
            LOG.warning(message)
            warnings.append(message)
        elif method == "matched_by_label" and target_end and block.end_date != target_end:
            # A label match that does not cover the expected window is still
            # usable, but the agent must not be told it lines up with the quarter.
            message = (
                f"{context}: '{key}' fell back to the {block.label!r} block, which "
                f"covers {block.start_date} -> {block.end_date} rather than the "
                f"expected {target_start} -> {target_end}."
            )
            LOG.warning(message)
            warnings.append(message)

        resolved[key] = block
        report[key] = {
            "workbook_block": block.label if block else None,
            "columns": block.column_range if block else None,
            "start_date": iso_or_empty(block.start_date) if block else "",
            "end_date": iso_or_empty(block.end_date) if block else "",
            "basis": block.basis if block else None,
            "resolved_by": method,
            "target_start_date": iso_or_empty(target_start),
            "target_end_date": iso_or_empty(target_end),
        }

    return resolved, report


# =============================================================================
# SECTION 6 - ASSET-CLASS (MANAGER) WORKBOOK PARSING
# =============================================================================

ROW_KIND_MANAGER = "manager"
ROW_KIND_BENCHMARK = "benchmark"
ROW_KIND_PEER_STAT = "peer_stat"
ROW_KIND_INDEX = "reference_index"
ROW_KIND_SECTION = "section_header"
ROW_KIND_BLANK = "blank"


def read_block_metrics(
    worksheet: Worksheet, row: int, block: PeriodBlock
) -> Dict[str, Optional[float]]:
    """Read the return / percentile / excess values for one row in one block."""
    values: Dict[str, Optional[float]] = {
        "return_cumulative": None,
        "peer_percentile": None,
        "excess_return_cumulative": None,
    }
    for metric, column in block.metric_columns.items():
        raw = worksheet.cell(row=row, column=column).value
        values[metric] = to_int(raw) if metric == "peer_percentile" else to_float(raw)
    return values


def empty_period_record() -> Dict[str, Any]:
    """A period the workbook did not supply. All values null, never zero."""
    return {
        "label": "",
        "period_start_date": "",
        "period_end_date": "",
        "basis": "",
        "return_cumulative": None,
        "benchmark_return": None,
        "benchmark_return_calculated": False,
        "peer_percentile": None,
        "excess_return_cumulative": None,
        "available": False,
    }


def build_period_record(
    block: Optional[PeriodBlock],
    metrics: Dict[str, Optional[float]],
    benchmark_return: Optional[float],
    benchmark_return_calculated: bool,
) -> Dict[str, Any]:
    """One entry of a manager's `performance_periods` object."""
    if block is None:
        return empty_period_record()

    return_value = metrics.get("return_cumulative")
    return {
        "label": block.label or "",
        "period_start_date": iso_or_empty(block.start_date),
        "period_end_date": iso_or_empty(block.end_date),
        # 'annualized' for the 3/5/10-year blocks, 'cumulative' otherwise.
        # Commentary must not describe an annualized figure as a total return.
        "basis": block.basis,
        "return_cumulative": round_or_none(return_value),
        "benchmark_return": round_or_none(benchmark_return),
        "benchmark_return_calculated": benchmark_return_calculated,
        "peer_percentile": metrics.get("peer_percentile"),
        "excess_return_cumulative": round_or_none(
            metrics.get("excess_return_cumulative")
        ),
        "available": return_value is not None,
    }


def resolve_benchmark_return(
    metrics: Dict[str, Optional[float]],
    benchmark_name: str,
    benchmark_returns: Dict[str, float],
) -> Tuple[Optional[float], bool]:
    """
    Benchmark return for one period.

    Prefers the workbook's own benchmark row; falls back to
    return - excess, flagging the value as calculated.
    """
    explicit = benchmark_returns.get(benchmark_name)
    if explicit is not None:
        return explicit, False

    return_value = metrics.get("return_cumulative")
    excess = metrics.get("excess_return_cumulative")
    if return_value is not None and excess is not None:
        return return_value - excess, True
    return None, False


def compute_performance_trends(
    performance_periods: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Streaks, per-period underperformance flags, and a trend direction.

    Streaks walk the quarter chain newest -> oldest and stop at the first
    quarter that breaks the run or has no data. An excess return of exactly
    zero breaks both streaks.
    """

    def excess_of(key: str) -> Optional[float]:
        return performance_periods.get(key, {}).get("excess_return_cumulative")

    under_streak = 0
    for key in QUARTER_TREND_SEQUENCE:
        value = excess_of(key)
        if value is None or value >= 0:
            break
        under_streak += 1

    over_streak = 0
    for key in QUARTER_TREND_SEQUENCE:
        value = excess_of(key)
        if value is None or value <= 0:
            break
        over_streak += 1

    # Oldest -> newest, only quarters that actually have an excess return.
    chronological = [
        excess_of(key) for key in reversed(QUARTER_TREND_SEQUENCE) if excess_of(key) is not None
    ]

    if len(chronological) < TREND_MIN_QUARTERS:
        trend_direction = "insufficient_data"
    else:
        deltas = [
            later - earlier
            for earlier, later in zip(chronological, chronological[1:])
        ]
        if all(delta > 0 for delta in deltas):
            trend_direction = "improving"
        elif all(delta < 0 for delta in deltas):
            trend_direction = "deteriorating"
        else:
            half = len(chronological) // 2
            earlier_mean = sum(chronological[:half]) / half
            recent_mean = sum(chronological[-half:]) / half
            shift = recent_mean - earlier_mean
            if shift > TREND_DIRECTION_THRESHOLD:
                trend_direction = "improving"
            elif shift < -TREND_DIRECTION_THRESHOLD:
                trend_direction = "deteriorating"
            else:
                trend_direction = "mixed"

    trends: Dict[str, Any] = {
        "consecutive_quarters_underperforming": under_streak,
        "consecutive_quarters_outperforming": over_streak,
    }
    for key in UNDERPERFORMANCE_FLAG_PERIODS:
        value = excess_of(key)
        trends[f"underperformed_{key}"] = value is not None and value < 0
    trends["trend_direction"] = trend_direction
    # An `underperformed_*` flag is False both when the manager beat the
    # benchmark and when the period is missing. This lists what was actually
    # evaluable so the agent can tell the two apart.
    trends["periods_evaluated"] = [
        key
        for key in UNDERPERFORMANCE_FLAG_PERIODS
        if excess_of(key) is not None
    ]
    trends["quarters_available"] = len(chronological)
    return trends


def build_ranking_trend(
    performance_periods: Dict[str, Dict[str, Any]]
) -> Dict[str, Optional[int]]:
    """Peer percentile across the headline periods."""
    return {
        key: performance_periods.get(key, {}).get("peer_percentile")
        for key in RANKING_TREND_PERIODS
    }


def classify_manager_row(
    name: Optional[str], identity: Dict[str, Optional[str]], metrics: Dict[str, Optional[float]]
) -> str:
    """Decide what kind of row this is."""
    if not name:
        return ROW_KIND_BLANK
    if BENCHMARK_ROW_RE.match(name):
        return ROW_KIND_BENCHMARK
    if PEER_STAT_ROW_RE.match(name):
        return ROW_KIND_PEER_STAT
    has_identity = any(
        identity.get(key) for key in ("ticker", "strategy_name", "portfolio_managers")
    )
    if has_identity:
        return ROW_KIND_MANAGER
    has_metrics = any(value is not None for value in metrics.values())
    return ROW_KIND_INDEX if has_metrics else ROW_KIND_SECTION


def split_portfolio_managers(raw: Optional[str]) -> List[str]:
    """Morningstar packs PM names as 'A;B;C;' - split into a clean list."""
    if not raw:
        return []
    return [part.strip() for part in raw.split(";") if part.strip()]


def strip_benchmark_prefix(name: str) -> str:
    return BENCHMARK_ROW_RE.sub("", name).strip()


def parse_asset_class_workbook(
    path: Path,
    spec: Dict[str, Any],
    quarter: Quarter,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Parse one asset-class workbook into managers + supporting context.

    Returns (parsed, debug) where `parsed` holds managers, reference indexes,
    peer group stats and lineage, and `debug` holds the layout report entry.
    """
    LOG.info("Parsing asset-class workbook: %s", path.name)
    warnings: List[str] = []
    skipped_rows: List[Dict[str, Any]] = []

    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        worksheet_names = list(workbook.sheetnames)
        worksheet = workbook[worksheet_names[0]]
        layout = build_sheet_layout(worksheet)
        context = f"{path.name} [{worksheet.title}]"
        block = select_period_block(layout, quarter, warnings, context)

        # Every logical period the commentary agent can reason over. The
        # selected quarter remains the primary period; the rest add history.
        period_keys = [definition["key"] for definition in LOGICAL_PERIOD_DEFINITIONS]
        logical_blocks, logical_period_map = resolve_logical_periods(
            layout, quarter, block, period_keys, warnings, context
        )

        missing_metrics = [
            metric for metric in METRIC_COLUMN_ALIASES if metric not in block.metric_columns
        ]
        if missing_metrics:
            message = (
                f"{context}: period block {block.label or '<unlabelled>'} is missing "
                f"columns for {missing_metrics}; those fields will be null."
            )
            LOG.warning(message)
            warnings.append(message)

        identity_columns = layout.identity_columns
        managers: List[Dict[str, Any]] = []
        reference_indexes: List[Dict[str, Any]] = []
        peer_group_stats: Dict[str, Dict[str, Optional[float]]] = {}
        section_benchmarks: Dict[str, List[Dict[str, Any]]] = {}
        benchmark_returns: Dict[str, float] = {}
        # Benchmark returns per logical period, so history can resolve an
        # explicit benchmark return rather than always deriving one.
        benchmark_returns_by_period: Dict[str, Dict[str, float]] = {
            key: {} for key in logical_blocks
        }
        header_rows_detected = [layout.header_row]
        row_kind_counts: Dict[str, int] = {}
        seen_lookup_keys: Dict[str, int] = {}

        current_section: Optional[str] = None
        # Pass 1 - classify every row and collect benchmark returns first, so
        # managers can resolve an explicit benchmark return regardless of
        # whether the benchmark row sits above or below them.
        classified: List[Tuple[int, str, Optional[str], Dict[str, Optional[str]], Dict[str, Optional[float]], Optional[str]]] = []
        for row in range(layout.header_row + 1, layout.max_row + 1):
            name = cell_text(worksheet, row, identity_columns.get("name", 1))
            identity = {
                key: cell_text(worksheet, row, column)
                for key, column in identity_columns.items()
                if key != "name"
            }
            metrics = read_block_metrics(worksheet, row, block)
            kind = classify_manager_row(name, identity, metrics)
            row_kind_counts[kind] = row_kind_counts.get(kind, 0) + 1

            if kind == ROW_KIND_SECTION:
                current_section = name
            classified.append((row, kind, name, identity, metrics, current_section))

            if kind in (ROW_KIND_BENCHMARK, ROW_KIND_INDEX) and name:
                clean = strip_benchmark_prefix(name) if kind == ROW_KIND_BENCHMARK else name
                if kind == ROW_KIND_BENCHMARK:
                    section_benchmarks.setdefault(current_section or "", []).append(
                        {
                            "name": clean,
                            "return_cumulative": round_or_none(metrics["return_cumulative"]),
                            "row": row,
                        }
                    )
                if metrics["return_cumulative"] is not None:
                    benchmark_returns.setdefault(clean, metrics["return_cumulative"])
                # Same row, read across every logical period.
                for period_key, period_block in logical_blocks.items():
                    if period_block is None:
                        continue
                    period_return = read_block_metrics(worksheet, row, period_block)[
                        "return_cumulative"
                    ]
                    if period_return is not None:
                        benchmark_returns_by_period[period_key].setdefault(
                            clean, period_return
                        )

        # Pass 2 - build the manager records.
        for row, kind, name, identity, metrics, section in classified:
            if kind == ROW_KIND_BLANK:
                continue

            if kind == ROW_KIND_SECTION:
                skipped_rows.append({"row": row, "name": name, "reason": "section header"})
                continue

            if kind == ROW_KIND_PEER_STAT:
                label = re.sub(
                    r"[^a-z0-9]+", "_", normalize_lookup_name(name).replace("peer group ", "")
                ).strip("_")
                peer_group_stats.setdefault(
                    label,
                    {
                        "return_cumulative": round_or_none(metrics["return_cumulative"]),
                        "excess_return_cumulative": round_or_none(
                            metrics["excess_return_cumulative"]
                        ),
                    },
                )
                skipped_rows.append(
                    {"row": row, "name": name, "reason": "peer group statistic row"}
                )
                continue

            if kind == ROW_KIND_BENCHMARK:
                skipped_rows.append(
                    {"row": row, "name": name, "reason": "section benchmark row"}
                )
                continue

            if kind == ROW_KIND_INDEX:
                reference_indexes.append(
                    {
                        "name": name,
                        "section": section,
                        "return_cumulative": round_or_none(metrics["return_cumulative"]),
                        "peer_percentile": metrics["peer_percentile"],
                        "excess_return_cumulative": round_or_none(
                            metrics["excess_return_cumulative"]
                        ),
                    }
                )
                skipped_rows.append(
                    {"row": row, "name": name, "reason": "reference index row"}
                )
                continue

            # --- manager row -------------------------------------------------
            calculation_benchmark = identity.get("calculation_benchmark")
            section_bench_list = section_benchmarks.get(section or "", [])
            section_bench_name = section_bench_list[0]["name"] if section_bench_list else None

            benchmark = calculation_benchmark or section_bench_name or spec["default_benchmark"]
            benchmark_source = (
                "calculation_benchmark_column"
                if calculation_benchmark
                else ("section_benchmark_row" if section_bench_name else "asset_class_default")
            )
            if benchmark_source == "asset_class_default":
                warnings.append(
                    f"{context}: row {row} ({name}) had no benchmark in the workbook; "
                    f"used the asset-class default {spec['default_benchmark']!r}."
                )

            return_cumulative = metrics["return_cumulative"]
            excess_return = metrics["excess_return_cumulative"]

            # --- every logical period for this manager ------------------------
            performance_periods: Dict[str, Dict[str, Any]] = {}
            for period_key in (d["key"] for d in LOGICAL_PERIOD_DEFINITIONS):
                period_block = logical_blocks.get(period_key)
                if period_block is None:
                    performance_periods[period_key] = empty_period_record()
                    continue
                period_metrics = (
                    metrics
                    if period_block is block
                    else read_block_metrics(worksheet, row, period_block)
                )
                period_benchmark_return, period_calculated = resolve_benchmark_return(
                    period_metrics,
                    benchmark,
                    benchmark_returns_by_period.get(period_key, {}),
                )
                performance_periods[period_key] = build_period_record(
                    period_block, period_metrics, period_benchmark_return, period_calculated
                )

            # Backward-compatible top-level fields mirror the selected quarter.
            selected_record = performance_periods["selected_quarter"]
            benchmark_return = selected_record["benchmark_return"]
            benchmark_return_calculated = selected_record["benchmark_return_calculated"]
            if benchmark_return is None:
                warnings.append(
                    f"{context}: row {row} ({name}) has no benchmark return and it "
                    f"could not be derived (return or excess return missing)."
                )

            if return_cumulative is None:
                warnings.append(
                    f"{context}: row {row} ({name}) has no return for {quarter.label}; "
                    f"the record was kept with null performance values."
                )

            record = {
                "manager": name,
                "strategy_name": identity.get("strategy_name") or "",
                "asset_class": spec["asset_class"],
                "benchmark": benchmark,
                "calculation_benchmark": calculation_benchmark or "",
                "return_cumulative": round_or_none(return_cumulative),
                "benchmark_return": round_or_none(benchmark_return),
                "benchmark_return_calculated": benchmark_return_calculated,
                "peer_percentile": metrics["peer_percentile"],
                "excess_return_cumulative": round_or_none(excess_return),
                "source_file": path.name,
                # --- historical context -------------------------------------
                "performance_periods": performance_periods,
                "performance_trends": compute_performance_trends(performance_periods),
                "ranking_trend": build_ranking_trend(performance_periods),
                # Additive context - useful when writing commentary, ignored by
                # any consumer that only reads the required fields.
                "ticker": identity.get("ticker") or "",
                "portfolio_managers": split_portfolio_managers(
                    identity.get("portfolio_managers")
                ),
                "source_row": row,
                "source_section": section or "",
                "benchmark_source": benchmark_source,
            }

            lookup_key = normalize_lookup_name(name)
            if lookup_key in seen_lookup_keys:
                warnings.append(
                    f"{context}: duplicate manager name {name!r} at row {row}; "
                    f"kept the first occurrence (row {managers[seen_lookup_keys[lookup_key]]['source_row']})."
                )
                skipped_rows.append(
                    {"row": row, "name": name, "reason": "duplicate manager name"}
                )
                continue

            seen_lookup_keys[lookup_key] = len(managers)
            managers.append(record)

        LOG.info(
            "  %s -> %d managers, %d reference indexes, %d warnings",
            path.name,
            len(managers),
            len(reference_indexes),
            len(warnings),
        )

        parsed = {
            "managers": managers,
            "reference_indexes": reference_indexes,
            "peer_group_stats": peer_group_stats,
            "section_benchmarks": section_benchmarks,
            "lineage": {
                "source_file": path.name,
                "worksheet": worksheet.title,
                "period_header": block.label or "",
                "generated_at": "",  # filled in by the caller
            },
            "warnings": warnings,
        }

        debug = {
            "file": path.name,
            "absolute_path": str(path),
            "worksheets_found": worksheet_names,
            "worksheet_parsed": worksheet.title,
            "layout": layout.describe(),
            "manager_header_rows_detected": header_rows_detected,
            "selected_period_block": block.describe(),
            "logical_period_map": logical_period_map,
            "logical_period_map_summary": [
                f"{key} -> {entry['workbook_block'] or 'NOT FOUND'}"
                for key, entry in logical_period_map.items()
            ],
            "metric_columns_selected": {
                metric: get_column_letter(column)
                for metric, column in sorted(block.metric_columns.items(), key=lambda kv: kv[1])
            },
            "row_counts": {
                "rows_scanned": layout.max_row - layout.header_row,
                "managers_parsed": len(managers),
                "reference_indexes": len(reference_indexes),
                "peer_group_stat_rows": row_kind_counts.get(ROW_KIND_PEER_STAT, 0),
                "benchmark_rows": row_kind_counts.get(ROW_KIND_BENCHMARK, 0),
                "section_header_rows": row_kind_counts.get(ROW_KIND_SECTION, 0),
                "blank_rows": row_kind_counts.get(ROW_KIND_BLANK, 0),
            },
            "group_sections_detected": [
                key for key in section_benchmarks.keys() if key
            ],
            "warnings": warnings,
            "skipped_rows": skipped_rows,
        }
        return parsed, debug
    finally:
        workbook.close()


# =============================================================================
# SECTION 7 - MARKET DATA WORKBOOK PARSING
# =============================================================================


@dataclass
class MarketSection:
    """One contiguous block of market series (factors / sectors / industries)."""

    title: str
    category: str
    first_row: int
    last_row: int
    members: List[Dict[str, Any]] = field(default_factory=list)
    benchmarks: List[Dict[str, Any]] = field(default_factory=list)

    def describe(self) -> Dict[str, Any]:
        return {
            "section_title": self.title,
            "category": self.category,
            "row_range": f"{self.first_row}-{self.last_row}",
            "member_count": len(self.members),
            "benchmark_rows": [b["name"] for b in self.benchmarks],
        }


def classify_market_section(title: str) -> str:
    lowered = title.lower()
    for needle, category in MARKET_CATEGORY_RULES:
        if needle in lowered:
            return category
    return "other"


def build_display_name(raw_name: str) -> str:
    """
    Turn a Morningstar index name into something readable in commentary.

    'Russell 1000 Growth Ind/Tech TR USD'      -> 'Technology'
    'S&P MidCap 400 Sub/Electl Compnts&Eq PR'  -> 'Electrical Components & Equipment'
    """
    name = raw_name.strip()
    if "/" in name:
        name = name.split("/", 1)[1]
    for suffix in MARKET_NAME_SUFFIXES:
        if name.upper().endswith(suffix.upper()):
            name = name[: -len(suffix)]
            break
    name = name.strip()
    if not name:
        return raw_name

    expanded = MARKET_NAME_EXPANSIONS.get(name.lower())
    if expanded:
        return expanded

    # Token-level cleanup for the long tail of truncated sub-industry names.
    # Guard brand ampersands ("S&P") before spacing out separator ampersands.
    guarded = re.sub(r"(?<![A-Za-z])S&P(?![A-Za-z])", AMPERSAND_GUARD, name)
    spaced = re.sub(r"\s*&\s*", " & ", guarded).replace(AMPERSAND_GUARD, "S&P")
    tokens: List[str] = []
    for token in spaced.split():
        key = token.strip(".,").lower()
        tokens.append(MARKET_TOKEN_EXPANSIONS.get(key, token))
    return re.sub(r"\s+", " ", " ".join(tokens)).strip() or raw_name


def parse_market_workbook(
    path: Path, quarter: Quarter
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Parse the sector / industry / factor workbook into named sections."""
    LOG.info("Parsing market workbook: %s", path.name)
    warnings: List[str] = []
    skipped_rows: List[Dict[str, Any]] = []

    workbook = openpyxl.load_workbook(path, data_only=True)
    try:
        worksheet_names = list(workbook.sheetnames)
        worksheet = workbook[worksheet_names[0]]
        layout = build_sheet_layout(worksheet)
        context = f"{path.name} [{worksheet.title}]"
        block = select_period_block(layout, quarter, warnings, context)

        logical_blocks, logical_period_map = resolve_logical_periods(
            layout, quarter, block, MARKET_LOGICAL_PERIODS, warnings, context
        )

        name_column = layout.identity_columns.get("name", 1)
        benchmark_column = layout.identity_columns.get("calculation_benchmark")

        sections: List[MarketSection] = []
        current: Optional[MarketSection] = None

        for row in range(layout.header_row + 1, layout.max_row + 1):
            name = cell_text(worksheet, row, name_column)
            calc_benchmark = (
                cell_text(worksheet, row, benchmark_column) if benchmark_column else None
            )
            metrics = read_block_metrics(worksheet, row, block)

            if not name:
                skipped_rows.append({"row": row, "name": None, "reason": "blank row"})
                continue

            # A section header has a title in column A and nothing in the
            # calculation-benchmark column.
            if not calc_benchmark and not any(v is not None for v in metrics.values()):
                if current is not None:
                    current.last_row = row - 1
                category = classify_market_section(name)
                current = MarketSection(
                    title=name, category=category, first_row=row, last_row=row
                )
                sections.append(current)
                if category == "other":
                    warnings.append(
                        f"{context}: section {name!r} (row {row}) did not match any "
                        f"factor/sector/industry rule; its rows are available in the "
                        f"debug report but are not summarised."
                    )
                continue

            if current is None:
                skipped_rows.append(
                    {"row": row, "name": name, "reason": "row appeared before any section header"}
                )
                continue

            current.last_row = row

            # Read this row across every logical market period once.
            period_metrics: Dict[str, Dict[str, Optional[float]]] = {}
            for period_key, period_block in logical_blocks.items():
                if period_block is None:
                    period_metrics[period_key] = {
                        "return_cumulative": None,
                        "excess_return_cumulative": None,
                        "peer_percentile": None,
                    }
                    continue
                values = (
                    metrics
                    if period_block is block
                    else read_block_metrics(worksheet, row, period_block)
                )
                period_metrics[period_key] = {
                    "return_cumulative": round_or_none(values["return_cumulative"]),
                    "excess_return_cumulative": round_or_none(
                        values["excess_return_cumulative"]
                    ),
                    "peer_percentile": values["peer_percentile"],
                }

            if BENCHMARK_ROW_RE.match(name):
                current.benchmarks.append(
                    {
                        "name": strip_benchmark_prefix(name),
                        "return_cumulative": round_or_none(metrics["return_cumulative"]),
                        "periods": period_metrics,
                        "row": row,
                    }
                )
                continue

            row_category = current.category
            if MARKET_ROW_INDUSTRY_RE.search(name) and row_category == "sectors":
                row_category = "industries"

            current.members.append(
                {
                    "name": name,
                    "display_name": build_display_name(name),
                    "category": row_category,
                    "section": current.title,
                    "benchmark": calc_benchmark or "",
                    "return_cumulative": round_or_none(metrics["return_cumulative"]),
                    "excess_return_cumulative": round_or_none(
                        metrics["excess_return_cumulative"]
                    ),
                    "peer_percentile": metrics["peer_percentile"],
                    "periods": period_metrics,
                    "source_row": row,
                }
            )

        sections_by_title = {section.title: section for section in sections}
        LOG.info(
            "  %s -> %d sections, %d series rows",
            path.name,
            len(sections),
            sum(len(s.members) for s in sections),
        )

        parsed = {
            "sections": sections,
            "sections_by_title": sections_by_title,
            "logical_blocks": logical_blocks,
            "lineage": {
                "source_file": path.name,
                "worksheet": worksheet.title,
                "period_header": block.label or "",
                "generated_at": "",
            },
            "warnings": warnings,
        }

        debug = {
            "file": path.name,
            "absolute_path": str(path),
            "worksheets_found": worksheet_names,
            "worksheet_parsed": worksheet.title,
            "layout": layout.describe(),
            "selected_period_block": block.describe(),
            "logical_period_map": logical_period_map,
            "logical_period_map_summary": [
                f"{key} -> {entry['workbook_block'] or 'NOT FOUND'}"
                for key, entry in logical_period_map.items()
            ],
            "metric_columns_selected": {
                metric: get_column_letter(column)
                for metric, column in sorted(block.metric_columns.items(), key=lambda kv: kv[1])
            },
            "market_section_ranges": [section.describe() for section in sections],
            "row_counts": {
                "rows_scanned": layout.max_row - layout.header_row,
                "sections_detected": len(sections),
                "series_rows_parsed": sum(len(s.members) for s in sections),
                "benchmark_rows": sum(len(s.benchmarks) for s in sections),
            },
            "warnings": warnings,
            "skipped_rows": skipped_rows,
        }
        return parsed, debug
    finally:
        workbook.close()


def select_market_data_for_asset_class(
    market: Dict[str, Any], asset_key: str, warnings: List[str]
) -> Dict[str, Any]:
    """
    Assemble the market context for one asset class.

    Pulls each category from the preferred sections listed in
    MARKET_SECTION_PREFERENCES, then sweeps the chosen sections for rows that
    were reclassified at row level (e.g. small-cap 'Sub/...' series).
    """
    sections_by_title: Dict[str, MarketSection] = market["sections_by_title"]
    preferences = MARKET_SECTION_PREFERENCES.get(asset_key, {})

    result: Dict[str, List[Dict[str, Any]]] = {
        "factors": [],
        "sectors": [],
        "industries": [],
    }
    sections_used: Dict[str, List[str]] = {"factors": [], "sectors": [], "industries": []}
    benchmarks: List[Dict[str, Any]] = []
    # (target_category, section) pairs. Harvesting is scoped to the category a
    # section was selected for, so pulling a sector section in to mine its
    # "Sub/..." industry rows can never pollute the sector list.
    chosen: List[Tuple[str, MarketSection]] = []
    seen_benchmarks: set = set()

    for category, preferred_titles in preferences.items():
        wanted = MARKET_SECTIONS_PER_CATEGORY_OVERRIDES.get(
            (asset_key, category), MARKET_SECTIONS_PER_CATEGORY
        )
        used = 0
        for title in preferred_titles:
            section = sections_by_title.get(title)
            if section is None:
                warnings.append(
                    f"{asset_key}: preferred market section {title!r} was not found in "
                    f"the market workbook."
                )
                continue
            chosen.append((category, section))
            sections_used[category].append(section.title)
            for benchmark in section.benchmarks:
                identity = (section.title, benchmark["name"])
                if identity in seen_benchmarks:
                    continue
                seen_benchmarks.add(identity)
                benchmarks.append({"section": section.title, **benchmark})
            used += 1
            if used >= wanted:
                break
        if used == 0 and preferred_titles:
            warnings.append(
                f"{asset_key}: no market section available for '{category}'."
            )

    seen_rows: set = set()
    for category, section in chosen:
        for member in section.members:
            # Row-level overrides may reclassify a row (e.g. a "Sub/..." series
            # inside a sector section); only keep rows for the target category.
            if member["category"] != category:
                continue
            identity = (category, section.title, member["source_row"])
            if identity in seen_rows:
                continue
            seen_rows.add(identity)
            result[category].append(member)

    for category, rows in result.items():
        if not rows:
            warnings.append(
                f"{asset_key}: no '{category}' rows are available in this export; "
                f"summaries for that category will be empty."
            )

    return {
        "period_header": market["lineage"]["period_header"],
        "source_file": market["lineage"]["source_file"],
        "sections_used": sections_used,
        "section_benchmarks": benchmarks,
        "factors": result["factors"],
        "sectors": result["sectors"],
        "industries": result["industries"],
    }


def flatten_market_row(member: Dict[str, Any], period_key: str) -> Dict[str, Any]:
    """
    Project a market series onto one period.

    The returned shape matches the single-period V1 row exactly, so the
    `market_data` alias stays byte-for-byte compatible for existing consumers.
    """
    values = member.get("periods", {}).get(period_key, {})
    return {
        "name": member["name"],
        "display_name": member["display_name"],
        "category": member["category"],
        "section": member["section"],
        "benchmark": member["benchmark"],
        "return_cumulative": values.get("return_cumulative"),
        "excess_return_cumulative": values.get("excess_return_cumulative"),
        "peer_percentile": values.get("peer_percentile"),
        "source_row": member["source_row"],
    }


def build_market_period_view(
    selection: Dict[str, Any],
    period_key: str,
    period_block: Optional[PeriodBlock],
    quarter_end: date,
) -> Dict[str, Any]:
    """One entry of the asset-class `market_data_periods` object."""
    factors = [flatten_market_row(row, period_key) for row in selection["factors"]]
    sectors = [flatten_market_row(row, period_key) for row in selection["sectors"]]
    industries = [flatten_market_row(row, period_key) for row in selection["industries"]]

    # The market workbook has no "YTD thru Last Q End" block, so its YTD runs
    # to the export date rather than to quarter end. Manager YTD and market YTD
    # therefore cover different windows; say so in the data, not just the log.
    aligned = period_block is not None and period_block.end_date == quarter_end
    note = ""
    if period_block is not None and not aligned:
        note = (
            f"This market window ends {iso_or_empty(period_block.end_date)}, not the "
            f"selected quarter end {quarter_end.isoformat()}. Do not present it as "
            f"covering exactly the same window as the manager figures."
        )

    view = {
        "label": period_block.label if period_block else "",
        "period_start_date": iso_or_empty(period_block.start_date) if period_block else "",
        "period_end_date": iso_or_empty(period_block.end_date) if period_block else "",
        "basis": period_block.basis if period_block else "",
        "available": period_block is not None,
        "aligned_with_selected_quarter_end": aligned,
        "note": note,
        "source_file": selection["source_file"],
        "sections_used": selection["sections_used"],
        "factors": factors,
        "sectors": sectors,
        "industries": industries,
    }
    view["summaries"] = build_summaries(view)
    return view


def build_market_trends(market_periods: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Headline best/worst movers, pre-computed so the agent need not sort."""

    def pick(period_key: str, category: str, best: bool) -> str:
        summaries = market_periods.get(period_key, {}).get("summaries", {})
        rows = summaries.get(f"{'top' if best else 'bottom'}_10_{category}", [])
        return rows[0]["display_name"] if rows else ""

    return {
        "best_sector_selected_quarter": pick("selected_quarter", "sectors", True),
        "worst_sector_selected_quarter": pick("selected_quarter", "sectors", False),
        "best_sector_ytd": pick("ytd", "sectors", True),
        "worst_sector_ytd": pick("ytd", "sectors", False),
        "best_factor_selected_quarter": pick("selected_quarter", "factors", True),
        "worst_factor_selected_quarter": pick("selected_quarter", "factors", False),
        "best_industry_selected_quarter": pick("selected_quarter", "industries", True),
        "worst_industry_selected_quarter": pick("selected_quarter", "industries", False),
        "best_sector_trailing_1_year": pick("trailing_1_year", "sectors", True),
        "worst_sector_trailing_1_year": pick("trailing_1_year", "sectors", False),
    }


# =============================================================================
# SECTION 8 - SUMMARIES
# =============================================================================


def summarize_series(rows: Sequence[Dict[str, Any]], top: bool) -> List[Dict[str, Any]]:
    """Rank market series by return, dropping rows with no return."""
    usable = [row for row in rows if row.get("return_cumulative") is not None]
    ordered = sorted(
        usable, key=lambda row: row["return_cumulative"], reverse=top
    )[:SUMMARY_TOP_N]
    return [
        {
            "name": row["name"],
            "display_name": row["display_name"],
            "section": row["section"],
            "return_cumulative": row["return_cumulative"],
            "excess_return_cumulative": row["excess_return_cumulative"],
        }
        for row in ordered
    ]


def build_summaries(market_data: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "top_10_factors": summarize_series(market_data["factors"], top=True),
        "bottom_10_factors": summarize_series(market_data["factors"], top=False),
        "top_10_sectors": summarize_series(market_data["sectors"], top=True),
        "bottom_10_sectors": summarize_series(market_data["sectors"], top=False),
        "top_10_industries": summarize_series(market_data["industries"], top=True),
        "bottom_10_industries": summarize_series(market_data["industries"], top=False),
    }


# =============================================================================
# SECTION 9 - MANAGER LOOKUP
# =============================================================================


def build_manager_lookup(
    managers: Sequence[Dict[str, Any]], warnings: List[str]
) -> Dict[str, int]:
    """
    Build the normalised name -> array index lookup embedded in each file.

    Primary keys are manager (fund) names. Optional secondary keys (ticker and
    strategy name) are added only when they do not collide with an existing
    key, so a manager-name hit always wins.
    """
    lookup: Dict[str, int] = {}
    for index, manager in enumerate(managers):
        key = normalize_lookup_name(manager["manager"])
        if not key:
            continue
        if key in lookup:
            warnings.append(
                f"manager_lookup: duplicate normalised key {key!r}; kept index {lookup[key]}."
            )
            continue
        lookup[key] = index

    if INCLUDE_TICKER_AND_STRATEGY_IN_LOOKUP:
        for index, manager in enumerate(managers):
            for extra in (manager.get("ticker"), manager.get("strategy_name")):
                key = normalize_lookup_name(extra)
                if key and key not in lookup:
                    lookup[key] = index

    return lookup


# =============================================================================
# SECTION 10 - EXTENSION POINT: MANAGER ATTRIBUTION (NOT PARSED IN V1)
# -----------------------------------------------------------------------------
# V1 deliberately does not parse manager-specific attribution workbooks. When
# that lands, implement `parse_manager_attribution` to return
#   { <normalised manager name>: {...attribution payload...} }
# and call `attach_attribution` from `build_quarter` right after the managers
# are parsed. Nothing else in the pipeline needs to change: the attribution
# payload is merged onto the existing manager records in place, so
# `manager_lookup` indexes stay valid.
# =============================================================================


def parse_manager_attribution(
    quarter_folder: Path, quarter: Quarter
) -> Dict[str, Dict[str, Any]]:
    """V2 hook. Returns an empty mapping in V1."""
    LOG.debug(
        "Attribution parsing is not implemented in V1 (folder=%s, quarter=%s)",
        quarter_folder,
        quarter.label,
    )
    return {}


def attach_attribution(
    managers: Sequence[Dict[str, Any]], attribution: Dict[str, Dict[str, Any]]
) -> int:
    """Merge attribution payloads onto manager records in place."""
    matched = 0
    for manager in managers:
        payload = attribution.get(normalize_lookup_name(manager["manager"]))
        if payload:
            manager["attribution"] = payload
            matched += 1
    return matched


# =============================================================================
# SECTION 11 - ORCHESTRATION
# =============================================================================


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    LOG.info("Wrote %s (%.1f KB)", path, path.stat().st_size / 1024)


def build_quarter(
    input_root: Path, output_root: Path, requested_quarter: str
) -> Dict[str, Any]:
    """Run the full build for one quarter. Returns the manifest."""
    quarter, quarter_folder = resolve_quarter(input_root, requested_quarter)
    LOG.info("Building quarter %s from %s", quarter.label, quarter_folder)

    generated_at = utc_now_iso()
    output_folder = output_root / quarter.label
    output_folder.mkdir(parents=True, exist_ok=True)

    global_warnings: List[str] = []
    debug_report: Dict[str, Any] = {
        "script_version": SCRIPT_VERSION,
        "period": quarter.label,
        "period_start_date": quarter.start_date.isoformat(),
        "period_end_date": quarter.end_date.isoformat(),
        "generated_at": generated_at,
        "input_folder": str(quarter_folder),
        "output_folder": str(output_folder),
        "workbooks": {},
        "asset_class_market_selection": {},
        "warnings": global_warnings,
    }

    # --- market workbook (shared by all three asset classes) -----------------
    market_path = find_workbook(quarter_folder, MARKET_WORKBOOK)
    market_parsed: Optional[Dict[str, Any]] = None
    if market_path is None:
        message = (
            f"Market workbook '{MARKET_WORKBOOK['expected_file']}' was not found in "
            f"{quarter_folder}. Asset-class files will be written without market data."
        )
        LOG.error(message)
        global_warnings.append(message)
        debug_report["workbooks"]["market"] = {"error": message}
    else:
        market_parsed, market_debug = parse_market_workbook(market_path, quarter)
        market_parsed["lineage"]["generated_at"] = generated_at
        debug_report["workbooks"]["market"] = market_debug
        global_warnings.extend(market_parsed["warnings"])

    # --- asset-class workbooks ------------------------------------------------
    manager_counts: Dict[str, int] = {}
    files_written: List[str] = []

    for spec in ASSET_CLASSES:
        key = spec["key"]
        path = find_workbook(quarter_folder, spec)
        if path is None:
            message = (
                f"Workbook '{spec['expected_file']}' was not found in {quarter_folder}; "
                f"{spec['output_file']} was not written."
            )
            LOG.error(message)
            global_warnings.append(message)
            debug_report["workbooks"][key] = {"error": message}
            manager_counts[key] = 0
            continue

        try:
            parsed, debug = parse_asset_class_workbook(path, spec, quarter)
        except Exception as error:  # noqa: BLE001 - report and keep going
            message = f"Failed to parse {path.name}: {error}"
            LOG.error(message)
            global_warnings.append(message)
            debug_report["workbooks"][key] = {"error": message, "file": path.name}
            manager_counts[key] = 0
            continue

        parsed["lineage"]["generated_at"] = generated_at
        file_warnings: List[str] = list(parsed["warnings"])

        # V2 hook - a no-op in V1.
        attribution = parse_manager_attribution(quarter_folder, quarter)
        if attribution:
            attach_attribution(parsed["managers"], attribution)

        if market_parsed is not None:
            selection = select_market_data_for_asset_class(
                market_parsed, key, file_warnings
            )
            market_logical_blocks = market_parsed["logical_blocks"]
            market_data_periods = {
                period_key: build_market_period_view(
                    selection,
                    period_key,
                    market_logical_blocks.get(period_key),
                    quarter.end_date,
                )
                for period_key in MARKET_LOGICAL_PERIODS
            }
            debug_report["asset_class_market_selection"][key] = {
                "sections_used": selection["sections_used"],
                "counts": {
                    "factors": len(selection["factors"]),
                    "sectors": len(selection["sectors"]),
                    "industries": len(selection["industries"]),
                },
                "periods": {
                    period_key: view["label"]
                    for period_key, view in market_data_periods.items()
                },
            }
        else:
            selection = {
                "period_header": "",
                "source_file": "",
                "sections_used": {"factors": [], "sectors": [], "industries": []},
                "section_benchmarks": [],
                "factors": [],
                "sectors": [],
                "industries": [],
            }
            market_data_periods = {
                period_key: build_market_period_view(
                    selection, period_key, None, quarter.end_date
                )
                for period_key in MARKET_LOGICAL_PERIODS
            }

        # `market_data` and `summaries` stay as top-level aliases of the
        # selected quarter so V1 consumers keep working unchanged.
        selected_view = market_data_periods["selected_quarter"]
        market_data = {
            "period_header": selection["period_header"],
            "source_file": selection["source_file"],
            "sections_used": selection["sections_used"],
            "section_benchmarks": selection["section_benchmarks"],
            "factors": selected_view["factors"],
            "sectors": selected_view["sectors"],
            "industries": selected_view["industries"],
        }
        summaries = selected_view["summaries"]
        market_trends = build_market_trends(market_data_periods)

        manager_lookup = build_manager_lookup(parsed["managers"], file_warnings)

        payload = {
            "asset_class": spec["asset_class"],
            "asset_class_key": key,
            "period": quarter.label,
            "period_start_date": quarter.start_date.isoformat(),
            "period_end_date": quarter.end_date.isoformat(),
            "default_benchmark": spec["default_benchmark"],
            "lineage": parsed["lineage"],
            "manager_count": len(parsed["managers"]),
            "managers": parsed["managers"],
            "manager_lookup": manager_lookup,
            "reference_indexes": parsed["reference_indexes"],
            "peer_group_stats": parsed["peer_group_stats"],
            # Placeholder; replaced below where the peer universe is unusable.
            # Selected-quarter aliases, unchanged from V1.
            "market_data": market_data,
            "summaries": summaries,
            # Multi-period market context and pre-computed headline movers.
            "market_data_periods": market_data_periods,
            "market_trends": market_trends,
            "warnings": file_warnings,
        }

        if not spec.get("peer_universe_reliable", True):
            strip_peer_universe(payload)

        write_json(output_folder / spec["output_file"], payload)
        files_written.append(spec["output_file"])
        manager_counts[key] = len(parsed["managers"])

        debug["warnings"] = file_warnings
        debug_report["workbooks"][key] = debug
        global_warnings.extend(file_warnings)

    # --- manifest -------------------------------------------------------------
    manifest = {
        "period": quarter.label,
        "generated_at": generated_at,
        "files": [spec["output_file"] for spec in ASSET_CLASSES],
        "manager_counts": {spec["key"]: manager_counts.get(spec["key"], 0) for spec in ASSET_CLASSES},
        "files_written": files_written,
        "script_version": SCRIPT_VERSION,
        "input_folder": str(quarter_folder),
        "warning_count": len(global_warnings),
    }
    write_json(output_folder / "manifest.json", manifest)

    debug_report["warnings"] = global_warnings
    write_json(output_folder / "debug_layout_report.json", debug_report)

    return manifest


# =============================================================================
# SECTION 12 - CLI
# =============================================================================


def configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)-8s %(message)s",
        stream=sys.stdout,
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_quarterly_json.py",
        description=(
            "Convert quarterly Morningstar Direct exports into Copilot-Studio-ready "
            "JSON for the Battle Book commentary agent."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            '  python build_quarterly_json.py --quarter "2026 Q2"\n'
            "  python build_quarterly_json.py --quarter latest\n"
            "  python build_quarterly_json.py --list-quarters\n"
        ),
    )
    parser.add_argument(
        "--quarter",
        default="latest",
        help='Quarter folder to build, e.g. "2026 Q2", or "latest" (default: latest).',
    )
    parser.add_argument(
        "--input-root",
        default=DEFAULT_INPUT_ROOT,
        help="Override the Quarterly Battle Book Inputs root folder.",
    )
    parser.add_argument(
        "--output-root",
        default=DEFAULT_OUTPUT_ROOT,
        help="Override the Quarterly Battle Book Data root folder.",
    )
    parser.add_argument(
        "--list-quarters",
        action="store_true",
        help="List the quarter folders found under the input root, then exit.",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    configure_logging(args.verbose)

    input_root = Path(args.input_root)
    output_root = Path(args.output_root)

    try:
        if args.list_quarters:
            for quarter, folder in discover_quarter_folders(input_root):
                print(f"{quarter.label}\t{folder}")
            return 0

        manifest = build_quarter(input_root, output_root, args.quarter)
    except (FileNotFoundError, ValueError) as error:
        LOG.error("%s", error)
        return 2
    except PermissionError as error:
        LOG.error(
            "Permission denied: %s\n"
            "       Close the workbook in Excel (or pause OneDrive sync) and retry.",
            error,
        )
        return 2

    total = sum(manifest["manager_counts"].values())
    LOG.info(
        "Done. %s: %d managers across %d files (%d warnings).",
        manifest["period"],
        total,
        len(manifest["files_written"]),
        manifest["warning_count"],
    )
    if manifest["warning_count"]:
        LOG.info("Review debug_layout_report.json for warning detail.")

    if not manifest["files_written"]:
        LOG.error(
            "No asset-class JSON files were produced for %s. "
            "Check that the quarter folder holds the four expected workbooks.",
            manifest["period"],
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
