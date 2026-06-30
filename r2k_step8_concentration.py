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
import os

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment

from r2k_perf_io import load_performance, load_monthly_holdings, BASE
from r2k_universe import annual_spine   # consolidated: one definition

OUT = BASE / "R2000G_Concentration.xlsx"
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "4"))
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
def weight_conc(rows):
    ws = sorted((h["weight"] for h in rows), reverse=True)
    tw = sum(ws) or 1e-9
    shares = [w / tw for w in ws]
    return {"n": len(ws), "top": {k: round(sum(ws[:k]) / tw * 100, 2) for k in TOP_NS},
            "max": round(ws[0] / tw * 100, 2) if ws else None,
            "hhi": round(sum((s * 100) ** 2 for s in shares), 1),
            "effn": round(1 / sum(s * s for s in shares), 0) if shares else None}


def compound(rets):
    g = 1.0
    for r in rets:
        if r is not None: g *= (1 + r)
    return g - 1.0


def build():
    series, idx, pdates = load_performance()
    hr = find(["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"])
    hs = find(["*[Ss][Pp]*600*[Gg]rowth*[Hh]olding*.xlsx", "*600*[Gg]rowth*[Hh]olding*.xlsx"])
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
    ww = wb.create_sheet("Weight Concentration")
    ww.cell(1, 1, "Weight concentration -- how top-heavy each benchmark is").font = TITLE
    cols = ["Year", "R2KG #", "R2KG Top10%", "R2KG Top25%", "R2KG Max name%", "R2KG HHI", "R2KG EffN",
            "600G #", "600G Top10%", "600G Top25%", "600G Max name%", "600G HHI", "600G EffN"]
    _hdr(ww, 3, cols); r = 4
    for y in years:
        a = weight_conc(hold_r[spine_r[y]])
        b = weight_conc(hold_s[spine_s[y]]) if (hold_s and y in spine_s) else None
        row = [y, a["n"], a["top"][10], a["top"][25], a["max"], a["hhi"], a["effn"]]
        row += ([b["n"], b["top"][10], b["top"][25], b["max"], b["hhi"], b["effn"]] if b else [None]*6)
        for c, v in enumerate(row, 1): ww.cell(r, c, v)
        r += 1
    ww.cell(r + 1, 1, "HHI = sum of squared % weights (higher = more concentrated). "
            "Effective N = 1/sum(share^2) = how many equal-weight names give the same concentration.")

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
    # average weight over the window's annual spines x window-cumulative constituent return
    snap = hold_r[spine_r[years[-1]]]
    names = []
    for h in snap:
        rec = ret_rec(h)
        if not rec: continue
        cr = compound([rec["ret"].get(d) for d in win])
        if all(rec["ret"].get(d) is None for d in win): continue
        names.append((h["nt"] or h["name"], h["weight"], cr))
    idx_win = compound([idx["R2KG"]["ret"].get(d) for d in win]) if "R2KG" in idx else None
    tw = sum(w for _, w, _ in names) or 1e-9
    # contribution_i = (avg weight fraction) x (constituent window return); store weight% too
    enriched = [(nm, w / tw, cr, (w / tw) * cr) for nm, w, cr in names]
    enriched.sort(key=lambda x: x[3], reverse=True)
    tot = sum(c for _, _, _, c in enriched)
    wc.cell(3, 1, f"Window: {win[0]:%Y-%m} to {win[-1]:%Y-%m}").font = Font(bold=True)
    wc.cell(3, 4, "Index return %"); wc.cell(3, 5, round(100 * idx_win, 1) if idx_win is not None else None)
    wc.cell(5, 1, "Share of window return from top contributors (latest-year membership):").font = Font(bold=True)
    for i, k in enumerate([10, 25, 50]):
        sh = sum(c for _, _, _, c in enriched[:k]) / tot * 100 if tot else None
        wc.cell(6 + i, 1, f"Top {k} names"); wc.cell(6 + i, 2, round(sh, 1) if sh is not None else None)
    _hdr(wc, 11, ["Rank", "Name", "Avg weight %", "Window return %", "Contribution (pts)"])
    for i, (nm, wf, cr, c) in enumerate(enriched[:15], 1):
        wc.cell(11 + i, 1, i); wc.cell(11 + i, 2, nm)
        wc.cell(11 + i, 3, round(100 * wf, 2)); wc.cell(11 + i, 4, round(100 * cr, 1))
        wc.cell(11 + i, 5, round(100 * c, 2))
    wc.cell(28, 1, "Contribution = avg window weight x constituent window return. Top-contributor share shows how "
            "much of the benchmark's gain came from a handful of names -- the narrower it is, the harder for a "
            "diversified active manager to keep pace.")

    # ---- Notes ----
    nd = wb.create_sheet("Notes")
    for i, ln in enumerate([
        "R2000G concentration & breadth deep-dive.",
        "Weight Concentration: from index weights only (both benchmarks). HHI and effective-N are standard",
        "   concentration measures; Max name% is the single largest constituent's weight.",
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
