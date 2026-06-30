"""
r2k_factor_spreads.py  --  DID QUALITY PAY INSIDE R2000G?  The keystone evidence the report was
missing: it joins each constituent's as-filed quality factor (from the panel) to its FORWARD 12-month
return (from the performance file) and measures the long-short quintile spread per factor, per year
and cumulative.  No look-ahead: the factor is known at the April snapshot; the return is earned over
the following 12 months (May→Apr).

Factors (quintiles, equal-weighted; top-quality minus bottom-quality):
  ROIC, GP/Assets (Novy-Marx), Accruals (low=quality), Net margin, FCF margin, and the
  Profitable-minus-Never-profitable cohort spread.

INPUTS  r2k_panel.csv (quality) + the Morningstar performance workbook (returns).
RUN:    python r2k_factor_spreads.py
"""
import statistics
from datetime import date

from r2k_universe import get_panel, get_quarterly_panel, BASE
from r2k_perf_io import load_performance, ntk

OUT = BASE / "r2k_factor_spreads.txt"

# (label, panel field, direction): +1 high value = higher quality, -1 low value = higher quality
FACTORS = [("ROIC", "roic", +1), ("GP/Assets", "gp_to_assets", +1),
           ("Accruals(low=Q)", "accruals", -1), ("NetMargin", "net_margin", +1),
           ("FCFMargin", "fcf_margin", +1)]
MIN_N = 50          # need a reasonable cross-section to form quintiles
WIN_LO, WIN_HI = 0.01, 0.99   # winsorize forward returns per year (tame micro-cap lottery tails)
NAME_CAP = 0.05     # within a quintile, cap any one name at 5% of quintile weight (no single-name domination)


def _ck(c):
    s = str(c)
    return str(int(s)) if s.isdigit() else s


def _parse_snap(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fwd_return(rec, window):
    """Compound a constituent's periodic returns over the forward window; None if too sparse."""
    rs = [rec["ret"].get(d) for d in window]
    present = [r for r in rs if r is not None]
    if len(present) < min(len(window), max(6, len(window) - 3)):   # 9-of-12 annual, 3-of-3 quarterly
        return None
    g = 1.0
    for r in present:
        g *= (1 + r)
    return g - 1.0


def _winsorizer(vals):
    """Return f(v) clamping to the [WIN_LO, WIN_HI] cross-sectional percentiles of vals."""
    xs = sorted(v for v in vals if v is not None)
    if len(xs) < 20:
        return lambda v: v
    n = len(xs)
    plo = xs[max(0, int(round(WIN_LO * (n - 1))))]
    phi = xs[min(n - 1, int(round(WIN_HI * (n - 1))))]
    return lambda v: None if v is None else max(plo, min(phi, v))


def _wquintile_spread(triples, direction):
    """triples: [(factor_value, index_weight, fwd_return)]. Top-quality minus bottom-quality quintile,
    quintiles split by name COUNT, returns CAP-WEIGHTED within each quintile (index-representative)."""
    pts = [(v * direction, w or 0.0, r) for v, w, r in triples if v is not None and r is not None]
    if len(pts) < MIN_N:
        return None
    pts.sort(key=lambda x: x[0])
    q = len(pts) // 5
    if q < 5:
        return None

    def wavg(grp):
        tw = sum(w for _, w, _ in grp) or 1e-9
        cap = NAME_CAP * tw                       # no single name owns more than 5% of the quintile
        cws = [min(w, cap) for _, w, _ in grp]
        tcw = sum(cws) or 1e-9
        return sum(cw * r for cw, (_, _, r) in zip(cws, grp)) / tcw
    return wavg(pts[-q:]) - wavg(pts[:q])


def _composite_z(rows):
    """{cik: composite quality z-score} -- mean of per-factor cross-sectional z (clipped ±3, signed by
    direction). Needs >=3 of the factors present for a name."""
    acc, cnt = {}, {}
    for _label, field, direction in FACTORS:
        vals = [(r["cik"], r.get(field)) for r in rows if r.get(field) is not None]
        xs = [v for _, v in vals]
        if len(xs) < 20:
            continue
        m = statistics.mean(xs)
        sd = statistics.pstdev(xs) or 1e-9
        for cik, v in vals:
            z = max(-3.0, min(3.0, (v - m) / sd)) * direction
            acc[cik] = acc.get(cik, 0.0) + z
            cnt[cik] = cnt.get(cik, 0) + 1
    return {c: acc[c] / cnt[c] for c in acc if cnt[c] >= 3}


def compute(panel, series, index_rows, all_dates, fwd_months=12, by_quarter=False):
    by_cik, by_nt = {}, {}
    for rec in series:
        m = rec["meta"]
        if m.get("cik"):
            by_cik[_ck(m["cik"])] = rec
        if m.get("nt"):
            by_nt.setdefault(m["nt"], rec)
    ridx = index_rows.get("R2KG") or index_rows.get("R2000G")

    if by_quarter:                                 # group by quarter-end snapshot date
        keys = sorted({r["snapshot"] for r in panel if r["covered"]})
        rows_of = lambda k: [r for r in panel if r["snapshot"] == k and r["covered"]]
    else:                                          # group by snapshot YEAR (annual; one snap/yr)
        keys = sorted({int(r["year"]) for r in panel})
        rows_of = lambda k: [r for r in panel if int(r["year"]) == k and r["covered"]]
    min_window = max(2, fwd_months - 2)
    per_year = {}
    for y in keys:
        rows = rows_of(y)
        d0 = _parse_snap(rows[0]["snapshot"]) if rows else None
        if d0 is None:
            continue
        window = [d for d in all_dates if d > d0][:fwd_months]
        if len(window) < min_window:               # incomplete forward window (e.g. final snapshot)
            continue
        idx_fwd = _fwd_return(ridx, window) if ridx else None
        wt = {r["cik"]: r["weight"] for r in rows}
        # forward returns, then winsorize cross-sectionally for the year
        raw = {}
        for r in rows:
            rec = by_cik.get(_ck(r["cik"])) or by_nt.get(r["nt"])
            if rec:
                fr = _fwd_return(rec, window)
                if fr is not None:
                    raw[r["cik"]] = fr
        wz = _winsorizer(list(raw.values()))
        fwd = {c: wz(v) for c, v in raw.items()}
        # single-factor spreads (cap-weighted quintiles)
        fac = {}
        for label, field, direction in FACTORS:
            triples = [(r.get(field), wt.get(r["cik"]), fwd.get(r["cik"])) for r in rows if r["cik"] in fwd]
            fac[label] = _wquintile_spread(triples, direction)
        # composite quality score spread
        comp_z = _composite_z(rows)
        ctrip = [(comp_z.get(r["cik"]), wt.get(r["cik"]), fwd.get(r["cik"]))
                 for r in rows if r["cik"] in fwd and r["cik"] in comp_z]
        comp = _wquintile_spread(ctrip, +1)
        # cohort spread: profitable minus never (cap-weighted)
        def _wavg(names):
            tw = sum(wt.get(c, 0.0) for c in names) or 1e-9
            return sum(wt.get(c, 0.0) * fwd[c] for c in names) / tw
        prof = [r["cik"] for r in rows if r["cik"] in fwd and r["cohort"] == "profitable"]
        nev = [r["cik"] for r in rows if r["cik"] in fwd and r["cohort"] == "never_profitable"]
        coh = (_wavg(prof) - _wavg(nev)) if (len(prof) >= 10 and len(nev) >= 10) else None
        per_year[y] = {"idx": idx_fwd, "fac": fac, "comp": comp, "coh": coh, "n": len(fwd)}
    return [k for k in keys if k in per_year], per_year


def _summ(vals):
    """(mean, hit-rate %, cumulative compounded, median) over the non-None annual spreads.
    The median is the outlier-immune 'typical year' (mean/cumulative are hostage to extreme years)."""
    xs = [v for v in vals if v is not None]
    if not xs:
        return None, None, None, None
    mean = sum(xs) / len(xs)
    hit = 100 * sum(1 for v in xs if v > 0) / len(xs)
    cum = 1.0
    for v in xs:
        cum *= (1 + v)
    return mean, hit, cum - 1.0, statistics.median(xs)


def _val(d, lab):
    if lab == "Composite":
        return d["comp"]
    if lab == "Prof-Never":
        return d["coh"]
    return d["fac"].get(lab)


LABELS = [f[0] for f in FACTORS] + ["Composite", "Prof-Never"]


def _plabel(k, by_quarter):
    return str(k) if by_quarter else f"{k}->{k+1}"


def build(by_quarter=False):
    """(keys, per, summary, by_quarter). Annual: years, forward-12m. Quarterly: quarter-end
    snapshots, forward-3m (non-overlapping, so the cumulative compounds correctly) -- 4x the
    observations, which robustifies the median/cumulative against any single extreme window."""
    panel = get_quarterly_panel(index="R2KG") if by_quarter else get_panel(index="R2KG")
    fwd = 3 if by_quarter else 12
    series, index_rows, all_dates = load_performance(verbose=False)
    keys, per = compute(panel, series, index_rows, all_dates, fwd_months=fwd, by_quarter=by_quarter)
    summary = {lab: _summ([_val(per[k], lab) for k in keys]) for lab in LABELS}
    return keys, per, summary, by_quarter


def write_sheet(wb, by_quarter=True):
    """Add the 'Quality Factor Spreads' tab to an openpyxl workbook (loads its own panel + returns).
    Defaults to the QUARTERLY (forward-3m, quarter-end) cut: it is robust to a single fiscal year's
    melt-up, where the annual forward-12m cut throws a -100% outlier (the 2025->2026 GP/Assets row).
    Pass by_quarter=False for the annual diagnostic cut."""
    from openpyxl.styles import Font, PatternFill, Alignment
    yy, per_year, summary, bq = build(by_quarter=by_quarter)
    if not yy and by_quarter:                      # no quarterly panel yet -> annual cut beats no tab
        yy, per_year, summary, bq = build(by_quarter=False)
    if not yy:
        return None
    ws = wb.create_sheet("Quality Factor Spreads")
    span = "Forward-3m, quarter-end" if bq else "Forward-12m, April-snapshot"
    ws.cell(1, 1, f"Did quality pay inside R2000G?  {span} long-short quintile spreads (%)").font = Font(bold=True, size=12)
    cadence_note = ("Quarter-end snapshots, return earned the next 3 months -- the ROBUST cut: a single "
                    "fiscal year's melt-up can't dominate a 40+ observation series the way it does the "
                    "annual one (see the median row)." if bq else
                    "April snapshot, return earned the next 12 months -- the ANNUAL cut (quarterly holdings "
                    "absent); with ~10 observations a single melt-up year (2026) swings the mean, so read "
                    "the MEDIAN row.")
    ws.cell(2, 1, "Top-quality minus bottom-quality quintile, cap-weighted within quintile (each name "
                  "capped at 5% of quintile weight), forward returns winsorized at 1/99 pct. No look-ahead. "
                  "Composite = mean of z-scored factors (most stable). Quality is defensive: positive in "
                  "busts (2016, 2022), negative in melt-ups. " + cadence_note).font = Font(size=9, italic=True, color="555555")
    head = [("Forward quarter" if bq else "Forward year"), "Index R%"] + LABELS
    HDR = PatternFill("solid", fgColor="1F4E5F")
    for c, h in enumerate(head, 1):
        x = ws.cell(4, c, h); x.fill = HDR; x.font = Font(bold=True, color="FFFFFF", size=10)
        x.alignment = Alignment(horizontal="center", wrap_text=True)
    pos = PatternFill("solid", fgColor="E2EFDA"); neg = PatternFill("solid", fgColor="FCE4D6")
    r = 5
    for y in yy:
        d = per_year[y]
        ws.cell(r, 1, _plabel(y, bq))
        ws.cell(r, 2, round(100 * d["idx"], 1) if d["idx"] is not None else None)
        for c, lab in enumerate(LABELS, 3):
            v = _val(d, lab)
            cell = ws.cell(r, c, round(100 * v, 1) if v is not None else None)
            if v is not None:
                cell.fill = pos if v > 0 else neg
        r += 1
    r += 1
    per = "qtr" if by_quarter else "yr"
    for name, agg in ((f"mean / {per}", 0), (f"median / {per}", 3), ("hit-rate %", 1), ("cumulative", 2)):
        ws.cell(r, 1, name).font = Font(bold=True)
        for c, lab in enumerate(LABELS, 3):
            m = summary[lab][agg]
            ws.cell(r, c, round(m if agg == 1 else 100 * m, 1) if m is not None else None).font = Font(bold=True)
        r += 1
    ws.freeze_panes = "C5"
    for col, w in enumerate([13, 9] + [13] * len(LABELS), 1):
        ws.column_dimensions[chr(64 + col)].width = w
    return "Quality Factor Spreads"


def main():
    import sys
    bq = "--quarterly" in sys.argv
    yy, per_year, summary, _ = build(by_quarter=bq)
    labels = LABELS
    fwdtxt = "forward 3m, quarterly rebalance" if bq else "forward 12m, May->Apr"
    plw = 12 if bq else 9

    def cell(v):
        return f"{100*v:>16.1f}" if v is not None else f"{'·':>16}"

    L = [f"QUALITY-FACTOR RETURN SPREADS  --  did quality pay inside R2000G? ({fwdtxt})",
         "  long-short = top-quality minus bottom-quality quintile, CAP-WEIGHTED within quintile,",
         "  forward returns winsorized at 1/99 pct (index-representative; tames micro-cap lottery tails). %", ""]
    L.append("  " + f"{'period':<{plw}}{'IndexR%':>9}" + "".join(f"{lab:>16}" for lab in labels) + f"{'n':>7}")
    L.append("  " + "-" * (9 + plw + 16 * len(labels) + 7))
    for y in yy:
        d = per_year[y]
        idx = f"{100*d['idx']:>9.1f}" if d["idx"] is not None else f"{'·':>9}"
        L.append(f"  {_plabel(y, bq):<{plw}}{idx}" + "".join(cell(_val(d, lab)) for lab in labels) + f"{d['n']:>7}")
    L.append("  " + "-" * (9 + plw + 16 * len(labels) + 7))
    for name, agg in (("mean/yr", 0), ("median/yr", 3), ("hit-rate%", 1), ("cumulative", 2)):
        cells = []
        for lab in labels:
            m = _summ([_val(per_year[y], lab) for y in yy])[agg]
            cells.append((f"{m:>16.1f}" if agg == 1 else cell(m)) if m is not None else f"{'·':>16}")
        L.append(f"  {name:<18}" + "".join(cells))
    L.append("")
    L.append("  READ: positive spread = HIGH-quality names outperformed LOW-quality over the next window.")
    L.append("  Quality here is DEFENSIVE/anti-bubble: it protects hard in busts (2022, 2016) and lags in")
    L.append("  melt-ups (2018, 2021, 2026). 'Composite' = mean of z-scored factors (the most stable");
    L.append("  signal); 'Prof-Never' = profitable minus never-profitable cohort. Cap-weighted quintiles +")
    L.append("  winsorized returns = what the INDEX experienced; no look-ahead." +
             ("  Quarterly: 4x the observations -> a more robust median/cumulative." if bq else ""))
    out = (BASE / "r2k_factor_spreads_q.txt") if bq else OUT
    out.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {out.name}")


if __name__ == "__main__":
    main()
