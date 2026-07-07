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
from r2k_universe import annual_spine   # consolidated: one definition
from r2k_calc import compound, weight_conc, carino_K, carino_k   # shared calc primitives (one definition)

OUT = BASE / "R2000G_Concentration.xlsx"
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


def build():
    series, idx, pdates = load_performance()
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

    # ---- Return Breadth (R2000G) ----
    wb2 = wb.create_sheet("Return Breadth")
    wb2.cell(1, 1, "R2000G return breadth -- how narrow was leadership (a direct active-manager headwind)").font = TITLE
    bcols = ["Year", "Names w/ return", "% Positive", "% Beat index", "Cap-wtd return %",
             "Median return %", "Cap-wtd minus median (pts)", "Top10 share of gains %", "Top25 share of gains %"]
    _hdr(wb2, 3, bcols); r = 4
    breadth_rows = []
    for y in years:
        snap = hold_r[spine_r[y]]
        ym = [d for d in pdates if d.year == y]
        if not ym: continue
        idx_ret = compound([idx["R2KG"]["ret"].get(d) for d in ym]) if "R2KG" in idx else None
        names = []
        for h in snap:
            rec = ret_rec(h)
            if not rec: continue
            yr_ret = compound([rec["ret"].get(d) for d in ym])
            if all(rec["ret"].get(d) is None for d in ym): continue
            names.append((h["weight"], yr_ret))
        if not names: continue
        tw = sum(w for w, _ in names) or 1e-9
        npos = sum(1 for _, rr in names if rr > 0)
        nbeat = sum(1 for _, rr in names if idx_ret is not None and rr > idx_ret)
        capw = sum(w * rr for w, rr in names) / tw
        med = median([rr for _, rr in names])
        contribs = sorted((w / tw * rr for w, rr in names), reverse=True)
        gains = sum(c for c in contribs if c > 0) or 1e-9
        top10 = sum(c for c in contribs[:10] if c > 0) / gains * 100
        top25 = sum(c for c in contribs[:25] if c > 0) / gains * 100
        row = [y, len(names), round(100*npos/len(names), 1), round(100*nbeat/len(names), 1),
               round(100*capw, 1), round(100*med, 1), round(100*(capw-med), 1),
               round(top10, 1), round(top25, 1)]
        for c, v in enumerate(row, 1): wb2.cell(r, c, v)
        breadth_rows.append((y, row)); r += 1
    wb2.cell(r + 1, 1, "% Beat index = share of constituents whose calendar-year return exceeded the index's. "
             "A positive cap-wtd-minus-median spread means a few big winners pulled the index above the typical stock.")
    wb2.freeze_panes = "A4"

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
    wc.cell(5, 1, "Share of window return from top contributors (monthly-linked, held-period weights):").font = Font(bold=True)
    for i, k in enumerate([10, 25, 50]):
        sh = sum(c for _, _, _, c in enriched[:k]) / tot * 100 if tot else None
        wc.cell(6 + i, 1, f"Top {k} names"); wc.cell(6 + i, 2, round(sh, 1) if sh is not None else None)
    _hdr(wc, 11, ["Rank", "Name", "Avg weight (held) %", "Return while held %", "Contribution (pts)"])
    for i, (nm, wf, hr, c) in enumerate(enriched[:15], 1):
        wc.cell(11 + i, 1, i); wc.cell(11 + i, 2, nm)
        wc.cell(11 + i, 3, round(100 * wf, 2)); wc.cell(11 + i, 4, round(100 * hr, 1))
        wc.cell(11 + i, 5, round(100 * c, 2))
    wc.cell(28, 1, "Contribution = sum over window months of (beginning-of-month weight x that month's return), "
            "Carino-linked so the parts sum to the compounded index window return. 'Return while held' compounds "
            "only the months the name was actually in the index -- a mid-window entrant is credited for its held "
            "period, not its entire multi-year run-up. Top-contributor share shows how much of the benchmark's "
            "gain came from a handful of names.")

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
        "Calendar years 2015 (from May) and 2026 (through Apr) are partial.",
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
