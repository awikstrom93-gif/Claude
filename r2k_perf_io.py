"""
============================================================
r2k_perf_io.py  --  shared parser for the Morningstar Direct monthly performance
export and the monthly holdings workbook. Used by the performance comparison
(step 4) and the cohort attribution (step 5).
============================================================
The performance export is one row per security (plus a few index/benchmark rows at
the bottom) with identifier columns (CIK / Ticker / ISIN / CUSIP / Name) followed by
a run of month-end date columns holding a CUMULATIVE total-return series.

This module:
  - locates the header row and the contiguous block of date columns (robust to the
    several date formats Morningstar emits),
  - reads each row into {meta, dates, cum, periodic},
  - converts the cumulative series to PERIODIC monthly returns (the unit the analytics
    compound and attribute), and
  - tags the two index/benchmark rows (Russell 2000 Growth, S&P SmallCap 600 Growth).

CUMULATIVE-SERIES CONVENTION (RET_MODE env var, default "cumpct"):
    cumpct    values are cumulative % since inception   -> level = 1 + v/100
    cumlevel  values are a growth-of-1 / index level     -> level = v (rescaled to 1.0)
    periodic  values are already periodic monthly % ret  -> used directly
It prints what it detected + the index rows' first months so the convention can be
sanity-checked against Morningstar; flip RET_MODE if the monthly numbers look wrong.
============================================================
"""
from pathlib import Path
from datetime import date, datetime
import os, re
import openpyxl

RET_MODE = os.environ.get("RET_MODE", "cumpct").lower()
BASE = Path(os.environ.get("R2KG_BASE", "."))

# index/benchmark row recognition (substring match on the Name column, case-insensitive)
IDX_R2KG = ("russell 2000 growth",)
IDX_SP6G = ("s&p smallcap 600 growth", "s&p smallcap 600 grth", "sp smallcap 600 growth")
ID_COLS = ("cik", "ticker", "isin", "cusip", "name", "secid", "security", "symbol")


def to_f(x):
    try:
        if x in (None, "", "-", "NA", "N/A"): return None
        return float(str(x).replace(",", "").replace("%", "").replace("$", ""))
    except (TypeError, ValueError): return None


def ntk(t): return re.sub(r"[^A-Z0-9]", "", str(t or "").upper())


_MON = {m: i for i, m in enumerate(
    ["jan","feb","mar","apr","may","jun","jul","aug","sep","oct","nov","dec"], 1)}


def parse_month(cell):
    """Parse a header cell into a month-end date, or None if it isn't a date."""
    if cell is None: return None
    if isinstance(cell, (datetime, date)):
        d = cell.date() if isinstance(cell, datetime) else cell
        return _eom(d.year, d.month)
    s = str(cell).strip()
    if not s: return None
    m = re.match(r"^(\d{4})[-/.](\d{1,2})(?:[-/.](\d{1,2}))?$", s)          # 2015-05 / 2015-05-31
    if m: return _eom(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})$", s)              # 5/31/2015
    if m:
        y = int(m.group(3)); y += 2000 if y < 100 else 0
        return _eom(y, int(m.group(1)))
    m = re.match(r"^(\d{4})(\d{2})$", s)                                     # 201505
    if m: return _eom(int(m.group(1)), int(m.group(2)))
    m = re.match(r"^([A-Za-z]{3,})[ \-/]?(\d{4})$", s)                       # May 2015 / May-2015
    if m and m.group(1)[:3].lower() in _MON:
        return _eom(int(m.group(2)), _MON[m.group(1)[:3].lower()])
    m = re.match(r"^(\d{4})[ \-/]?([A-Za-z]{3,})$", s)                       # 2015 May
    if m and m.group(2)[:3].lower() in _MON:
        return _eom(int(m.group(1)), _MON[m.group(2)[:3].lower()])
    return None


def _eom(y, mo):
    if mo == 12: return date(y, 12, 31)
    nxt = date(y + (mo // 12), (mo % 12) + 1, 1)
    return date(nxt.year, nxt.month, 1).fromordinal(nxt.toordinal() - 1)


def find_performance_file():
    pats = ["*[Cc]onstituent*[Pp]erformance*.xlsx", "*[Mm]onthly*[Pp]erformance*.xlsx",
            "*[Pp]erformance*.xlsx", "*[Rr]eturn*.xlsx"]
    for p in pats:
        c = list(BASE.glob(p))
        if c: return c[0]
    raise FileNotFoundError("monthly performance workbook not found in R2KG_BASE")


def _cum_to_periodic(cum):
    """cum: list of (date, raw_value) sorted ascending. Returns list of (date, periodic_ret)."""
    pts = [(d, v) for d, v in cum if v is not None]
    if not pts: return []
    if RET_MODE == "periodic":
        return [(d, v / 100.0) for d, v in pts]
    if RET_MODE == "cumlevel":
        base = pts[0][1]
        levels = [(d, (v / base) if base else None) for d, v in pts]
    else:  # cumpct
        levels = [(d, 1.0 + v / 100.0) for d, v in pts]
    out = []
    prev = None
    for d, lv in levels:
        if lv is None or lv <= 0: prev = lv; continue
        out.append((d, (lv / prev - 1.0) if (prev and prev > 0) else (lv - 1.0)))
        prev = lv
    return out


def load_performance(path=None, verbose=True):
    """Returns (series, index_rows, dates):
        series      list of {meta, ret:{date:periodic}, cum:{date:raw}}  (constituents)
        index_rows  {"R2KG": <series-dict>, "SP6G": <series-dict>}       (benchmarks)
        dates       sorted full list of month-end dates seen
    """
    path = Path(path) if path else find_performance_file()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    # locate header row: the row with the most identifier-column hits AND >=12 date cols to its right
    hdr_i = date_cols = id_idx = None
    for i, r in enumerate(rows[:30]):
        cells = [str(c).strip().lower() if c is not None else "" for c in r]
        idh = {nm: j for j, c in enumerate(cells) for nm in ID_COLS if c == nm}
        if not idh: continue
        dcols = [(j, parse_month(r[j])) for j in range(len(r)) if parse_month(r[j])]
        if len(dcols) >= 12:
            hdr_i, date_cols, id_idx = i, dcols, idh
            break
    if hdr_i is None:
        raise ValueError("could not locate header row with identifiers + >=12 date columns")

    dates = [d for _, d in date_cols]
    all_dates = sorted(set(dates))

    def meta_of(r):
        def g(nm):
            j = id_idx.get(nm)
            return r[j] if (j is not None and j < len(r) and r[j] is not None) else None
        cik = g("cik")
        cik = str(int(to_f(cik))).zfill(10) if (cik is not None and to_f(cik) is not None) else None
        return {"cik": cik, "ticker": str(g("ticker") or "").strip(), "nt": ntk(g("ticker")),
                "isin": str(g("isin") or "").strip().upper(), "cusip": str(g("cusip") or "").strip().upper(),
                "name": str(g("name") or g("security") or "").strip()}

    series, index_rows = [], {}
    for r in rows[hdr_i + 1:]:
        if r is None or all(c is None for c in r): continue
        meta = meta_of(r)
        nm = meta["name"].lower()
        cum = [(d, to_f(r[j])) for j, d in date_cols if j < len(r)]
        per = dict(_cum_to_periodic(cum))
        rec = {"meta": meta, "ret": per, "cum": {d: to_f(r[j]) for j, d in date_cols if j < len(r)}}
        if any(k in nm for k in IDX_R2KG):
            index_rows["R2KG"] = rec
        elif any(k in nm for k in IDX_SP6G):
            index_rows["SP6G"] = rec
        elif meta["cik"] or meta["nt"]:
            series.append(rec)

    if verbose:
        print(f"  performance file: {path.name}")
        print(f"    header row {hdr_i+1}; {len(date_cols)} month columns "
              f"({all_dates[0]:%Y-%m} .. {all_dates[-1]:%Y-%m}); RET_MODE={RET_MODE}")
        print(f"    {len(series)} constituent rows; index rows found: {sorted(index_rows)}")
        for key, rec in index_rows.items():
            sample = [(d, rec["ret"].get(d)) for d in all_dates[:4]]
            txt = ", ".join(f"{d:%Y-%m} {100*v:+.2f}%" for d, v in sample if v is not None)
            print(f"    [{key}] {rec['meta']['name']}: first months -> {txt}")
    return series, index_rows, all_dates


# ---------- monthly holdings (one sheet per month-end) ----------
def find_holdings_file():
    c = (list(BASE.glob("*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"))
         or list(BASE.glob("*[Hh]olding*.xlsx")))
    if not c: raise FileNotFoundError("holdings workbook not found in R2KG_BASE")
    return c[0]


def parse_sheet_month(sn):
    """Sheet names like '4.30.2026' / '3.31.2026' / '12.31.2015'."""
    m = re.search(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})", str(sn))
    if not m: return None
    y = int(m.group(3)); y += 2000 if y < 100 else 0
    return _eom(y, int(m.group(1)))


def load_monthly_holdings(path=None, verbose=True):
    """Returns {month_end_date: [ {cik,nt,ticker,name,weight,gics,ms_industry} ]}."""
    path = Path(path) if path else find_holdings_file()
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    out = {}
    for sn in wb.sheetnames:
        d = parse_sheet_month(sn)
        if d is None: continue
        ws = wb[sn]; hdr = None; idx = {}; rows = []
        for r in ws.iter_rows(values_only=True):
            if hdr is None:
                hdr = [str(c).strip() if c else "" for c in r]; idx = {h: i for i, h in enumerate(hdr)}
                if "Ticker" not in idx and "Name" not in idx: hdr = None
                continue
            def g(col):
                i = idx.get(col); return r[i] if (i is not None and i < len(r) and r[i] is not None) else None
            tk = g("Ticker")
            cikv = g("CIK")
            if tk is None and cikv is None: continue
            cik = str(int(to_f(cikv))).zfill(10) if (cikv is not None and to_f(cikv) is not None) else None
            rows.append({"cik": cik, "nt": ntk(tk), "ticker": str(tk or "").strip(),
                         "name": str(g("Name") or ""), "weight": to_f(g("Portfolio Weighting %")) or 0.0,
                         "gics": str(g("GICS Sector") or ""), "ms_industry": str(g("Morningstar Industry") or "")})
        if rows: out[d] = rows
    wb.close()
    if verbose:
        ds = sorted(out)
        print(f"  holdings file: {path.name}  ({len(out)} monthly snapshots, "
              f"{ds[0]:%Y-%m} .. {ds[-1]:%Y-%m})")
    return out
