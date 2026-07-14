"""
============================================================
r2k_step8_concentration.py  --  concentration & breadth deep-dive.
============================================================
Two distinct lenses the IC cares about:

  WEIGHT concentration (both indices) -- how top-heavy the benchmark is:
      Top 1/5/10/25/50 weight, largest single name, HHI, effective number of stocks.
  RETURN breadth & concentration (R2000G, needs constituent returns) -- how narrow
      leadership was, which is a direct headwind for active managers:
      % of names with a positive year, % that BEAT the index, cap-weighted vs median
      return spread, and the share of the year's gains delivered by the top contributors.

INPUTS  (R2KG_BASE)
    *Russell*Growth*Holdings*.xlsx        R2000G weights (CIK optional)
    *600*Growth*Holdings*.xlsx            S&P 600 Growth weights
    *Performance*.xlsx                    R2000G constituent monthly returns + index rows

OUTPUT
    R2000G_Concentration.xlsx
      Weight Concentration, Return Breadth, Return Concentration, Notes

Weight concentration needs no fundamentals; return breadth joins constituent returns
by CIK then ticker. Calendar years 2015 & 2026 are partial.
============================================================
"""
from pathlib import Path
from datetime import date
from statistics import median
from collections import defaultdict
import os, math

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from r2k_perf_io import load_performance, load_monthly_holdings, BASE
from r2k_universe import annual_spine, find_annual, find_quarterly   # consolidated: one definition
from r2k_calc import compound, weight_conc, carino_K, carino_k   # shared calc primitives (one definition)

OUT = BASE / "R2000G_Concentration.xlsx"
IKEY = {"R2KG": "R2KG", "SP600G": "SP6G"}   # our index label -> the benchmark key in load_performance
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "6"))
WINDOW_MONTHS = int(os.environ.get("WINDOW_MONTHS", "36"))
WINDOW_START = os.environ.get("WINDOW_START")
TOP_NS = [1, 5, 10, 25, 50]

HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
HDR2 = PatternFill("solid", fgColor="7A3B2E")
TITLE = Font(bold=True, size=12)


def find(pats):
    for p in pats:
        c = list(BASE.glob(p))
        if c: return c[0]
    return None


def _hdr(ws, row, hs, fill=HDR):
    for c, h in enumerate(hs, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = fill; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)


# annual_spine imported from r2k_universe (single source of truth).
# weight_conc() and compound() imported from r2k_calc (single definition). weight_conc uses TOP_NS by
# default there ([1,5,10,25,50]), which matches this module's TOP_NS.


def finest_holdings(index):
    """Beginning-of-month weights at the finest cadence (annual + quarterly merged) for either index --
    the index-parameterized twin of step5's R2KG-only loader, so contribution can be reconstructed for
    both benchmarks with minimal weight-drift. Quarterly wins date collisions (finer). {date: [rows]}."""
    merged = {}
    for finder in (lambda: find_annual(index), lambda: find_quarterly(index)):
        try:
            path = finder()
        except Exception:
            path = None
        if path:
            for d, rows in load_monthly_holdings(path, verbose=False).items():
                merged[d] = rows
    return merged


def _nearest_prior(dts, d):
    # STRICTLY before d: the beginning-of-month held weight, from a snapshot dated before the return
    # month. Using <= would pick the concurrent month-end snapshot, whose weights already embed that
    # month's price move -- a look-ahead that over-weights each month's winners and, in high-dispersion
    # months (esp. the June reconstitution), inflates the whole bottom-up contribution well above the
    # index return. Matches the strict r2k_calc.nearest_prior used everywhere else in the pipeline.
    p = [x for x in dts if x < d]
    return p[-1] if p else None


def group_contrib(index, months, hold, hdates, ret_rec, idx, keyfn):
    """(index_return, {group: contribution}) over `months`, monthly-linked and Carino-scaled so the
    group contributions (+ the unmatched slice) sum to the compounded index return. Each held name is
    credited to keyfn(h) -- a name (for concentration), a GICS sector, or a Morningstar industry. Uses
    the strict beginning-of-month prior snapshot (no look-ahead) and beginning-of-month held weight."""
    ikey = IKEY[index]
    if ikey not in idx:
        return None, {}
    idx_ret = compound([idx[ikey]["ret"].get(d) for d in months])
    if idx_ret is None:
        return None, {}
    Kt = carino_K(idx_ret)
    contrib = defaultdict(float)
    for d in months:
        snap = hold.get(_nearest_prior(hdates, d))
        if not snap:
            continue
        tw = sum(h["weight"] for h in snap) or 1.0
        Rm = idx[ikey]["ret"].get(d)
        if Rm is None:
            continue
        km = carino_k(Rm)
        for h in snap:
            rec = ret_rec(h)
            if rec is None:
                continue
            rm = rec["ret"].get(d)
            if rm is None:
                continue
            contrib[keyfn(h) or "Unknown"] += (km / Kt) * (h["weight"] / tw) * rm
    return idx_ret, contrib


def period_contrib(index, months, hold, hdates, ret_rec, idx):
    """(index_return, {name: contribution}); the name-keyed case of group_contrib (see it for the math)."""
    return group_contrib(index, months, hold, hdates, ret_rec, idx, lambda h: h["nt"] or h["name"])


def topn_pts(contrib, ns, idx_ret):
    """{n: (contribution_pts, share_of_index_return)} for the top-n return contributors. The SHARE
    divides the top-n contribution by the INDEX RETURN (idx_ret) -- so it reconciles with the 'index%'
    column shown beside it and with pts/index-return. (The prior version divided by the sum of all
    constituent contributions, which does not equal the index return once coverage/linking are in play,
    so 'share' and 'pts / index return' disagreed.) Share is None when idx_ret <= 0, because a 'share of
    the return' is meaningless against a flat or negative base."""
    ranked = sorted(contrib.values(), reverse=True)
    denom = idx_ret if (idx_ret and idx_ret > 0) else None
    return {nn: (sum(ranked[:nn]), (sum(ranked[:nn]) / denom if denom else None)) for nn in ns}


def return_contribution(wb, series, idx, pdates, ret_rec):
    """New tab: how much of each index's return came from its top contributors -- the run-up in names,
    turned into RETURN, both ABSOLUTE (each index) and RELATIVE (R2KG minus S&P600G). Calendar-year and
    rolling-12m, so a concentrated run-up (top-N weight ballooning within a year, per the Weight
    Concentration tab) is tied to the return it delivered."""
    NS = [10, 25, 50]
    holds = {ix: finest_holdings(ix) for ix in ("R2KG", "SP600G")}
    hd = {ix: sorted(holds[ix]) for ix in holds}

    def covered(ix):
        if IKEY[ix] not in idx or not hd[ix]:
            return False
        snap = holds[ix][hd[ix][-1]]
        return snap and sum(1 for h in snap if ret_rec(h)) / len(snap) > 0.5

    idxs = [ix for ix in ("R2KG", "SP600G") if covered(ix)]
    rel = len(idxs) == 2
    lab = {"R2KG": "R2KG", "SP600G": "600G"}

    ws = wb.create_sheet("Return Contribution")
    ws.cell(1, 1, "Return contribution of the top names -- how the run-up in the largest names drove "
            "each index (absolute), and R2000G vs S&P600G (relative)").font = TITLE

    # ---- calendar-year ----
    ws.cell(3, 1, "Calendar-year: contribution (pts) of the top-N return drivers to each index's total "
            "return, and their share of it").font = Font(bold=True, size=11, color="1F4E5F")
    head = ["Year"]
    for ix in idxs:
        head += [f"{lab[ix]} index%"] + [f"{lab[ix]} Top{n} pts" for n in NS] \
            + [f"{lab[ix]} Top{n} %ret" for n in NS]      # top-10/25/50 share of the index return, per year
    if rel:
        head += ["Top10 pts diff (R2KG-600G)", "Top25 pts diff"]
    _hdr(ws, 4, head); r = 5
    years = sorted({d.year for d in pdates})
    for y in years:
        months = [d for d in pdates if d.year == y]
        per = {}
        for ix in idxs:
            iret, contrib = period_contrib(ix, months, holds[ix], hd[ix], ret_rec, idx)
            per[ix] = (iret, topn_pts(contrib, NS, iret)) if iret is not None else None
        if not any(per.get(ix) for ix in idxs):
            continue
        row = [y]
        for ix in idxs:
            if per.get(ix):
                iret, tp = per[ix]
                row += [round(100 * iret, 1)] + [round(100 * tp[n][0], 1) for n in NS] + \
                       [round(100 * tp[n][1], 0) if tp[n][1] is not None else None for n in NS]
            else:
                row += [None] * (2 * len(NS) + 1)
        if rel and per.get("R2KG") and per.get("SP600G"):
            d10 = 100 * (per["R2KG"][1][10][0] - per["SP600G"][1][10][0])
            d25 = 100 * (per["R2KG"][1][25][0] - per["SP600G"][1][25][0])
            row += [round(d10, 1), round(d25, 1)]
        elif rel:
            row += [None, None]
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
        r += 1

    # ---- rolling 12m ----
    r += 1
    ws.cell(r, 1, "Rolling 12-month: contribution (pts) of the top-N return drivers over the trailing "
            "year -- when concentration drove each index, and the gap vs S&P600G").font = Font(bold=True, size=11, color="7A3B2E")
    r += 1
    rhead = ["Month ending"]
    for ix in idxs:
        rhead += [f"{lab[ix]} Top10 pts", f"{lab[ix]} Top25 pts"]
    if rel:
        rhead += ["Top10 pts diff (R2KG-600G)"]
    _hdr(ws, r, rhead, fill=HDR2); r += 1
    mdates = sorted(pdates)
    for i in range(11, len(mdates)):
        window = mdates[i - 11:i + 1]                      # trailing 12 months
        d = mdates[i]
        cell = {}
        for ix in idxs:
            iret, contrib = period_contrib(ix, window, holds[ix], hd[ix], ret_rec, idx)
            cell[ix] = topn_pts(contrib, [10, 25], iret) if iret is not None else None
        if not any(cell.values()):
            continue
        row = [f"{d:%Y-%m}"]
        for ix in idxs:
            tp = cell.get(ix)
            row += [round(100 * tp[10][0], 1), round(100 * tp[25][0], 1)] if tp else [None, None]
        if rel and cell.get("R2KG") and cell.get("SP600G"):
            row += [round(100 * (cell["R2KG"][10][0] - cell["SP600G"][10][0]), 1)]
        elif rel:
            row += [None]
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
        r += 1

    ws.cell(r + 1, 1, "Contribution = sum over the period's months of (beginning-of-month held weight x "
            "that month's return), Carino-linked so the top-N contributions are a share of the index's "
            "compounded return. 'Top-N' = the N largest RETURN contributors that period (concentration of "
            "gains). A positive R2KG-minus-600G diff means R2000G's gains leaned harder on a few names than "
            "the earnings-screened S&P600G did." + ("" if rel else "  (S&P600G constituent returns not "
            "found in the performance file -- showing R2000G only; supply the multi-index constituent "
            "workbook to add the relative columns.)"))
    ws.freeze_panes = "B5"
    return idxs


def group_contribution(wb, idx, pdates, ret_rec, keyfn, tabname, title, group_label, top_n=None):
    """New tab: how much of each index's return came from each GROUP (GICS sector or Morningstar
    industry) over time. Block 1 -- R2000G calendar-year contribution (pts) by group (chartable, each
    year's return split by group). Block 2 -- full-period cumulative contribution by group, ranked,
    R2000G vs S&P600G, with each group's share of the index return. Both are Carino-linked (block 1
    within each year, block 2 over the whole window), so the parts sum to the compounded index return
    for that span. When there are more groups than `top_n`, the smaller ones collapse to 'Other'."""
    holds = {ix: finest_holdings(ix) for ix in ("R2KG", "SP600G")}
    hd = {ix: sorted(holds[ix]) for ix in holds}

    def covered(ix):
        if IKEY[ix] not in idx or not hd[ix]:
            return False
        snap = holds[ix][hd[ix][-1]]
        return bool(snap) and sum(1 for h in snap if ret_rec(h)) / len(snap) > 0.5

    idxs = [ix for ix in ("R2KG", "SP600G") if covered(ix)]
    if "R2KG" not in idxs:
        return None
    rel = "SP600G" in idxs
    years = sorted({d.year for d in pdates})
    allm = sorted(pdates)

    # full-period contribution by group (Carino-linked to the compounded full return), to rank groups
    full = {ix: group_contrib(ix, allm, holds[ix], hd[ix], ret_rec, idx, keyfn) for ix in idxs}
    fr_ir, fr_gc = full["R2KG"]
    if fr_ir is None:
        return None
    ranked = sorted(fr_gc, key=lambda g: -abs(fr_gc[g]))
    collapse = bool(top_n) and len(ranked) > top_n
    keep = ranked[:top_n] if collapse else ranked
    cols = keep + (["Other"] if collapse else [])
    bucket = lambda g: g if g in keep else "Other"

    ws = wb.create_sheet(tabname)
    ws.cell(1, 1, title).font = TITLE

    # ---- Block 1: R2000G calendar-year contribution (pts) by group ----
    ws.cell(3, 1, f"R2000G: calendar-year contribution (pts) to the index return by {group_label} "
                  "(each year's bar sums to the index return)").font = Font(bold=True, size=11, color="1F4E5F")
    _hdr(ws, 4, [f"Year {group_label}", "R2KG index%"] + cols)          # unique corner label = chart anchor
    r = 5
    for y in years:
        months = [d for d in pdates if d.year == y]
        iret, gc = group_contrib("R2KG", months, holds["R2KG"], hd["R2KG"], ret_rec, idx, keyfn)
        if iret is None:
            continue
        agg = defaultdict(float)
        for g, v in gc.items():
            agg[bucket(g)] += v
        row = [y, round(100 * iret, 1)] + [round(100 * agg.get(c, 0.0), 1) for c in cols]
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
        r += 1

    # ---- Block 2: full-period cumulative contribution by group, ranked ----
    r += 1
    ws.cell(r, 1, f"Full-period cumulative contribution (pts) by {group_label}, ranked -- what drove "
                  "the return over the whole window").font = Font(bold=True, size=11, color="7A3B2E")
    r += 1
    hh = [group_label, "R2KG pts", "R2KG % of return"] + (["600G pts", "600G % of return"] if rel else [])
    _hdr(ws, r, hh, fill=HDR2)
    r += 1
    sr_ir, sr_gc = full["SP600G"] if rel else (None, {})
    for g in ranked:
        row = [g, round(100 * fr_gc[g], 1),
               round(100 * fr_gc[g] / fr_ir, 0) if fr_ir and fr_ir > 0 else None]
        if rel:
            row += [round(100 * sr_gc.get(g, 0.0), 1),
                    round(100 * sr_gc.get(g, 0.0) / sr_ir, 0) if sr_ir and sr_ir > 0 else None]
        for c, v in enumerate(row, 1):
            ws.cell(r, c, v)
        r += 1
    ws.cell(r + 1, 1, f"Contribution = sum over months of (beginning-of-month held weight x that month's "
                      f"return), Carino-linked so the {group_label} parts sum to the index's compounded "
                      f"return for the span. '% of return' = the group's pts / the index return; blank when "
                      f"the index return is <= 0. Names lacking a return stream fall in the coverage residual, "
                      f"so the parts sum to slightly under the index return (see the Return Contribution tab).")
    ws.column_dimensions["A"].width = 30
    ws.freeze_panes = "B5"
    return ws.title


def build():
    series, idx, pdates = load_performance()      # unified finder prefers the multi-index constituent file
    from r2k_universe import find_annual          # one shared holdings resolver (quarterly-aware)
    hr = find_annual("R2KG")
    hs = find_annual("SP600G")
    if not hr:
        raise SystemExit("!! R2000G holdings not found")
    hold_r = load_monthly_holdings(hr, verbose=False)
    hold_s = load_monthly_holdings(hs, verbose=False) if hs else None
    spine_r = annual_spine(hold_r)
    spine_s = annual_spine(hold_s) if hold_s else {}
    years = sorted(spine_r)

    def boy_membership(hold, y):
        """Beginning-of-year membership: the EARLIEST holdings snapshot in calendar year `y` (its January
        reconstitution). Breadth buckets a name's full calendar-year return, so counting it against
        beginning-of-year membership keeps that point-in-time: a name added mid-year is not credited for
        the months before it joined, and a name deleted mid-year is still counted for the year it was in
        at the start. The prior code used the JUNE (annual-spine) snapshot, which quietly credited names
        added Feb-Jun with the whole year's return (the same pre-membership look-ahead the Return
        Concentration tab already avoids)."""
        ds = [d for d in hold if d.year == y]
        return hold[min(ds)] if ds else None

    # return lookup
    by_cik, by_nt = {}, {}
    for rec in series:
        m = rec["meta"]
        if m["cik"]: by_cik[m["cik"]] = rec
        if m["nt"]: by_nt.setdefault(m["nt"], rec)
    def ret_rec(h): return by_cik.get(h["cik"]) or by_nt.get(h["nt"])

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Weight Concentration ----
    SHOW = (5, 10, 25, 50)                       # top-N weights the tab displays (weight_conc computes all)
    def conc_cols(pfx):
        return [f"{pfx} #"] + [f"{pfx} Top{k}%" for k in SHOW] + [f"{pfx} Max%", f"{pfx} HHI", f"{pfx} EffN"]
    def conc_row(c):
        return ([c["n"]] + [c["top"][k] for k in SHOW] + [c["max"], c["hhi"], c["effn"]]) if c else [None] * (len(SHOW) + 4)

    ww = wb.create_sheet("Weight Concentration")
    ww.cell(1, 1, "Weight concentration -- how top-heavy each benchmark is").font = TITLE
    # ANNUAL (the June spine snapshot each year)
    ww.cell(3, 1, "Annual -- the June snapshot each year").font = Font(bold=True, size=11, color="1F4E5F")
    cols = ["Year"] + conc_cols("R2KG") + conc_cols("600G")
    _hdr(ww, 4, cols); r = 5
    for y in years:
        a = weight_conc(hold_r[spine_r[y]])
        b = weight_conc(hold_s[spine_s[y]]) if (hold_s and y in spine_s) else None
        for c, v in enumerate([y] + conc_row(a) + conc_row(b), 1):
            ww.cell(r, c, v)
        r += 1
    # QUARTERLY -- intra-year run-up: a name/cohort can balloon between the annual June snapshots, and
    # the quarter-end (Mar/Jun/Sep/Dec) top-N weight makes that visible. Weight-only, so both indices
    # are fully covered regardless of fundamentals coverage.
    from r2k_universe import quarterly_weight_conc, quarter_label
    qr_ = dict(quarterly_weight_conc("R2KG", top_ns=SHOW))
    qs_ = dict(quarterly_weight_conc("SP600G", top_ns=SHOW))
    qdates = sorted(set(qr_) | set(qs_))
    r += 1
    ww.cell(r, 1, "Quarterly -- top-N weight at each quarter-end (Mar/Jun/Sep/Dec); shows how much the "
            "top names run up WITHIN a calendar year, between the annual snapshots above").font = Font(bold=True, size=11, color="7A3B2E")
    r += 1
    _hdr(ww, r, ["Quarter"] + conc_cols("R2KG") + conc_cols("600G"), fill=HDR2); r += 1
    for d in qdates:
        for c, v in enumerate([quarter_label(d)] + conc_row(qr_.get(d)) + conc_row(qs_.get(d)), 1):
            ww.cell(r, c, v)
        r += 1
    ww.cell(r + 1, 1, "HHI = sum of squared % weights (higher = more concentrated). "
            "Effective N = 1/sum(share^2) = how many equal-weight names give the same concentration. "
            "Top-N% = combined index weight of the N largest names. Quarterly rows use the quarter-end "
            "holdings; annual rows use the June snapshot the rest of the workbook is built on.")
    ww.freeze_panes = "B5"

    # ---- Return Breadth (BOTH indices) ----
    def breadth(snap, ikey, ym):
        """[names, %pos, %beat, capwtd, median, cap-minus-median, top10 share of gains, top25]
        for one index-year, from beginning-of-year membership joined to constituent returns."""
        idx_ret = compound([idx[ikey]["ret"].get(d) for d in ym]) if ikey in idx else None
        names = []
        for h in snap:
            rec = ret_rec(h)
            if not rec or all(rec["ret"].get(d) is None for d in ym):
                continue
            names.append((h["weight"], compound([rec["ret"].get(d) for d in ym])))
        if not names:
            return None
        tw = sum(w for w, _ in names) or 1e-9
        npos = sum(1 for _, rr in names if rr > 0)
        nbeat = sum(1 for _, rr in names if idx_ret is not None and rr > idx_ret)
        capw = sum(w * rr for w, rr in names) / tw
        med = median([rr for _, rr in names])
        contribs = sorted((w / tw * rr for w, rr in names), reverse=True)
        gains = sum(c for c in contribs if c > 0) or 1e-9
        return [len(names), round(100 * npos / len(names), 1), round(100 * nbeat / len(names), 1),
                round(100 * capw, 1), round(100 * med, 1), round(100 * (capw - med), 1),
                round(sum(c for c in contribs[:10] if c > 0) / gains * 100, 1),
                round(sum(c for c in contribs[:25] if c > 0) / gains * 100, 1)]

    have_s = bool(hold_s and spine_s)
    wb2 = wb.create_sheet("Return Breadth")
    wb2.cell(1, 1, "Return breadth -- how narrow was leadership (a direct active-manager headwind): "
             "R2000G vs S&P600G").font = TITLE
    metric = ["Names", "% Positive", "% Beat index", "Cap-wtd ret%", "Median ret%",
              "Cap-wtd - median (pts)", "Top10 % of gains", "Top25 % of gains"]
    bcols = ["Year"] + [f"R2KG {m}" for m in metric] + ([f"600G {m}" for m in metric] if have_s else [])
    _hdr(wb2, 3, bcols); r = 4
    breadth_rows = []
    for y in years:
        ym = [d for d in pdates if d.year == y]
        if not ym:
            continue
        mem_r = boy_membership(hold_r, y)
        a = breadth(mem_r, "R2KG", ym) if mem_r else None
        if a is None:
            continue
        mem_s = boy_membership(hold_s, y) if have_s else None
        b = breadth(mem_s, "SP6G", ym) if mem_s else None
        row = [y] + a + (b if b else ([None] * len(metric) if have_s else []))
        for c, v in enumerate(row, 1):
            wb2.cell(r, c, v)
        breadth_rows.append((y, row)); r += 1
    wb2.cell(r + 1, 1, "% Beat index = share of constituents whose calendar-year return exceeded THEIR OWN "
             "index's. A positive cap-wtd-minus-median spread means a few big winners pulled the index above "
             "the typical stock -- narrower in R2000G is the active-manager headwind; compare with S&P600G.")
    wb2.freeze_panes = "B4"

    # ---- Return Concentration over the manager window ----
    wc = wb.create_sheet("Return Concentration")
    wc.cell(1, 1, "R2000G return concentration over the manager window -- who drove the benchmark").font = TITLE
    win = [d for d in pdates if "R2KG" in idx and idx["R2KG"]["ret"].get(d) is not None]
    if WINDOW_START:
        ws0 = date.fromisoformat(WINDOW_START); win = [d for d in win if d >= ws0]
    else:
        win = win[max(0, len(win) - WINDOW_MONTHS):]
    # MONTHLY-LINKED contribution. The prior version used latest-snapshot weight x the constituent's
    # return compounded over the WHOLE window -- so a name that grew INTO the index was credited with its
    # entire multi-year run-up (including months before it was a member) at its final, large weight. That
    # overstated late entrants wildly (AAOI: +8372% x 0.51% = 43 pts, from a spurious pre-membership run).
    # Correct contribution = sum over window months of (beginning-of-month weight x that month's return),
    # Carino-scaled so the parts sum to the COMPOUNDED index window return. Finest holdings cadence
    # (annual+quarterly) means a name is only credited for the months it was actually held.
    from r2k_step5_cohort_attribution import load_finest_holdings, nearest_prior
    fh = load_finest_holdings(); hdates = sorted(fh)
    idx_win = compound([idx["R2KG"]["ret"].get(d) for d in win]) if "R2KG" in idx else None
    Kt = carino_K(idx_win) if idx_win is not None else 1.0
    contrib = defaultdict(float); wsum = defaultdict(float); wn = defaultdict(int); grow = defaultdict(lambda: 1.0)
    for d in win:
        snap = fh.get(nearest_prior(hdates, d))
        if not snap: continue
        traw = sum(h["weight"] for h in snap) or 1.0
        Rm = idx["R2KG"]["ret"].get(d) if "R2KG" in idx else None
        km = carino_k(Rm) if Rm is not None else 1.0
        for h in snap:
            rec = ret_rec(h)
            if rec is None: continue
            rm = rec["ret"].get(d)
            if rm is None: continue
            nm = h["nt"] or h["name"]
            wf = h["weight"] / traw
            contrib[nm] += (km / Kt) * wf * rm        # Carino-linked so contributions sum to compounded total
            wsum[nm] += wf; wn[nm] += 1; grow[nm] *= (1 + rm)
    enriched = [(nm, wsum[nm] / wn[nm], grow[nm] - 1.0, contrib[nm]) for nm in contrib if wn[nm]]
    enriched.sort(key=lambda x: x[3], reverse=True)
    tot = sum(c for _, _, _, c in enriched)
    wc.cell(3, 1, f"Window: {win[0]:%Y-%m} to {win[-1]:%Y-%m}").font = Font(bold=True)
    wc.cell(3, 4, "Index return %"); wc.cell(3, 5, round(100 * idx_win, 1) if idx_win is not None else None)
    # both-index top-N share of the window return + the R2KG-minus-600G gap (does R2000G lean harder on
    # a few names than the earnings-screened S&P600G?). Reuse the shared contribution primitive.
    holds_w = {ix: finest_holdings(ix) for ix in ("R2KG", "SP600G")}
    hd_w = {ix: sorted(holds_w[ix]) for ix in holds_w}
    share = {}
    for ix in ("R2KG", "SP600G"):
        iret, cc = period_contrib(ix, win, holds_w[ix], hd_w[ix], ret_rec, idx)
        share[ix] = topn_pts(cc, [10, 25, 50], iret) if iret is not None else None
    rel_w = share["R2KG"] and share["SP600G"]
    wc.cell(5, 1, "Share of the window return from the top contributors (monthly-linked, held-period "
            "weights) -- R2000G vs S&P600G:").font = Font(bold=True)
    _hdr(wc, 6, ["", "R2KG share %", "600G share %", "R2KG - 600G (pts)"] if rel_w else ["", "R2KG share %"])
    for i, k in enumerate([10, 25, 50]):
        rr = wc.cell(7 + i, 1, f"Top {k} names")
        a = share["R2KG"][k][1] if share["R2KG"] else None
        wc.cell(7 + i, 2, round(100 * a, 1) if a is not None else None)
        if rel_w:
            b = share["SP600G"][k][1]
            wc.cell(7 + i, 3, round(100 * b, 1) if b is not None else None)
            wc.cell(7 + i, 4, round(100 * (a - b), 1) if (a is not None and b is not None) else None)
    _hdr(wc, 12, ["Rank", "Name (R2000G)", "Avg weight (held) %", "Return while held %", "Contribution (pts)"])
    for i, (nm, wf, hr, c) in enumerate(enriched[:15], 1):
        wc.cell(12 + i, 1, i); wc.cell(12 + i, 2, nm)
        wc.cell(12 + i, 3, round(100 * wf, 2)); wc.cell(12 + i, 4, round(100 * hr, 1))
        wc.cell(12 + i, 5, round(100 * c, 2))
    wc.cell(29, 1, "Contribution = sum over window months of (beginning-of-month weight x that month's return), "
            "Carino-linked so the parts sum to the compounded index window return. 'Return while held' compounds "
            "only the months the name was actually in the index -- a mid-window entrant is credited for its held "
            "period, not its entire multi-year run-up. Top-contributor share shows how much of the benchmark's "
            "gain came from a handful of names.")

    # ---- Return Contribution (top-name contribution, both indices, absolute + relative) ----
    rc_idxs = return_contribution(wb, series, idx, pdates, ret_rec)

    # ---- Sector & Industry Contribution (what sectors/industries drove the return over time) ----
    group_contribution(wb, idx, pdates, ret_rec, lambda h: h.get("gics") or "Unknown",
                        "Sector Contribution", "Contribution to index return by GICS sector, over time",
                        "GICS sector")
    group_contribution(wb, idx, pdates, ret_rec, lambda h: h.get("ms_industry") or "Unknown",
                        "Industry Contribution", "Contribution to index return by Morningstar industry, over time",
                        "Morningstar industry", top_n=15)

    # ---- Notes ----
    nd = wb.create_sheet("Notes")
    for i, ln in enumerate([
        "R2000G concentration & breadth deep-dive.",
        "Weight Concentration: from index weights only (both benchmarks). HHI and effective-N are standard",
        "   concentration measures; Max name% is the single largest constituent's weight. Shows Top 5/10/25/50%.",
        "   ANNUAL rows use the June snapshot; QUARTERLY rows add every quarter-end (Mar/Jun/Sep/Dec) so the",
        "   within-year run-up of the top names -- which the annual snapshot misses -- is visible.",
        "Return Breadth: per calendar year, joins constituent monthly returns to beginning-of-year membership.",
        "   % Beat index and the cap-wtd-vs-median spread show how concentrated the year's leadership was.",
        "Return Concentration: over the manager window, the share of the index's return from the top 10/25/50 names.",
        "Return Contribution: calendar-year AND rolling-12m contribution (pts) of the top-N return drivers for",
        "   BOTH indices and the R2KG-minus-600G difference -- turns the weight run-up into the return it delivered,",
        "   absolute and relative to the earnings-screened S&P600G." + ("" if len(rc_idxs) == 2 else
        "  (S&P600G constituents absent from the perf file -> R2000G only.)"),
        "   Top-N %ret columns show each year's top-10/25/50 as a share of THAT year's index return.",
        "Sector Contribution: contribution (pts) to the index return by GICS sector -- calendar-year (each year's",
        "   bar sums to the index return) and full-period ranked, R2KG vs 600G, with each sector's % of the return.",
        "Industry Contribution: same, by Morningstar industry (top 15 by |contribution|, the rest collapse to 'Other').",
        f"Calendar years {min(pdates):%Y} (from {min(pdates):%b}) and "
        f"{max(pdates):%Y} (through {max(pdates):%b}) are partial.",
    ], 1): nd.cell(i, 1, ln)
    nd.column_dimensions["A"].width = 115

    wb.save(OUT)
    print(f"  DONE -> {OUT.name}")
    if breadth_rows:
        y, rr = breadth_rows[-1]
        print(f"  {y}: {rr[3]}% of names beat the index; top-10 names = {rr[7]}% of gains")
    if tot:
        print(f"  window top-10 share of index return: "
              f"{sum(c for _,_,_,c in enriched[:10])/tot*100:.1f}%")


if __name__ == "__main__":
    build()
