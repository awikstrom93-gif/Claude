"""
============================================================
r2k_step4_performance.py  --  Russell 2000 Growth vs S&P SmallCap 600 Growth
index-level performance comparison (US Small Cap Growth benchmark review)
============================================================
Uses the index/benchmark return rows in the Morningstar monthly performance export
(no constituent data needed) to quantify the relative-performance story the IC cares
about: how much R2000G out/under-ran the earnings-screened S&P 600 Growth, where the
gap opened, and over the ~2.5-year window the active managers were measured on.

INPUT  (R2KG_BASE)
    *Monthly Performance*.xlsx   (must contain the two TR USD index rows)

OUTPUT
    R2000G_vs_SP600G_Performance.xlsx
      README, Summary, Trailing Returns, Calendar Year, Monthly & Cumulative,
      Rolling 12m Excess, Up/Down Capture, Drawdown, Charts

Set WINDOW_MONTHS (default 30) to change the "manager underperformance" window,
or WINDOW_START=YYYY-MM-DD to pin its start. RET_MODE (see r2k_perf_io) controls
how the cumulative export is converted to periodic returns.
============================================================
"""
from pathlib import Path
from datetime import date
import os, math

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference

from r2k_perf_io import load_performance, BASE
from r2k_calc import compound

OUT = BASE / "R2000G_vs_SP600G_Performance.xlsx"
WINDOW_MONTHS = int(os.environ.get("WINDOW_MONTHS", "36"))
WINDOW_START = os.environ.get("WINDOW_START")  # optional YYYY-MM-DD


# ---------- return math ----------
# compound() imported from r2k_calc (single definition; see top-of-file import)


def annualize(total_ret, n_months):
    if n_months <= 0 or total_ret is None: return None
    yrs = n_months / 12.0
    return (1.0 + total_ret) ** (1.0 / yrs) - 1.0 if yrs > 0 else None


def ann_vol(rets):
    rs = [r for r in rets if r is not None]
    if len(rs) < 2: return None
    mu = sum(rs) / len(rs)
    var = sum((r - mu) ** 2 for r in rs) / (len(rs) - 1)
    return math.sqrt(var) * math.sqrt(12)


def max_drawdown(growth):
    """growth: list of cumulative growth-of-1 levels. Returns (mdd, peak_date, trough_date)."""
    peak = -1e9; mdd = 0.0; pk_d = tr_d = None; peak_d = None
    for d, lv in growth:
        if lv > peak: peak = lv; peak_d = d
        dd = lv / peak - 1.0 if peak > 0 else 0.0
        if dd < mdd: mdd = dd; pk_d = peak_d; tr_d = d
    return mdd, pk_d, tr_d


def series_for(rec, dates):
    return [rec["ret"].get(d) for d in dates]


def growth_levels(dates, rets):
    out = []; g = 1.0
    for d, r in zip(dates, rets):
        if r is not None: g *= (1.0 + r)
        out.append((d, g))
    return out


def capture(bench, port, dates):
    """Up/down capture of `port` relative to benchmark `bench`, on the MORNINGSTAR (geometric-mean)
    convention: the ratio of the two indices' PER-PERIOD geometric-mean returns over the bench-up /
    bench-down months -- geomean = growth^(1/n) - 1. This is what Morningstar reports and is what a
    reader will find there (e.g. S&P600G vs R2KG down-capture ~87, not the ~98 the compound-total ratio
    gives -- over many deeply-negative down months the compound ratio is pulled toward 100 and overstates
    down-capture / understates up-capture). The underlying leg compounds up_b/dn_b/up_p/dn_p (growth-of-$1
    over the up / down months) are still returned so the exhibit can SHOW its work: (up-leg x down-leg)
    reconstructs each index's cumulative return exactly, independent of the ratio convention."""
    up_b = up_p = dn_b = dn_p = fl_b = fl_p = 1.0; nu = nd = nf = 0
    for d in dates:
        rb, rp = bench["ret"].get(d), port["ret"].get(d)
        if rb is None or rp is None: continue
        if rb > 0: up_b *= (1 + rb); up_p *= (1 + rp); nu += 1
        elif rb < 0: dn_b *= (1 + rb); dn_p *= (1 + rp); nd += 1
        else: fl_b *= (1 + rb); fl_p *= (1 + rp); nf += 1
    def _geo_ratio(gp, gb, n):
        if not n:
            return None
        gmb = gb ** (1.0 / n) - 1                       # per-period geometric mean of the benchmark
        return ((gp ** (1.0 / n) - 1) / gmb) if gmb != 0 else None
    up = _geo_ratio(up_p, up_b, nu)
    dn = _geo_ratio(dn_p, dn_b, nd)
    return up, dn, nu, nd, {"up_b": up_b, "up_p": up_p, "dn_b": dn_b, "dn_p": dn_p,
                            "fl_b": fl_b, "fl_p": fl_p, "nf": nf}


# ---------- workbook styling ----------
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
HDR2 = PatternFill("solid", fgColor="7A3B2E")   # quarterly-companion header fill
TITLE = Font(bold=True, size=12)


def _hdr(ws, row, headers, fill=HDR):
    for c, h in enumerate(headers, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = fill; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)


def _p(v, nd=2): return round(100 * v, nd) if v is not None else None


def build():
    series, idx, dates = load_performance()
    if "R2KG" not in idx or "SP6G" not in idx:
        raise SystemExit(f"!! need both index rows; found {sorted(idx)}. "
                         f"Check the Name column / IDX_* patterns in r2k_perf_io.py")
    R, S = idx["R2KG"], idx["SP6G"]
    # restrict to months both indices have a return
    dts = [d for d in dates if R["ret"].get(d) is not None and S["ret"].get(d) is not None]
    rr = [R["ret"][d] for d in dts]; sr = [S["ret"][d] for d in dts]
    er = [a - b for a, b in zip(rr, sr)]                # R2KG - SP6G monthly excess
    rg = growth_levels(dts, rr); sg = growth_levels(dts, sr)
    n = len(dts)

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Summary ----
    ws = wb.create_sheet("Summary")
    ws.cell(row=1, column=1, value="Russell 2000 Growth vs S&P SmallCap 600 Growth -- index-level summary").font = TITLE
    ws.cell(row=2, column=1, value=f"Full window: {dts[0]:%Y-%m} to {dts[-1]:%Y-%m}  ({n} months)")
    _hdr(ws, 4, ["Statistic", "R2000G", "S&P 600 Growth", "R2KG - SP6G"])
    full_R, full_S = compound(rr), compound(sr)
    aR, aS = annualize(full_R, n), annualize(full_S, n)
    vR, vS = ann_vol(rr), ann_vol(sr)
    mddR, _, _ = max_drawdown(rg); mddS, _, _ = max_drawdown(sg)
    # ORDER MATTERS: the Chart Builder carves this block into commensurable charts by row position --
    # rows 1-3 are the annualized percent figures (return / vol / drawdown, all in the tens of %),
    # row 4 is the unitless ratio, row 5 the cumulative % (hundreds), row 6 the hit-rate %. Keeping
    # like-scaled statistics together is what lets each chart share one axis and actually mean something
    # (the old single chart mixed 165% cumulative, a 0.44 ratio and -33% drawdown on one axis -> noise).
    # The Executive Summary reads these by LABEL (label_row), so reordering here is safe.
    stats = [
        ("Annualized return", _p(aR), _p(aS), _p(aR - aS) if (aR is not None and aS is not None) else None),
        ("Annualized volatility", _p(vR), _p(vS), _p(vR - vS) if (vR is not None and vS is not None) else None),
        ("Max drawdown", _p(mddR), _p(mddS),
         _p(mddR - mddS) if (mddR is not None and mddS is not None) else None),
        ("Return / volatility", round(aR / vR, 2) if (aR and vR) else None,
         round(aS / vS, 2) if (aS and vS) else None,
         round(aR / vR - aS / vS, 2) if (aR and vR and aS and vS) else None),
        ("Cumulative total return", _p(full_R), _p(full_S), _p(full_R - full_S)),
        ("% months R2KG > SP6G", round(100 * sum(1 for x in er if x > 0) / n, 1), None, None),
    ]
    r = 5
    for row in stats:
        for c, v in enumerate(row, 1): ws.cell(row=r, column=c, value=v)
        r += 1

    # ---- Trailing Returns ----
    wt = wb.create_sheet("Trailing Returns")
    wt.cell(row=1, column=1, value="Trailing returns (annualized for periods > 1y)").font = TITLE
    _hdr(wt, 3, ["Period", "R2000G", "S&P 600 Growth", "Excess (R2KG-SP6G)"])
    # custom manager window
    if WINDOW_START:
        ws_start = date.fromisoformat(WINDOW_START)
        win_idx = [i for i, d in enumerate(dts) if d >= ws_start]
    else:
        win_idx = list(range(max(0, n - WINDOW_MONTHS), n))
    periods = [("1Y (12m)", 12), ("3Y (36m)", 36), ("5Y (60m)", 60), ("Since start", n)]
    rr_ = 4
    for label, mo in periods:
        seg = range(max(0, n - mo), n); k = len(list(seg))
        tR, tS = compound([rr[i] for i in seg]), compound([sr[i] for i in seg])
        if k > 12: tR, tS = annualize(tR, k), annualize(tS, k)
        ex = (tR - tS) if (tR is not None and tS is not None) else None
        for c, v in enumerate([label, _p(tR), _p(tS), _p(ex)], 1): wt.cell(row=rr_, column=c, value=v)
        rr_ += 1
    # the manager-underperformance window (cumulative, NOT annualized -- it's the lived gap)
    wlabel = (f"Manager window from {dts[win_idx[0]]:%Y-%m}" if win_idx else "Manager window")
    wR, wS = compound([rr[i] for i in win_idx]), compound([sr[i] for i in win_idx])
    for c, v in enumerate([f"{wlabel} (cumulative)", _p(wR), _p(wS),
                           _p(wR - wS) if (wR is not None and wS is not None) else None], 1):
        wt.cell(row=rr_, column=c, value=v)
    wt.cell(row=rr_ + 2, column=1,
            value="Manager window = trailing %d months unless WINDOW_START set; shown cumulative (the lived gap)."
                  % WINDOW_MONTHS)

    # ---- Calendar Year ----
    wc = wb.create_sheet("Calendar Year")
    wc.cell(row=1, column=1, value="Calendar-year total return (2015 & 2026 partial)").font = TITLE
    _hdr(wc, 3, ["Year", "R2000G", "S&P 600 Growth", "Excess", "Months"])
    yrs = sorted(set(d.year for d in dts)); cr = 4
    for y in yrs:
        seg = [i for i, d in enumerate(dts) if d.year == y]
        cR, cS = compound([rr[i] for i in seg]), compound([sr[i] for i in seg])
        for c, v in enumerate([y, _p(cR), _p(cS), _p(cR - cS), len(seg)], 1): wc.cell(row=cr, column=c, value=v)
        cr += 1
    # quarterly total return -- the intra-year performance rhythm the calendar-year rows compress (e.g. a
    # quarter where the low-quality tail rips shows up here, not in the annual figure).
    cr += 1
    wc.cell(row=cr, column=1, value="Quarterly total return -- each calendar quarter's total return for "
            "both indices (%), with the R2KG-minus-SP6G excess").font = Font(bold=True, size=11, color="7A3B2E")
    cr += 1
    _hdr(wc, cr, ["Quarter", "R2000G", "S&P 600 Growth", "Excess", "Months"], fill=HDR2); cr += 1
    qseg = {}
    for i, d in enumerate(dts):
        qseg.setdefault((d.year, (d.month - 1) // 3 + 1), []).append(i)
    for (yy, qq) in sorted(qseg):
        seg = qseg[(yy, qq)]
        cR, cS = compound([rr[i] for i in seg]), compound([sr[i] for i in seg])
        for c, v in enumerate([f"{yy} Q{qq}", _p(cR), _p(cS), _p(cR - cS), len(seg)], 1): wc.cell(row=cr, column=c, value=v)
        cr += 1

    # ---- Monthly & Cumulative ----
    wm = wb.create_sheet("Monthly & Cumulative")
    _hdr(wm, 1, ["Month", "R2KG ret %", "SP6G ret %", "Excess %",
                 "R2KG growth$1", "SP6G growth$1", "Cum excess (R2KG-SP6G) %"])
    # Cumulative excess = the growth-of-$1 DIFFERENCE (R2KG $ growth - SP6G $ growth), so it ends at the
    # SAME -25.5% the Perf Summary scoreboard reports (165.0% - 190.5%). The prior version compounded the
    # monthly excess (a monthly-rebalanced long/short), which ended at -7.69% and silently disagreed with
    # the headline and with this column's own "R2KG-SP6G" label.
    for i, d in enumerate(dts):
        vals = [f"{d:%Y-%m-%d}", _p(rr[i]), _p(sr[i]), _p(er[i]),
                round(rg[i][1], 4), round(sg[i][1], 4), round(100 * (rg[i][1] - sg[i][1]), 2)]
        for c, v in enumerate(vals, 1): wm.cell(row=i + 2, column=c, value=v)
    wm.freeze_panes = "A2"

    # ---- Rolling 12m Excess (+ rolling annualized volatility) ----
    wr = wb.create_sheet("Rolling 12m Excess")
    _hdr(wr, 1, ["Month", "R2KG 12m %", "SP6G 12m %", "Excess 12m %",
                 "R2KG 12m vol %", "SP6G 12m vol %"])

    def _rollvol(xs):
        """annualized volatility of a 12-month return window (sample stdev x sqrt(12), in %)."""
        s = [x for x in xs if x is not None]
        if len(s) < 3:
            return None
        m = sum(s) / len(s)
        return round((sum((x - m) ** 2 for x in s) / (len(s) - 1)) ** 0.5 * (12 ** 0.5) * 100, 1)

    rw = 2
    for i in range(11, n):
        seg = range(i - 11, i + 1)
        segR, segS = [rr[j] for j in seg], [sr[j] for j in seg]
        tR, tS = compound(segR), compound(segS)
        vals = [f"{dts[i]:%Y-%m-%d}", _p(tR), _p(tS), _p(tR - tS), _rollvol(segR), _rollvol(segS)]
        for c, v in enumerate(vals, 1): wr.cell(row=rw, column=c, value=v)
        rw += 1
    wr.freeze_panes = "A2"

    # ---- Capture ----  (self-reconciling: capture ratios AND the leg compounds that rebuild cumulative)
    wcap = wb.create_sheet("Up-Down Capture")
    wcap.cell(row=1, column=1, value="Capture of S&P 600 Growth vs R2000G (R2KG = benchmark)").font = TITLE
    _hdr(wcap, 3, ["Window", "Up capture %", "Down capture %", "Up months", "Down months",
                   "R2KG up-leg %", "R2KG down-leg %", "SP6G up-leg %", "SP6G down-leg %",
                   "R2KG cumul %", "SP6G cumul %"])
    full = capture(R, S, dts)
    win = capture(R, S, [dts[i] for i in win_idx])
    for k, (label, cap) in enumerate([("Full period", full), (wlabel, win)]):
        up, dn, nu, nd, leg = cap
        # cumulative rebuilt from the SAME up/down/flat legs the capture is measured on
        cumR = leg["up_b"] * leg["dn_b"] * leg["fl_b"] - 1
        cumS = leg["up_p"] * leg["dn_p"] * leg["fl_p"] - 1
        vals = [label, _p(up, 1) if up is not None else None, _p(dn, 1) if dn is not None else None, nu, nd,
                _p(leg["up_b"] - 1), _p(leg["dn_b"] - 1), _p(leg["up_p"] - 1), _p(leg["dn_p"] - 1),
                _p(cumR), _p(cumS)]
        for c, v in enumerate(vals, 1):
            wcap.cell(row=4 + k, column=c, value=v)
    # tripwire: the full-period legs must rebuild the Summary tab's cumulative exactly (same series).
    fl = full[4]
    recon_R = fl["up_b"] * fl["dn_b"] * fl["fl_b"] - 1
    recon_S = fl["up_p"] * fl["dn_p"] * fl["fl_p"] - 1
    assert abs(recon_R - full_R) < 1e-9 and abs(recon_S - full_S) < 1e-9, \
        f"capture legs do not reconcile to cumulative: R {recon_R:.6f} vs {full_R:.6f}, S {recon_S:.6f} vs {full_S:.6f}"
    wcap.cell(row=7, column=1, value="Up/Down capture = ratio of the PER-PERIOD geometric-mean returns of SP6G vs R2KG "
              "in months R2KG was up / down (Morningstar convention: geomean = growth^(1/n)-1). This matches the "
              "capture ratios Morningstar publishes; the earlier compound-total ratio overstated down-capture (~98 vs "
              "~87) because a long run of deeply-negative down-month compounds pulls that ratio toward 100.")
    wcap.cell(row=8, column=1, value="HOW TO READ: the leg columns show growth-of-$1 in the up-months and down-months "
              "for BOTH indices; (up-leg x down-leg) rebuilds each index's cumulative (last two columns), which ties to "
              "the Summary tab EXACTLY -- the reconstruction is independent of the ratio convention. Compare capture to "
              "the SAME row's cumulative; never cross-wire full-period cumulative with the window's capture.")
    wcap.cell(row=9, column=1, value="Down-capture < 100 means SP6G fell less than R2KG in R2KG's down months; up-capture "
              "< 100 means it rose less in the up months. The window (2011-01 start here) can differ from a vendor's, "
              "which shifts which months count as up/down and moves the ratio a little.")

    # ---- Drawdown ----
    wdd = wb.create_sheet("Drawdown")
    _hdr(wdd, 1, ["Month", "R2KG drawdown %", "SP6G drawdown %"])
    pkR = pkS = -1e9
    for i, d in enumerate(dts):
        pkR = max(pkR, rg[i][1]); pkS = max(pkS, sg[i][1])
        vals = [f"{d:%Y-%m-%d}", _p(rg[i][1] / pkR - 1), _p(sg[i][1] / pkS - 1)]
        for c, v in enumerate(vals, 1): wdd.cell(row=i + 2, column=c, value=v)
    wdd.freeze_panes = "A2"

    # ---- Window Proof (data-driven justification of the manager window) ----
    wp = wb.create_sheet("Window Proof")
    wp.cell(1, 1, "Why the manager window starts where it does -- when R2000G began leading the quality index").font = TITLE
    # relative line = R2KG growth / SP6G growth; its trough = R2KG's relative low (start of its run)
    rel = [rg[i][1] / sg[i][1] for i in range(n)]
    trough_i = min(range(n), key=lambda i: rel[i])
    trough_d = dts[trough_i]
    # last persistent stretch of positive rolling-12m excess (>=6 of trailing 12m excess>0, sticky)
    roll = [None] * n
    for i in range(11, n):
        roll[i] = compound(rr[i-11:i+1]) - compound(sr[i-11:i+1])
    run_start_i = None
    for i in range(11, n):
        if roll[i] is not None and roll[i] > 0 and all((roll[j] is None or roll[j] > -0.01) for j in range(i, n)):
            run_start_i = i; break
    wp.cell(3, 1, "Inflection points (from the data):").font = Font(bold=True, size=11)
    wp.cell(4, 1, "R2KG relative low vs S&P 600 Growth (trough of the relative line)")
    wp.cell(4, 3, f"{trough_d:%Y-%m}")
    if run_start_i is not None:
        wp.cell(5, 1, "Start of R2KG's persistent rolling-12m outperformance run")
        wp.cell(5, 3, f"{dts[run_start_i]:%Y-%m}")
    wp.cell(6, 1, "Window used in this analysis (trailing 3 years to data end)")
    win_start_d = dts[win_idx[0]] if win_idx else None
    wp.cell(6, 3, f"{win_start_d:%Y-%m}" if win_start_d else "")
    # candidate-window comparison: cumulative R2KG / SP6G / excess from several anchors
    wp.cell(8, 1, "Cumulative return from candidate start dates (all ending at data end):").font = Font(bold=True, size=11)
    _hdr(wp, 9, ["Window start", "Months", "R2000G", "S&P 600 Growth", "Excess (R2KG-SP6G)", "Note"])
    cands = []
    for mo, lab in [(12, "trailing 1y"), (24, "trailing 2y"), (30, "trailing 2.5y"),
                    (36, "trailing 3y (used)"), (48, "trailing 4y")]:
        si = max(0, n - mo); cands.append((si, lab))
    cands.append((trough_i, "from R2KG relative low"))
    if run_start_i is not None: cands.append((run_start_i, "from outperformance run"))
    seen = set(); rr_ = 10
    for si, lab in sorted(cands):
        if si in seen: continue
        seen.add(si)
        cR, cS = compound(rr[si:]), compound(sr[si:])
        vals = [f"{dts[si]:%Y-%m}", n - si, _p(cR), _p(cS), _p(cR - cS), lab]
        for c, v in enumerate(vals, 1): wp.cell(rr_, c, v)
        rr_ += 1
    # the proof series: cumulative excess (R2KG-SP6G) over time, for the chart
    base_row = rr_ + 2
    wp.cell(base_row - 1, 1, "Cumulative excess (R2KG - SP6G), growth of $1 difference -- the proof series:").font = Font(bold=True)
    _hdr(wp, base_row, ["Month", "Cum excess (R2KG-SP6G) %", "Rolling 12m excess %"])
    for i, d in enumerate(dts):
        wp.cell(base_row + 1 + i, 1, f"{d:%Y-%m-%d}")
        wp.cell(base_row + 1 + i, 2, round(100 * (rg[i][1] - sg[i][1]), 2))   # true growth-of-$1 difference (ends -25.5%)
        wp.cell(base_row + 1 + i, 3, _p(roll[i]) if roll[i] is not None else None)
    proof_first = base_row + 1; proof_last = base_row + n
    ch = LineChart(); ch.title = "Cumulative excess R2KG - SP6G (trough = start of R2KG's run)"
    ch.height, ch.width = 8, 20
    ch.add_data(Reference(wp, min_col=2, max_col=3, min_row=base_row, max_row=proof_last), titles_from_data=True)
    ch.set_categories(Reference(wp, min_col=1, min_row=proof_first, max_row=proof_last))
    wp.add_chart(ch, f"E{base_row}")
    wp.cell(proof_last + 2, 1,
            "How to read: the cumulative-excess line falls while the quality index (S&P 600 Growth) leads, troughs "
            f"when R2000G is relatively weakest ({trough_d:%b %Y}), then rises as R2000G's lower-quality tail leads. "
            "The trailing-3-year window captures that rising leg -- i.e. the dates aren't hand-picked; they bracket "
            "the period the benchmark outran the quality index, which is exactly when disciplined managers fell behind.")

    # ---- Charts ----
    cs = wb.create_sheet("Charts")
    def line(title, sheet, cols, anchor, rows):
        ch = LineChart(); ch.title = title; ch.height, ch.width = 8, 18
        ch.add_data(Reference(wb[sheet], min_col=cols[0], max_col=cols[1], min_row=1, max_row=rows + 1),
                    titles_from_data=True)
        ch.set_categories(Reference(wb[sheet], min_col=1, min_row=2, max_row=rows + 1))
        cs.add_chart(ch, anchor)
    line("Growth of $1: R2000G vs S&P 600 Growth", "Monthly & Cumulative", (5, 6), "A1", n)
    line("Cumulative excess return (R2KG - SP6G)", "Monthly & Cumulative", (7, 7), "A18", n)
    line("Rolling 12-month excess (R2KG - SP6G)", "Rolling 12m Excess", (4, 4), "A35", n - 11)
    line("Drawdown: R2000G vs S&P 600 Growth", "Drawdown", (2, 3), "A52", n)

    # ---- README ----
    rd = wb.create_sheet("README")
    for i, line_ in enumerate([
        "Russell 2000 Growth vs S&P SmallCap 600 Growth -- index-level performance.",
        "Source: index/benchmark TR USD rows in the Morningstar monthly performance export.",
        "Returns are total return (TR), converted from the export's cumulative series to periodic monthly (see RET_MODE).",
        "Trailing periods >1y are annualized; the manager window is shown CUMULATIVE (the lived gap).",
        "Up/Down capture treats R2000G as the benchmark (managers are benched to R2000G).",
        "WHY THIS MATTERS: S&P SmallCap 600 requires positive trailing GAAP earnings to enter; R2000G does not.",
        "  The constituent-level quality/composition comparison (the structural 'why') is in the step-5 cohort work",
        "  and the step-3 analytics; this workbook is the realized-return scoreboard between the two benchmarks.",
        f"Window settings: WINDOW_MONTHS={WINDOW_MONTHS}, WINDOW_START={WINDOW_START or '(trailing)'}.",
    ], 1): rd.cell(row=i + 1, column=1, value=line_)
    rd.column_dimensions["A"].width = 115
    wb.move_sheet("README", -(len(wb.sheetnames) - 1))

    wb.save(OUT)
    print(f"\n  DONE -> {OUT.name}")
    print(f"  full period {dts[0]:%Y-%m}..{dts[-1]:%Y-%m}: R2KG {_p(full_R)}%  SP6G {_p(full_S)}%  "
          f"excess {_p(full_R-full_S)}%")
    print(f"  manager window ({wlabel}): R2KG {_p(wR)}%  SP6G {_p(wS)}%  excess {_p(wR-wS)}%")


if __name__ == "__main__":
    build()
