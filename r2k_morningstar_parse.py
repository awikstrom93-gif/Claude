"""
============================================================
r2k_morningstar_parse.py  --  turn Morningstar statement downloads (Income Statement,
Balance Sheet, Cash Flow) into one tidy LONG table + a reusable securities crosswalk.
============================================================
Morningstar's "data download" exports are company-per-row matrices: the first columns are
Name / Ticker / CUSIP / CIK, then every metric is repeated once per fiscal year in the
sheet's range, with self-describing headers like

    "Total Revenue Value-FY Year2011 Reported Currency"
    "Report Period End Date-IS-FY Year2011"
    "Free Cash Flow to Equity Holders - FY Year2012"

This script parses those headers, so it works for ANY Morningstar statement download of the
same shape (not just the R2000G pull) -- point it at the files and it emits:

  morningstar_long.csv     one row per (cik, fiscal_year, statement, metric):
                           cik,ticker,cusip,name,statement,fiscal_year,period_end,
                           report_date,fiscal_period,fye,metric,value
                           Values are in REPORTED CURRENCY, actual units (NOT millions) --
                           same units as the SEC as-filed pull, so they compare directly.

  securities_crosswalk.csv cik,ticker,cusip,name  (deduped identity map)
  cusip2cik.json / ticker2cik.json   lookup maps for resolving holdings that lack a CIK

ALL line items are preserved (200+ per statement), so downstream reconciliation can sum
components (e.g. Gross Profit ?= Revenue - Cost Of Revenue) to adjudicate discrepancies.

INPUTS  (R2KG_BASE, or pass paths as argv)
    auto-globs *Morningstar*.xlsx and classifies each by filename
    (Income/Inc/IS -> IS, Balance/BS -> BS, Cash/CF -> CF)

RUN: python r2k_morningstar_parse.py  [file1.xlsx file2.xlsx ...]
============================================================
"""
from pathlib import Path
from datetime import datetime
import os, re, csv, sys, json, glob

import openpyxl

BASE = Path(os.environ.get("R2KG_BASE", "."))
LONG_CSV = BASE / "morningstar_long.csv"
XWALK_CSV = BASE / "securities_crosswalk.csv"
CUSIP2CIK = BASE / "cusip2cik.json"
TICKER2CIK = BASE / "ticker2cik.json"

ID_COLS = {"name", "ticker", "cusip", "cik"}
DESCRIPTORS = {  # base label (lowercased) -> tidy key
    "report period end date": "period_end",
    "report period end": "period_end",
    "actual report date": "report_date",
    "fiscal period": "fiscal_period",
    "fiscal year end": "fye",
}
YEAR_RE = re.compile(r"Year\s*(\d{4})")
NA = {"", "#n/a", "n/a", "na", "nm", "nmf", "none", "-", "--"}


def classify(path):
    """Return IS/BS/CF from the filename, or None if it can't be told confidently (so we don't
    mis-parse e.g. a Morningstar *returns* file as an income statement)."""
    n = path.name.lower()
    if any(k in n for k in ("income", "inc stmt", "_is_", " is ", "incomestatement")): return "IS"
    if "balance" in n or " bs " in n or "_bs_" in n or "balancesheet" in n: return "BS"
    if any(k in n for k in ("cash flow", "cash_flow", "cashflow", " cf ", "_cf_")): return "CF"
    return None


# filename keywords that mark a file as a statement download (vs returns/holdings/etc.)
STMT_GLOB_HINT = ("income", "balance", "cash")


def statement_files(folder):
    out = []
    for p in folder.glob("*Morningstar*.xlsx"):
        if classify(p) is not None:
            out.append(p)
    return sorted(out)


def parse_header(h):
    """Return (base_metric, year:int|None, descriptor_key|None). base_metric is the clean
    line-item name with the year, 'Value', currency note and statement infix stripped."""
    if not isinstance(h, str) or not h.strip():
        return None, None, None
    if h.strip().lower() in ID_COLS:
        return h.strip().lower(), None, None     # id column
    m = YEAR_RE.search(h)
    if not m:
        return None, None, None                  # not a year-bearing data column
    year = int(m.group(1))
    pre = h[:m.start()]
    pre = re.sub(r"[-–]\s*(IS|BS|CF)\s*[-–]\s*FY\s*$", "", pre)   # "-IS-FY "
    pre = re.sub(r"[-–]\s*FY\s*$", "", pre)                            # "-FY " / "- FY "
    pre = re.sub(r"\s+FY\s*$", "", pre)
    pre = re.sub(r"\s*Value\s*[-–]?\s*$", "", pre)                     # trailing " Value"
    base = pre.strip(" -–")
    key = DESCRIPTORS.get(base.lower())
    return base, year, key


def to_num(v):
    if v is None: return None
    if isinstance(v, (int, float)) and not isinstance(v, bool): return v
    s = str(v).strip()
    if s.lower() in NA: return None
    s = s.replace(",", "").replace("$", "")
    try:
        return float(s)
    except ValueError:
        return None


def to_date(v):
    if v is None: return ""
    if isinstance(v, datetime): return v.date().isoformat()
    s = str(v).strip()
    if not s or s.lower() in NA: return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try: return datetime.strptime(s, fmt).date().isoformat()
        except ValueError: pass
    return s[:10]


def clean_id(v):
    if v is None: return ""
    if isinstance(v, float) and v.is_integer(): return str(int(v))
    return str(v).strip()


def parse_workbook(path, statement, long_rows, ident):
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    for sn in wb.sheetnames:
        ws = wb[sn]
        it = ws.iter_rows(values_only=True)
        try: header = next(it)
        except StopIteration: continue
        # map columns
        id_idx = {}; year_cols = {}      # year -> {desc_key|metric: col}
        for ci, h in enumerate(header):
            base, year, dkey = parse_header(h)
            if base in ID_COLS and year is None:
                id_idx[base] = ci
            elif year is not None:
                slot = year_cols.setdefault(year, {})
                if dkey:                       # descriptor (period_end/report_date/...)
                    slot.setdefault("__" + dkey, ci)
                else:                          # a real line item
                    slot.setdefault(base, ci)
        if not id_idx or not year_cols:
            continue
        for row in it:
            cik = clean_id(row[id_idx["cik"]]) if "cik" in id_idx else ""
            tk = clean_id(row[id_idx["ticker"]]) if "ticker" in id_idx else ""
            cusip = clean_id(row[id_idx["cusip"]]) if "cusip" in id_idx else ""
            name = clean_id(row[id_idx["name"]]) if "name" in id_idx else ""
            if not cik and not tk:
                continue                       # blank / index parent row
            if cik:
                ident.setdefault(cik, {"ticker": tk, "cusip": cusip, "name": name})
            for year, slot in year_cols.items():
                def cell(k):
                    ci = slot.get(k)
                    return row[ci] if (ci is not None and ci < len(row)) else None
                period_end = to_date(cell("__period_end"))
                report_date = to_date(cell("__report_date"))
                fiscal_period = clean_id(cell("__fiscal_period"))
                fye = clean_id(cell("__fye"))
                metrics = {k: to_num(cell(k)) for k in slot if not k.startswith("__")}
                if not period_end and all(v is None for v in metrics.values()):
                    continue                   # company not present this year
                for metric, val in metrics.items():
                    if val is None:
                        continue
                    long_rows.append([cik, tk, cusip, name, statement, year, period_end,
                                      report_date, fiscal_period, fye, metric, val])
    wb.close()


def resolve_inputs(argv):
    """Expand argv into existing files, tolerant of shells (e.g. PowerShell) that don't expand
    globs themselves. Each arg may be a file, a glob, or a directory. Falls back to auto-globbing
    *Morningstar*.xlsx in R2KG_BASE when nothing is passed."""
    out = []
    for a in argv:
        p = Path(a)
        if p.is_dir():
            out += statement_files(p)
        elif any(ch in a for ch in "*?[") or not p.exists():
            hits = [Path(h) for h in glob.glob(a)]                      # as given (may be relative)
            if not hits:
                hits = [Path(h) for h in glob.glob(str(BASE / a))]      # relative to R2KG_BASE
            out += sorted(hits)
        else:
            out.append(p)
    if not out:
        out = statement_files(BASE)
    # dedupe, keep only existing files
    seen, res = set(), []
    for p in out:
        rp = p.resolve()
        if rp not in seen and p.exists():
            seen.add(rp); res.append(p)
    return res


def main():
    paths = resolve_inputs(sys.argv[1:])
    if not paths:
        raise SystemExit("!! no Morningstar workbooks found. Run with no args to auto-find "
                         "*Morningstar*.xlsx in this folder, or pass explicit file paths.")

    long_rows = []; ident = {}
    for p in paths:
        st = classify(p)
        if st is None:
            print(f"  SKIP   {p.name}  (can't tell if Income/Balance/Cash from the name -- "
                  f"rename it to include 'Income', 'Balance' or 'Cash' to include it)")
            continue
        n0 = len(long_rows)
        parse_workbook(p, st, long_rows, ident)
        print(f"  parsed {p.name}  [{st}]  -> {len(long_rows)-n0:,} values")
    if not long_rows:
        raise SystemExit("!! none of the inputs parsed -- check they are Morningstar statement downloads.")

    # dedupe long rows (last wins) on (cik,statement,year,metric)
    seen = {}
    for r in long_rows:
        seen[(r[0], r[4], r[5], r[10])] = r
    rows = list(seen.values())
    rows.sort(key=lambda r: (r[0], r[4], r[5], r[10]))
    with open(LONG_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cik", "ticker", "cusip", "name", "statement", "fiscal_year",
                    "period_end", "report_date", "fiscal_period", "fye", "metric", "value"])
        w.writerows(rows)

    # crosswalk + lookup maps
    with open(XWALK_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik", "ticker", "cusip", "name"])
        for cik, d in sorted(ident.items()):
            w.writerow([cik, d["ticker"], d["cusip"], d["name"]])
    bad_cusip = lambda c: (not c) or ("e+" in c.lower()) or ("." in c) or len(c) not in (8, 9)
    cusip2cik = {d["cusip"]: cik for cik, d in ident.items() if not bad_cusip(d["cusip"])}
    ticker2cik = {d["ticker"]: cik for cik, d in ident.items() if d["ticker"]}
    CUSIP2CIK.write_text(json.dumps(cusip2cik, indent=0))
    TICKER2CIK.write_text(json.dumps(ticker2cik, indent=0))
    n_badcusip = sum(1 for d in ident.values() if bad_cusip(d["cusip"]))

    print(f"\n  -> {LONG_CSV.name}    {len(rows):,} values  "
          f"({len({r[0] for r in rows})} companies, statements={sorted({r[4] for r in rows})}, "
          f"years {min(r[5] for r in rows)}-{max(r[5] for r in rows)})")
    print(f"  -> {XWALK_CSV.name}   {len(ident)} securities")
    print(f"  -> {CUSIP2CIK.name} / {TICKER2CIK.name}  "
          f"({len(cusip2cik)} cusip, {len(ticker2cik)} ticker keys; {n_badcusip} cusips unusable/corrupt)")


if __name__ == "__main__":
    main()
