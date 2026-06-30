"""
r2k_universe.py  --  THE single source of truth for the R2000G report's universe, identity, and
per-constituent metrics.  Everything downstream (quality trends, cohorts, concentration, biotech,
SP600G comparison, data reliability, benchmark reconcile) is a PROJECTION of the one panel this
module builds -- so the steps can never again disagree on who is in the index, which CIK a ticker
maps to, which fiscal year is used, or what a name's metrics are.

LAYERS
  1. Engine (unchanged): dera_classify -> edgar_annual_fundamentals_ASFILED.csv
  2. Universe/identity (HERE): one holdings loader, one CIK resolver, one annual spine, one fy0,
     one company_metrics.  The copy-pasted norm_facts/ticker_cik_map/fund_for/annual_spine that
     had drifted across steps 5/6/8/9 are consolidated here and imported everywhere.
  3. Panel (HERE): build_panel() -> one row per (snapshot x constituent) with identity + every
     metric + coverage reason.  Cached to r2k_panel.csv so it is inspectable and reusable.
  4/5. Views + assembly: thin projections of the panel (ported incrementally).

IDENTITY -- THE FIX
  The R2000G holdings file carries NO CIK column (verified: 0% populated, all years), so identity
  is resolved by ticker.  We resolve BASE-FIRST: security_cik_map.json wins, the temporal map only
  fills tickers the base map lacks.  This matches steps 5/6/8/9 and is correct on every material
  name (e.g. CZR 2016 -> 858339 Caesars Entertainment Corp, not the modern 1590895).  Legacy step3
  used temporal-FIRST, and temporal_cik_map.json holds present-day CIKs for historical snapshots,
  which is what silently corrupted step3's NI series (~$28B moved across 69 names, $5.8B on CZR'16).

RUN:  python r2k_universe.py            # builds the panel, writes r2k_panel.csv, prints verification
"""
import os, csv
from pathlib import Path

# --- reuse the proven heavy logic verbatim (no reimplementation of the accounting/metrics math) ---
from r2k_step3_analytics import (load_fundamentals, load_maps, pick_fy0, company_metrics,
                                  find_holdings)
from r2k_perf_io import load_monthly_holdings, ntk

BASE = Path(os.environ.get("R2KG_BASE", "."))
PANEL_CSV = BASE / "r2k_panel.csv"
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "4"))     # annual spine: snapshot nearest this month
BIO_KEYWORDS = [k.strip().lower() for k in os.environ.get("BIOTECH_KEYWORDS", "biotech").split(",") if k.strip()]


# ============================ universe / identity (one definition) ============================
def norm_facts(facts):
    """Re-key fundamentals by canonical int-string CIK so any zero-padding matches."""
    out = {}
    for k, v in facts.items():
        try: out[str(int(k))] = v
        except (TypeError, ValueError): out[str(k)] = v
    return out


def fund_for(nf, cik):
    if not cik: return None
    try: return nf.get(str(int(cik)))
    except (TypeError, ValueError): return nf.get(str(cik))


def ticker_cik_map(base, temporal):
    """ntk(ticker) -> CIK, BASE-FIRST: the security map wins; the temporal map only fills tickers
    the base map lacks.  Holdings rows without a CIK column are resolved by ticker through this."""
    tmap = dict(base)
    for _, d in (temporal or {}).items():
        for tk, c in d.items():
            if c: tmap.setdefault(ntk(tk), str(c))
    return tmap


def annual_spine(holdings):
    """{year: snapshot_date nearest TARGET_MONTH}."""
    out = {}
    for d in sorted(holdings):
        cur = out.get(d.year)
        if cur is None or abs(d.month - TARGET_MONTH) < abs(cur.month - TARGET_MONTH):
            out[d.year] = d
    return out


def canon_cik(raw):
    """Canonical int-string form of a CIK candidate (or None)."""
    if raw is None or raw == "": return None
    try: return str(int(raw))
    except (TypeError, ValueError): return str(raw)


def resolve_identity(h, tmap, nfacts):
    """(cik, cf, reason).  Prefer the holdings row's own CIK if present (forward-compatible),
    else base-first ticker resolution.  cf is the per-company fundamentals dict, or None."""
    raw = h.get("cik") or tmap.get(h["nt"])
    cik = canon_cik(raw)
    cf = fund_for(nfacts, cik)
    if not raw:   reason = "no-cik-for-ticker"
    elif not cf:  reason = "no-fundamentals"
    else:         reason = "ok"
    return cik, cf, reason


def is_biotech(h):
    il = (h.get("ms_industry") or "").lower()
    return any(k in il for k in BIO_KEYWORDS)


# ============================ the panel (one row per snapshot x constituent) ============================
# identity columns first, then every company_metrics field.
ID_COLS = ["year", "snapshot", "ticker", "nt", "name", "cik", "fy0", "weight",
           "gics", "ms_industry", "is_biotech", "covered", "reason"]
METRIC_COLS = ["revenue", "net_income", "operating_income", "gross_profit", "equity", "assets",
               "cash", "debt", "cfo", "fcf", "sector", "rev_yoy", "rev_cagr3", "ni_yoy",
               "gross_margin", "op_margin", "net_margin", "fcf_margin", "roe", "roa", "roic",
               "_nopat", "_ic", "gp_to_assets", "accruals", "cash_conversion", "asset_turnover",
               "rule_of_40", "d_to_equity", "d_to_capital", "prof_ni", "prof_oi", "p2", "p3",
               "ever_profitable", "was_profitable_prior", "cohort", "never_basis", "has_rev"]
PANEL_COLS = ID_COLS + METRIC_COLS


def build_panel(verbose=True):
    """Build the canonical panel: list of dicts, one per (snapshot x constituent), covered or not.
    Uncovered names are kept (metrics blank, reason set) so the panel is the FULL historical ledger."""
    nfacts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    hold = load_monthly_holdings(find_holdings(), verbose=False)
    spine = annual_spine(hold)
    rows = []
    for year in sorted(spine):
        snap = spine[year]
        for h in hold[snap]:
            cik, cf, reason = resolve_identity(h, tmap, nfacts)
            rec = {"year": year, "snapshot": str(snap)[:10], "ticker": h.get("ticker", ""),
                   "nt": h["nt"], "name": h.get("name", ""), "cik": cik or "", "fy0": "",
                   "weight": h.get("weight", 0.0) or 0.0, "gics": h.get("gics", ""),
                   "ms_industry": h.get("ms_industry", ""), "is_biotech": int(is_biotech(h)),
                   "covered": 0, "reason": reason}
            for k in METRIC_COLS:
                rec[k] = None
            if cf:
                fy0 = pick_fy0(cf, snap)
                if fy0 is None:
                    rec["reason"] = "no-fy0-before-snapshot"
                else:
                    cm = company_metrics(cf, fy0)
                    rec["fy0"] = fy0
                    rec["covered"] = 1
                    for k in METRIC_COLS:
                        rec[k] = cm.get(k)
            rows.append(rec)
    if verbose:
        ny = len(spine); ncov = sum(r["covered"] for r in rows)
        print(f"  panel: {len(rows)} constituent-years across {ny} snapshots, {ncov} covered "
              f"({100*ncov/len(rows):.1f}%)")
    return rows


def write_panel(rows, path=PANEL_CSV):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PANEL_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return path


def _to_num(x):
    if x is None or x == "": return None
    try: return float(x)
    except (TypeError, ValueError): return x


STR_COLS = {"sector", "cohort", "never_basis"}
BOOL_COLS = {"prof_ni", "prof_oi", "p2", "p3", "ever_profitable", "was_profitable_prior", "has_rev"}


def _to_bool(x):
    if x is None or x == "": return None
    return str(x).strip().lower() in ("true", "1", "1.0", "yes")


def load_panel(path=PANEL_CSV):
    """Read the cached panel back, coercing numeric/boolean columns. Use this in view modules so a
    tab is a pure projection of the panel and never re-loads holdings/fundamentals itself."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k in METRIC_COLS + ["weight"]:
                if k in STR_COLS:                       # keep these as strings
                    continue
                r[k] = _to_bool(r.get(k)) if k in BOOL_COLS else _to_num(r.get(k))
            yr = _to_num(r.get("year"))
            r["year"] = int(yr) if yr is not None else None
            r["fy0"] = int(_to_num(r["fy0"])) if r.get("fy0") not in (None, "") else None
            r["covered"] = int(_to_num(r.get("covered")) or 0)
            r["is_biotech"] = int(_to_num(r.get("is_biotech")) or 0)
            rows.append(r)
    return rows


# ============================ verification (keystone proof) ============================
def _dollar_b(rows, key):
    return sum(r[key] for r in rows if r.get(key)) / 1e9


def quality_trends_from_panel(rows):
    """Reproduce the headline Quality-Trends aggregate FROM the panel, to prove the keystone is
    correct (2016 Total NI must now be ~13.2, not the legacy 6.5)."""
    years = sorted({r["year"] for r in rows})
    out = []
    for y in years:
        cov = [r for r in rows if r["year"] == y and r["covered"]]
        wt = sum(r["weight"] for r in cov) or 1.0
        cls_ni = [r for r in cov if r["prof_ni"] is not None]
        unprof = 100 * sum(r["weight"] for r in cls_ni if not r["prof_ni"]) / (sum(r["weight"] for r in cls_ni) or 1.0)
        out.append((y, _dollar_b(cov, "revenue"), _dollar_b(cov, "net_income"), unprof, len(cov)))
    return out


# ============================ shared view helpers (one copy, used by every view module) ============================
ANALYTICS = BASE / "Russell2000Growth_Analytics.xlsx"


def get_panel():
    """Load the cached panel, building+caching it if absent. The single entry point views use."""
    if PANEL_CSV.exists():
        return load_panel()
    rows = build_panel(); write_panel(rows)
    return rows


def diff_sheet(sheet, hdr, rows, src=ANALYTICS, tol=0.05, keycols=1):
    """Cell-by-cell diff of panel-derived rows vs the current workbook sheet. The row key is the
    first `keycols` columns joined (use keycols=2 for sheets with two rows per period, e.g.
    Composition Change). Numeric cells within `tol` are equal. Returns the change count."""
    if not src.exists():
        print(f"  (no {src.name} to diff against -- skipping)")
        return None
    import openpyxl
    ws = openpyxl.load_workbook(src, data_only=True)[sheet]

    def mk(vals):
        return "|".join(str(v)[:14] for v in vals[:keycols])

    old = {}
    for r in range(4, ws.max_row + 1):
        cells = [ws.cell(r, c).value for c in range(1, len(hdr) + 1)]
        if cells[0]:
            old[mk(cells)] = cells
    print(f"\n  DIFF vs current {src.name} :: {sheet}")
    nd = 0
    for row in rows:
        key = mk(row)
        o = old.get(key)
        if not o:
            print(f"    {key}  (not in current sheet)"); continue
        for c in range(keycols, len(hdr)):
            nv, ov = row[c], o[c]
            if nv is None and ov is None:
                continue
            try:
                if ov is not None and nv is not None and abs(float(nv) - float(ov)) < tol:
                    continue
            except (TypeError, ValueError):
                if nv == ov:
                    continue
            print(f"    {key:<22} {hdr[c]:<22} current={ov!s:<10} -> panel={nv!s:<10}")
            nd += 1
    print(f"  {nd} cell(s) changed." if nd else "  identical.")
    return nd


def print_sheet(hdr, rows, ncols=5):
    print("    " + "".join(str(h)[:8].rjust(9) for h in hdr[:ncols]))
    for row in rows:
        print("    " + "".join(str(v).rjust(9) for v in row[:ncols]))


def main():
    rows = build_panel(verbose=True)
    write_panel(rows)
    print(f"  -> {PANEL_CSV.name}")
    print("\n  VERIFY -- Quality Trends headline straight off the panel (base-first identity):")
    print(f"    {'year':<6}{'TotRev$B':>10}{'TotNI$B':>10}{'%Unprof':>9}{'covered':>9}")
    for y, rev, ni, unprof, n in quality_trends_from_panel(rows):
        flag = "  <- 2016 NI should be ~13.2 (was 6.5 under the legacy temporal-first bug)" if y == 2016 else ""
        print(f"    {y:<6}{rev:>10.1f}{ni:>10.1f}{unprof:>9.1f}{n:>9}{flag}")


if __name__ == "__main__":
    main()
