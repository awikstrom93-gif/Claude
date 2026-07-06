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
                                  find_holdings, aggregate, dollar_agg)
from r2k_perf_io import load_monthly_holdings, ntk

BASE = Path(os.environ.get("R2KG_BASE", "."))
PANEL_CSV = BASE / "r2k_panel.csv"
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "6"))     # annual spine: snapshot nearest this month.
# Default = JUNE: 6/30 exists for every year in the quarterly holdings (April did not), it lands right
# AFTER the annual Russell reconstitution (fresh membership + growth-style reassignment), and it puts
# R2000G and S&P600G on the SAME snapshot date. Set SNAP_MONTH=4 to reproduce the legacy April cut.
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


from r2k_calc import annual_spine as _annual_spine   # single impl in the shared calc module

SNAP_DRIFT_TOL = int(os.environ.get("SNAP_DRIFT_TOL", "4"))        # drop a year whose only snapshot is >this many months from target
MIN_MEMBER_FRAC = float(os.environ.get("MIN_MEMBER_FRAC", "0.5"))  # drop a snapshot with < this fraction of the median membership


def annual_spine(holdings):
    """{year: snapshot_date} = the nearest-TARGET_MONTH snapshot per year (r2k_calc.annual_spine), with
    TWO guards so a bad snapshot can't silently enter the annual analysis (each WARNS when it fires):
      * DRIFT -- drop a year whose best snapshot is far from the target month (e.g. a lone December stub
        when the spine is June): a poor point-in-time proxy, not a real annual observation.
      * TRUNCATION -- drop a snapshot whose membership is far below the median (e.g. a partial holdings
        export of 93 names vs ~1,150): it would bias every weight-weighted metric. This is exactly the
        Russell-2013/14 truncated-sheet failure, now caught instead of silently analyzed."""
    spine = _annual_spine(holdings, TARGET_MONTH)
    if not spine:
        return spine
    counts = {y: len(holdings.get(d, [])) for y, d in spine.items()}
    med = sorted(counts.values())[len(counts) // 2] if counts else 0
    kept = {}
    for y, d in sorted(spine.items()):
        drift = abs(d.month - TARGET_MONTH)
        if drift > SNAP_DRIFT_TOL:
            print(f"  !! annual spine: dropping {y} -- snapshot {d} is {drift} months from target month "
                  f"{TARGET_MONTH} (not a valid annual point; e.g. a lone December stub).")
            continue
        if med and counts[y] < MIN_MEMBER_FRAC * med:
            print(f"  !! annual spine: dropping {y} -- snapshot {d} has only {counts[y]} members vs median "
                  f"{med}: a TRUNCATED holdings export. Re-pull that period's holdings.")
            continue
        kept[y] = d
    return kept


def canon_cik(raw):
    """Canonical int-string form of a CIK candidate (or None)."""
    if raw is None or raw == "": return None
    try: return str(int(raw))
    except (TypeError, ValueError): return str(raw)


def resolve_identity(h, tmap, nfacts, snap_dt=None, temporal=None, skey=None):
    """(cik, cf, reason).  POINT-IN-TIME by FISCAL-YEAR FRESHNESS: among the candidate CIKs for this
    ticker (base security map, holdings-file CIK, temporal map), pick the one whose pick_fy0 is the
    NEWEST fiscal year <= the snapshot -- i.e. the entity actually FILING 10-Ks at that date -- with
    base-first as the tie-break.  This tracks ticker reuse through mergers automatically: a reused
    ticker resolves to whichever issuer was alive then (CZR -> Caesars pre-2021, Eldorado-Caesars
    from 2021; WMGI -> Wright NV from 2016; ARRY -> Array Technologies once Array Biopharma is gone),
    with no hand-coded overrides.  When base==the only candidate (the ~13k normal names) nothing
    changes.  Survivorship-mapped file CIKs are harmless here -- a stale entity loses on freshness."""
    def _fy0(cf):
        if not cf:
            return None
        if snap_dt is not None:
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is not None:
                return fy0
        ys = [y for y in cf if isinstance(y, int)]
        return max(ys) if ys else None

    base_cik = canon_cik(tmap.get(h["nt"]))
    base_cf = fund_for(nfacts, base_cik)
    base_fy0 = _fy0(base_cf)

    # CONSERVATIVE: keep base unless base is DEFUNCT/stale -- its newest fiscal year is older than the
    # normal one-year reporting lag (fy0 < snapshot_year - 1), the signature of an entity that stopped
    # filing. Only THEN do we switch to a still-filing alternative. Normal names (base fy0 = Y-1) and
    # the merger-overlap years (both entities filing -> tie) are untouched, so base-first is preserved.
    snap_year = snap_dt.year if snap_dt is not None else None
    base_stale = base_fy0 is None or (snap_year is not None and base_fy0 < snap_year - 1)
    chosen_cik, chosen_cf = base_cik, base_cf
    if base_stale:
        alts = [h.get("cik")]
        if temporal is not None and skey:
            td = temporal.get(skey) or temporal.get(f"{skey[:4]}-04-30") or {}
            alts.append(td.get(h.get("ticker")) or td.get(str(h.get("ticker") or "").upper()) or td.get(h["nt"]))
        best = base_fy0 if base_fy0 is not None else -1
        for cand in alts:
            cik = canon_cik(cand)
            if not cik or cik == base_cik:
                continue
            cf = fund_for(nfacts, cik)
            fy0 = _fy0(cf)
            if fy0 is not None and fy0 > best:        # switch only to a strictly FRESHER alternative
                best, chosen_cik, chosen_cf = fy0, cik, cf
    if chosen_cik is None:
        return None, None, "no-cik-for-ticker"
    return chosen_cik, chosen_cf, ("ok" if chosen_cf else "no-fundamentals")


def is_biotech(h):
    il = (h.get("ms_industry") or "").lower()
    return any(k in il for k in BIO_KEYWORDS)


def find(pats):
    """First file in BASE matching any of the glob patterns (or None)."""
    for p in pats:
        c = list(BASE.glob(p))
        if c:
            return c[0]
    return None


def sp600g_holdings():
    return find_annual("SP600G")


def find_annual(index):
    """The ANNUAL holdings workbook (one April snapshot/yr), explicitly EXCLUDING the quarterly file
    so the annual panel never accidentally reads it now that both live in the folder. Deterministic
    (sorted), so every caller that uses this resolves the SAME file. If no dedicated annual workbook
    exists for the index, fall back to the quarterly file -- an index that ships only quarterly
    holdings (e.g. SP600G here) is then still covered; annual_spine takes the snapshot nearest April
    from whatever month-ends exist (March for a 3/6/9/12 file). This is the single holdings resolver;
    step6/8/9 and the panel all call it so they can never diverge on which file 'the index' is."""
    pats = (["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"] if index == "R2KG"
            else ["*[Ss][Pp]*600*[Gg]rowth*[Hh]olding*.xlsx", "*600*[Gg]rowth*[Hh]olding*.xlsx"])
    for p in pats:
        for c in sorted(BASE.glob(p)):
            if "quarterly" not in c.name.lower():
                return c
    return find_quarterly(index)         # graceful fallback: only-quarterly index still resolves


def find_quarterly(index):
    """The QUARTERLY holdings workbook (Mar/Jun/Sep/Dec quarter-ends + recent months)."""
    if index == "R2KG":
        return find(["*[Rr]ussell*[Qq]uarterly*[Hh]olding*.xlsx", "*[Rr]ussell*[Gg]rowth*[Qq]uarterly*.xlsx"])
    return find(["*600*[Qq]uarterly*[Hh]olding*.xlsx", "*[Ss][Pp]*600*[Qq]uarterly*.xlsx"])


QUARTER_MONTHS = (3, 6, 9, 12)          # the consistent quarter-end spine across both indices
PANEL_Q_CSV = BASE / "r2k_panel_q.csv"


# ============================ the panel (one row per snapshot x constituent) ============================
# identity columns first, then every company_metrics field.
ID_COLS = ["index", "year", "snapshot", "ticker", "nt", "name", "cik", "fy0", "weight",
           "gics", "ms_industry", "is_biotech", "covered", "reason"]
METRIC_COLS = ["revenue", "net_income", "operating_income", "gross_profit", "equity", "assets",
               "cash", "debt", "cfo", "fcf", "sector", "rev_yoy", "rev_cagr3", "ni_yoy",
               "gross_margin", "op_margin", "net_margin", "fcf_margin", "roe", "roa", "roic",
               "_nopat", "_ic", "gp_to_assets", "accruals", "cash_conversion", "asset_turnover",
               "rule_of_40", "d_to_equity", "d_to_capital", "prof_ni", "prof_oi", "p2", "p3",
               "ever_profitable", "was_profitable_prior", "cohort", "never_basis", "has_rev",
               "_aeq", "_ata",   # average equity/assets, for average-denominator ROE $agg + DuPont
               "ebitda", "interest_expense"]   # for the solvency tab -> single (panel) vintage
PANEL_COLS = ID_COLS + METRIC_COLS


def _index_rows(index, hold, snaps, nfacts, tmap, temporal=None):
    """All (snapshot x constituent) rows for one index over the given snapshot dates."""
    rows = []
    for snap in snaps:
        skey = str(snap)[:10]
        for h in hold[snap]:
            cik, cf, reason = resolve_identity(h, tmap, nfacts, snap_dt=snap, temporal=temporal, skey=skey)
            rec = {"index": index, "year": snap.year, "snapshot": str(snap)[:10],
                   "ticker": h.get("ticker", ""), "nt": h["nt"], "name": h.get("name", ""),
                   "cik": cik or "", "fy0": "", "weight": h.get("weight", 0.0) or 0.0,
                   "gics": h.get("gics", ""), "ms_industry": h.get("ms_industry", ""),
                   "is_biotech": int(is_biotech(h)), "covered": 0, "reason": reason}
            for k in METRIC_COLS:
                rec[k] = None
            if cf:
                fy0 = pick_fy0(cf, snap)
                if fy0 is None:
                    rec["reason"] = "no-fy0-before-snapshot"
                else:
                    cm = company_metrics(cf, fy0)
                    # a resolved fy0 whose fundamentals are ENTIRELY empty (no revenue, assets, equity,
                    # or net income -- e.g. a reverse-merger predecessor/stub year like AMRX 2018) is not
                    # usable data: keep it uncovered so it can't dilute coverage or feed a $0 balance
                    # sheet into the quality/leverage analytics.
                    core_vals = (cm.get("revenue"), cm.get("assets"), cm.get("equity"), cm.get("net_income"))
                    if all(v is None or v == 0 for v in core_vals):
                        rec["reason"] = "empty-fundamentals"
                    else:
                        rec["fy0"], rec["covered"] = fy0, 1
                        for k in METRIC_COLS:
                            rec[k] = cm.get(k)
            rows.append(rec)
    return rows


def build_panel(verbose=True):
    """Build the canonical panel: one row per (index x snapshot x constituent), covered or not.
    R2000G always; S&P 600 Growth too if its holdings workbook is present. Uncovered names are
    kept (metrics blank, reason set) so the panel is the FULL historical ledger for each index."""
    nfacts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    rows = []
    for index, path in (("R2KG", find_annual("R2KG") or find_holdings()), ("SP600G", find_annual("SP600G"))):
        if not path:
            continue
        if verbose and "quarterly" in path.name.lower():
            print(f"  [{index}] no annual holdings file -- falling back to the QUARTERLY workbook "
                  f"({path.name}); annual spine = snapshot nearest April (March for a 3/6/9/12 file)")
        hold = load_monthly_holdings(path, verbose=False)
        spine = annual_spine(hold)
        rows += _index_rows(index, hold, sorted(spine.values()), nfacts, tmap, temporal)
    if verbose:
        _report_coverage(rows, "constituent-years")
        if not any(r["index"] == "SP600G" for r in rows):
            print("  (no S&P 600 Growth holdings file found -- panel is R2000G-only)")
    return rows


def _report_coverage(rows, unit):
    for idx in ("R2KG", "SP600G"):
        ir = [r for r in rows if r["index"] == idx]
        if ir:
            nc = sum(r["covered"] for r in ir)
            print(f"  panel[{idx}]: {len(ir)} {unit}, {nc} covered ({100*nc/len(ir):.1f}%)")


def build_quarterly_panel(verbose=True):
    """Quarter-end panel (Mar/Jun/Sep/Dec) from the QUARTERLY holdings files, for the timing-sensitive
    exhibits (valuation, factor spreads). Same base-first identity -- the files' survivorship-mapped
    CIK column is ignored. Cached separately so it never disturbs the annual panel."""
    nfacts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    rows = []
    for index in ("R2KG", "SP600G"):
        path = find_quarterly(index)
        if not path:
            continue
        hold = load_monthly_holdings(path, verbose=False)
        snaps = sorted(d for d in hold if d.month in QUARTER_MONTHS)
        rows += _index_rows(index, hold, snaps, nfacts, tmap, temporal)
    if verbose:
        if rows:
            nq = len({(r["index"], r["snapshot"]) for r in rows})
            print(f"  quarterly panel: {len(rows)} rows over {nq} index-quarters")
            _report_coverage(rows, "constituent-quarters")
        else:
            print("  (no quarterly holdings files found -- run needs *Quarterly*Holdings*.xlsx)")
    return rows


def get_quarterly_panel(index="R2KG"):
    """Load the cached quarterly panel, building it if absent. index=None for all rows."""
    if PANEL_Q_CSV.exists():
        _warn_if_stale(PANEL_Q_CSV, "R2KG")
        rows = load_panel(PANEL_Q_CSV)
    else:
        rows = build_quarterly_panel(); write_panel(rows, PANEL_Q_CSV)
    return rows if index is None else [r for r in rows if r["index"] == index]


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
            r["index"] = r.get("index") or "R2KG"   # old caches without the column are R2000G
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


# ============================ index-level quality reducer (one definition) ============================
def by_index_year(rows):
    """{(index, year): [member rows]} -- every constituent row (covered or not) for that snapshot."""
    out = {}
    for r in rows:
        out.setdefault((r["index"], int(r["year"])), []).append(r)
    return out


def year_snapshot(rows):
    """{year: snapshot-date string} for a single-index panel -- the ACTUAL annual snapshot date used
    each year (e.g. 2016-06-30 under SNAP_MONTH=6), so views label rows with the real date instead of a
    hardcoded month. All rows in a year share one snapshot (annual_spine picks one per index-year)."""
    out = {}
    for r in rows:
        y = int(r["year"])
        if y not in out and r.get("snapshot"):
            out[y] = r["snapshot"]
    return out


def index_quality(members, tops=(10, 25, 50)):
    """Reproduce step6's snapshot_quality() dict from panel rows: `members` is every constituent row
    for one (index, year); the covered subset drives the quality metrics, all members drive
    concentration/sector. Percent fields are already x100; margins/returns are FRACTIONS (as step6)."""
    cov = [r for r in members if r["covered"]]
    wt_all = sum(r["weight"] for r in members)
    sect = {}
    for r in members:
        g = r["gics"] or "Unknown"
        sect[g] = sect.get(g, 0.0) + r["weight"]
    out = {"n_members": len(members), "n_cov": len(cov), "wt_all": wt_all,
           "wt_cov": sum(r["weight"] for r in cov), "sectors": sect}
    ws = sorted((r["weight"] for r in members), reverse=True)
    tw = sum(ws) or 1e-9
    shares = [w / tw for w in ws]
    out["topn"] = {n: round(sum(ws[:n]) / tw * 100, 1) for n in tops}
    out["hhi"] = round(sum((s * 100) ** 2 for s in shares), 1)
    out["effn"] = round(1 / sum(s * s for s in shares), 1) if shares else None
    if not cov:
        return out
    colk = lambda k: [(r[k], r["weight"]) for r in cov]
    ag = lambda k: aggregate(colk(k), winsor=True)
    wtot = sum(r["weight"] for r in cov) or 1.0

    def upct(flag):
        cls = [r for r in cov if r[flag] is not None]
        if not cls:
            return None
        return 100 * sum(r["weight"] for r in cls if r[flag] is False) / (sum(r["weight"] for r in cls) or 1)

    out.update(
        unprof_ni=upct("prof_ni"), unprof_oi=upct("prof_oi"),
        no_rev=100 * sum(r["weight"] for r in cov if not r["has_rev"]) / wtot,
        w_prof=100 * sum(r["weight"] for r in cov if r["cohort"] == "profitable") / wtot,
        w_fallen=100 * sum(r["weight"] for r in cov if r["cohort"] == "fallen") / wtot,
        w_never=100 * sum(r["weight"] for r in cov if r["cohort"] == "never_profitable") / wtot,
        gross_m=ag("gross_margin")["wavg"], op_m=ag("op_margin")["wavg"], net_m=ag("net_margin")["wavg"],
        op_da=dollar_agg([(r["operating_income"], r["revenue"]) for r in cov]),
        net_da=dollar_agg([(r["net_income"], r["revenue"]) for r in cov]),
        gross_da=dollar_agg([(r["gross_profit"], r["revenue"]) for r in cov]),
        roe_w=ag("roe")["wavg"],   # ROE $agg on AVERAGE equity (matches per-name ROE, ROIC $agg, the views)
        roe_da=dollar_agg([(r["net_income"], r["_aeq"] if r.get("_aeq") is not None else r["equity"]) for r in cov]),
        roic_w=ag("roic")["wavg"], roic_da=dollar_agg([(r["_nopat"], r["_ic"]) for r in cov]),
        gp_assets=ag("gp_to_assets")["median"], accruals=ag("accruals")["median"],
        cashconv=ag("cash_conversion")["median"],
        rev_yoy=ag("rev_yoy")["wavg"], rev_cagr3=ag("rev_cagr3")["median"], rule40=ag("rule_of_40")["median"],
        de_w=ag("d_to_equity")["wavg"], dcap_w=ag("d_to_capital")["wavg"],
        tot_rev=sum(r["revenue"] for r in cov if r["revenue"]) / 1e9,
    )
    return out


# ============================ shared view helpers (one copy, used by every view module) ============================
ANALYTICS = BASE / "Russell2000Growth_Analytics.xlsx"


def _warn_if_stale(panel_path, holdings_index):
    """Warn (once) if a cached panel is older than its inputs, so a standalone view never silently
    projects a stale panel. The full r2k_report.py run always rebuilds, so this only bites ad-hoc
    single-tab runs -- exactly where staleness used to hide. Checks the ACTUAL panel input
    (edgar_annual_fundamentals_ASFILED.csv, what load_fundamentals reads) and the holdings file; and
    separately flags the as-filed export being older than the engine output (fundamentals_dera.csv),
    which means r2k_dera_to_fundamentals.py wasn't re-run after the last classify."""
    try:
        if not panel_path.exists():
            return
        pm = panel_path.stat().st_mtime
        ins = []
        asfiled = BASE / "edgar_annual_fundamentals_ASFILED.csv"   # the panel's actual fundamentals source
        if asfiled.exists():
            ins.append(asfiled)
        hf = find_annual(holdings_index) if panel_path == PANEL_CSV else find_quarterly(holdings_index)
        if hf:
            ins.append(hf)
        newer = [p.name for p in ins if p.stat().st_mtime > pm + 1]
        if newer:
            print(f"  !! {panel_path.name} is STALE (older than {', '.join(newer)}) -- "
                  f"rebuild with `python r2k_universe.py{'' if panel_path == PANEL_CSV else ' --quarterly'}` "
                  f"or run r2k_report.py before trusting this tab.")
        # as-filed export not regenerated after the last engine run -> panel fundamentals lag the engine,
        # and the solvency tab (which reads fundamentals_dera.csv directly) is on a newer vintage.
        dera = BASE / "fundamentals_dera.csv"
        if asfiled.exists() and dera.exists() and dera.stat().st_mtime > asfiled.stat().st_mtime + 1:
            print(f"  !! edgar_annual_fundamentals_ASFILED.csv is OLDER than fundamentals_dera.csv -- "
                  f"re-run r2k_dera_to_fundamentals.py so the panel matches the latest engine (and the "
                  f"solvency tab's vintage).")
    except Exception:
        pass


def get_panel(index="R2KG"):
    """Load the cached panel, building+caching it if absent. The single entry point views use.
    Defaults to R2000G rows only (so existing R2KG views are unaffected by adding SP600G to the
    panel); pass index=None for ALL rows, or index="SP600G" for the comparison universe."""
    if PANEL_CSV.exists():
        _warn_if_stale(PANEL_CSV, "R2KG")
        rows = load_panel()
    else:
        rows = build_panel(); write_panel(rows)
    return rows if index is None else [r for r in rows if r["index"] == index]


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
    import sys
    if "--quarterly" in sys.argv:
        rows = build_quarterly_panel(verbose=True)
        write_panel(rows, PANEL_Q_CSV)
        print(f"  -> {PANEL_Q_CSV.name}")
        return
    rows = build_panel(verbose=True)
    write_panel(rows)
    print(f"  -> {PANEL_CSV.name}")
    r2kg = [r for r in rows if r["index"] == "R2KG"]
    print("\n  VERIFY -- R2000G Quality Trends headline straight off the panel (base-first identity):")
    print(f"    {'year':<6}{'TotRev$B':>10}{'TotNI$B':>10}{'%Unprof':>9}{'covered':>9}")
    for y, rev, ni, unprof, n in quality_trends_from_panel(r2kg):
        flag = "  <- 2016 NI should be ~13.2 (was 6.5 under the legacy temporal-first bug)" if y == 2016 else ""
        print(f"    {y:<6}{rev:>10.1f}{ni:>10.1f}{unprof:>9.1f}{n:>9}{flag}")


if __name__ == "__main__":
    main()
