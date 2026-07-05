"""
============================================================
r2k_step6_index_comparison.py  --  Russell 2000 Growth vs S&P SmallCap 600 Growth
fundamental-quality & composition comparison (the structural "why")
============================================================
Runs the SAME as-filed quality metrics over BOTH indices' constituents and lays them
side by side, to evidence the centerpiece thesis for the IC: the S&P 600 requires
positive trailing GAAP earnings to enter and R2000G does not, so R2000G carries a far
larger unprofitable / pre-revenue tail -- exactly what rallied while earnings-disciplined
(600-like) managers lagged.

INPUTS  (R2KG_BASE)
    edgar_annual_fundamentals_ASFILED.csv         (combined universe -- run the engine
                                                   AFTER r2k_build_sp600g_universe.py)
    *Russell*Growth*Holdings*.xlsx                 (R2000G membership + weight, CIK)
    *600*Growth*Holdings*.xlsx                     (S&P 600 Growth membership + weight, CIK)

OUTPUT
    R2000G_vs_SP600G_Quality.xlsx
      README, Comparison, R2000G Quality, SP600G Quality, Cohort Weights,
      Sector Mix, Concentration, Charts

Point-in-time (FY0 = latest 10-K filed before the snapshot) and average-denominator
ratios -- identical methodology to r2k_step3_analytics, so numbers reconcile.
============================================================
"""
from pathlib import Path
from datetime import date
from statistics import median
import os, re

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference

from r2k_perf_io import load_monthly_holdings, BASE, ntk
from r2k_step3_analytics import (load_fundamentals, company_metrics, pick_fy0,
                                 aggregate, dollar_agg, load_maps)

OUT = BASE / "R2000G_vs_SP600G_Quality.xlsx"
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "4"))     # annual spine: snapshot nearest this month
TOP_NS = [10, 25, 50]


def find(pats):
    for p in pats:
        c = list(BASE.glob(p))
        if c: return c[0]
    return None


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
    """ntk(ticker) -> CIK from the security map + every temporal snapshot, so holdings rows
    WITHOUT a CIK column (the R2000G file) can still be resolved by ticker."""
    tmap = dict(base)
    for _, d in (temporal or {}).items():
        for tk, c in d.items():
            if c: tmap.setdefault(ntk(tk), str(c))
    return tmap


from r2k_universe import annual_spine   # single definition (nearest-TARGET_MONTH snapshot per year)


# ---------- per-snapshot index quality ----------
def snapshot_quality(rows, snap_dt, facts, tmap, temporal=None):
    from r2k_universe import resolve_identity   # one identity resolver (point-in-time freshness)
    cov = []                              # (metrics, weight)
    sect = {}; wt_all = 0.0
    skey = str(snap_dt)[:10]
    for h in rows:
        wt_all += h["weight"]
        sect[h["gics"] or "Unknown"] = sect.get(h["gics"] or "Unknown", 0.0) + h["weight"]
        _cik, cf, _r = resolve_identity(h, tmap, facts, snap_dt=snap_dt, temporal=temporal, skey=skey)
        if not cf: continue
        fy0 = pick_fy0(cf, snap_dt)
        if fy0 is None: continue
        cov.append((company_metrics(cf, fy0), h["weight"]))
    out = {"n_members": len(rows), "n_cov": len(cov), "wt_all": wt_all,
           "wt_cov": sum(w for _, w in cov), "sectors": sect}
    # concentration (membership-only -- always available, even with no fundamental coverage)
    ws = sorted((h["weight"] for h in rows), reverse=True); tw = sum(ws) or 1e-9
    shares = [w / tw for w in ws]
    out["topn"] = {n: round(sum(ws[:n]) / tw * 100, 1) for n in TOP_NS}
    out["hhi"] = round(sum((s * 100) ** 2 for s in shares), 1)
    out["effn"] = round(1 / sum(s * s for s in shares), 1) if shares else None
    if not cov: return out
    def colk(k): return [(m[k], w) for m, w in cov]
    ag = lambda k: aggregate(colk(k), winsor=True)
    wtot = sum(w for _, w in cov) or 1.0
    def upct(flag):
        cls = [(m, w) for m, w in cov if m[flag] is not None]
        if not cls: return None
        return 100 * sum(w for m, w in cls if m[flag] is False) / sum(w for _, w in cls)
    out.update(
        unprof_ni=upct("prof_ni"), unprof_oi=upct("prof_oi"),
        no_rev=100 * sum(w for m, w in cov if not m["has_rev"]) / wtot,
        w_prof=100 * sum(w for m, w in cov if m["cohort"] == "profitable") / wtot,
        w_fallen=100 * sum(w for m, w in cov if m["cohort"] == "fallen") / wtot,
        w_never=100 * sum(w for m, w in cov if m["cohort"] == "never_profitable") / wtot,
        gross_m=ag("gross_margin")["wavg"], op_m=ag("op_margin")["wavg"], net_m=ag("net_margin")["wavg"],
        # dollar-aggregate margins (sum income / sum revenue) -- the index-level convention; matches
        # FactSet/published index fundamentals. wavg-of-ratios is distorted by tiny-revenue/huge-loss names.
        op_da=dollar_agg([(m["operating_income"], m["revenue"]) for m, _ in cov]),
        net_da=dollar_agg([(m["net_income"], m["revenue"]) for m, _ in cov]),
        gross_da=dollar_agg([(m["gross_profit"], m["revenue"]) for m, _ in cov]),
        roe_w=ag("roe")["wavg"],   # ROE $agg on AVERAGE equity (matches per-name ROE, ROIC $agg, and the views)
        roe_da=dollar_agg([(m["net_income"], m["_aeq"] if m.get("_aeq") is not None else m["equity"]) for m, _ in cov]),
        roic_w=ag("roic")["wavg"], roic_da=dollar_agg([(m["_nopat"], m["_ic"]) for m, _ in cov]),
        gp_assets=ag("gp_to_assets")["median"], accruals=ag("accruals")["median"],
        cashconv=ag("cash_conversion")["median"],
        rev_yoy=ag("rev_yoy")["wavg"], rev_cagr3=ag("rev_cagr3")["median"], rule40=ag("rule_of_40")["median"],
        de_w=ag("d_to_equity")["wavg"], dcap_w=ag("d_to_capital")["wavg"],
        tot_rev=sum(m["revenue"] for m, _ in cov if m["revenue"]) / 1e9,
    )
    return out


# ---------- workbook ----------
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
HDR2 = PatternFill("solid", fgColor="7A3B2E")
TITLE = Font(bold=True, size=12)
def _hdr(ws, row, hs, fill=HDR):
    for c, h in enumerate(hs, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = fill; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)
def _p(v, nd=1): return round(v, nd) if v is not None else None       # already-in-% inputs
def _pp(v, nd=1): return round(100 * v, nd) if v is not None else None # fraction -> %
def _x(v, nd=2): return round(v, nd) if v is not None else None


FULL_COLS = ["Year", "Snapshot", "Members", "Covered", "%Wt cov", "%Unprof NI wt", "%Unprof OI wt",
             "%No-Rev wt", "Prof wt", "Fallen wt", "Never wt", "Tot Rev $B",
             "OpMgn $agg", "NetMgn $agg", "GrossMgn $agg", "OpMgn wavg", "GrossMgn wavg",
             "ROE wavg", "ROE $agg", "ROIC wavg", "ROIC $agg", "GP/Assets med", "Accruals med",
             "CashConv med", "RevYoY wavg", "Rev3yCAGR med", "RuleOf40 med", "D/E wavg", "D/Cap wavg"]


def full_row(year, snap_dt, q):
    return [year, f"{snap_dt:%Y-%m-%d}", q["n_members"], q["n_cov"],
            _p(100 * q["wt_cov"] / (q["wt_all"] or 1)), _p(q.get("unprof_ni")), _p(q.get("unprof_oi")),
            _p(q.get("no_rev")), _p(q.get("w_prof")), _p(q.get("w_fallen")), _p(q.get("w_never")),
            _x(q.get("tot_rev"), 1),
            _pp(q.get("op_da")), _pp(q.get("net_da")), _pp(q.get("gross_da")), _pp(q.get("op_m")), _pp(q.get("gross_m")),
            _pp(q.get("roe_w")), _pp(q.get("roe_da")), _pp(q.get("roic_w")), _pp(q.get("roic_da")),
            _pp(q.get("gp_assets")), _pp(q.get("accruals")), _x(q.get("cashconv")),
            _pp(q.get("rev_yoy")), _pp(q.get("rev_cagr3")), _pp(q.get("rule40")),
            _x(q.get("de_w")), _pp(q.get("dcap_w"))]


def build():
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    from r2k_universe import find_annual          # one shared holdings resolver (quarterly-aware)
    hr = find_annual("R2KG")
    hs = find_annual("SP600G")
    if not hr or not hs:
        raise SystemExit(f"!! need both holdings files. R2000G={hr}, SP600G={hs}")
    print(f"  R2000G holdings:  {hr.name}")
    print(f"  SP600G holdings:  {hs.name}")
    hold_r = load_monthly_holdings(hr, verbose=False)
    hold_s = load_monthly_holdings(hs, verbose=False)
    spine_r, spine_s = annual_spine(hold_r), annual_spine(hold_s)
    years = sorted(set(spine_r) & set(spine_s))
    print(f"  common years: {years[0]}..{years[-1]} ({len(years)})")

    qr = {y: snapshot_quality(hold_r[spine_r[y]], spine_r[y], facts, tmap, temporal) for y in years}
    qs = {y: snapshot_quality(hold_s[spine_s[y]], spine_s[y], facts, tmap, temporal) for y in years}

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Comparison (the earnings-screen headline) ----
    wc = wb.create_sheet("Comparison")
    wc.cell(row=1, column=1, value="R2000G vs S&P 600 Growth -- the earnings-screen gap (by index weight)").font = TITLE
    METR = [("%Unprofitable (NI) wt", "unprof_ni", "p"), ("%Unprofitable (OI) wt", "unprof_oi", "p"),
            ("%No-revenue wt", "no_rev", "p"), ("Never-profitable wt", "w_never", "p"),
            ("Op margin ($agg)", "op_da", "pp"), ("Net margin ($agg)", "net_da", "pp"),
            ("ROIC ($agg)", "roic_da", "pp"), ("ROE ($agg)", "roe_da", "pp"),
            ("Rev YoY (wavg)", "rev_yoy", "pp"), ("Rev 3y CAGR (med)", "rev_cagr3", "pp"),
            ("D/Capital (wavg)", "dcap_w", "pp")]
    hdrs = ["Year"]
    for label, _, _ in METR: hdrs += [f"{label} R2KG", f"{label} 600G", f"{label} Diff"]
    _hdr(wc, 3, hdrs); r = 4
    conv = {"p": _p, "pp": _pp}
    for y in years:
        row = [y]
        for _, key, kind in METR:
            f = conv[kind]
            a, b = f(qr[y].get(key)), f(qs[y].get(key))
            row += [a, b, (round(a - b, 1) if (a is not None and b is not None) else None)]
        for c, v in enumerate(row, 1): wc.cell(row=r, column=c, value=v)
        r += 1
    wc.cell(row=r + 1, column=1, value="Diff = R2000G - S&P 600 Growth. Positive '%unprofitable' / '%no-revenue' "
            "diffs quantify R2000G's larger low-quality tail (the structural reason 600-like managers lagged).")
    wc.cell(row=r + 2, column=1, value="Margins are DOLLAR-AGGREGATE (sum income / sum revenue) -- the index "
            "convention; validated against FactSet's R2000G EBIT margin. (Weight-weighted-average margins, shown on "
            "the per-index tabs, are distorted by tiny-revenue loss-makers and are not index-representative.)")
    wc.cell(row=r + 3, column=1, value="Gross margin is computed only where GrossProfit is reported (many "
            "banks/retailers/industrials don't tag it), so it runs higher than a full-COGS index figure -- use as a "
            "relative R2KG-vs-600G signal, not an absolute level.")
    wc.freeze_panes = "B4"

    # ---- per-index full quality tabs ----
    for name, q, fill in [("R2000G Quality", qr, HDR), ("SP600G Quality", qs, HDR2)]:
        ws = wb.create_sheet(name)
        ws.cell(row=1, column=1, value=f"{name} -- annual snapshot (nearest month {TARGET_MONTH})").font = TITLE
        _hdr(ws, 3, FULL_COLS, fill); rr = 4
        for y in years:
            for c, v in enumerate(full_row(y, (spine_r if "R2000G" in name else spine_s)[y], q[y]), 1):
                ws.cell(row=rr, column=c, value=v)
            rr += 1
        ws.freeze_panes = "C4"

    # ---- Cohort Weights (both indices) ----
    wco = wb.create_sheet("Cohort Weights")
    wco.cell(row=1, column=1, value="Profitability cohort weights -- R2000G vs S&P 600 Growth").font = TITLE
    _hdr(wco, 3, ["Year", "R2KG Prof", "R2KG Fallen", "R2KG Never", "600G Prof", "600G Fallen",
                  "600G Never", "Never wt diff (R2KG-600G)"]); rr = 4
    for y in years:
        a = qr[y]; b = qs[y]
        nd = (a.get("w_never") or 0) - (b.get("w_never") or 0)
        row = [y, _p(a.get("w_prof")), _p(a.get("w_fallen")), _p(a.get("w_never")),
               _p(b.get("w_prof")), _p(b.get("w_fallen")), _p(b.get("w_never")), round(nd, 1)]
        for c, v in enumerate(row, 1): wco.cell(row=rr, column=c, value=v)
        rr += 1

    # ---- Sector Mix (latest year) ----
    wsx = wb.create_sheet("Sector Mix")
    ly = years[-1]
    wsx.cell(row=1, column=1, value=f"GICS sector weights -- latest snapshot ({ly})").font = TITLE
    _hdr(wsx, 3, ["GICS Sector", "R2000G wt%", "S&P 600 Growth wt%", "Diff"]); rr = 4
    sa = qr[ly]["sectors"]; sb = qs[ly]["sectors"]
    ta = sum(sa.values()) or 1; tb = sum(sb.values()) or 1
    for sec in sorted(set(sa) | set(sb), key=lambda s: -(sa.get(s, 0) / ta)):
        wa, wb_ = 100 * sa.get(sec, 0) / ta, 100 * sb.get(sec, 0) / tb
        for c, v in enumerate([sec, round(wa, 1), round(wb_, 1), round(wa - wb_, 1)], 1):
            wsx.cell(row=rr, column=c, value=v)
        rr += 1

    # ---- Concentration ----
    wcn = wb.create_sheet("Concentration")
    wcn.cell(row=1, column=1, value="Breadth & concentration -- R2000G vs S&P 600 Growth").font = TITLE
    head = ["Year"] + [f"R2KG Top{n}" for n in TOP_NS] + ["R2KG HHI", "R2KG EffN"] + \
           [f"600G Top{n}" for n in TOP_NS] + ["600G HHI", "600G EffN"]
    _hdr(wcn, 3, head); rr = 4
    for y in years:
        a, b = qr[y], qs[y]
        row = [y] + [a["topn"][n] for n in TOP_NS] + [a["hhi"], a["effn"]] + \
              [b["topn"][n] for n in TOP_NS] + [b["hhi"], b["effn"]]
        for c, v in enumerate(row, 1): wcn.cell(row=rr, column=c, value=v)
        rr += 1

    # ---- Charts ----
    cs = wb.create_sheet("Charts"); n = len(years)
    def line(title, cols, anchor):
        ch = LineChart(); ch.title = title; ch.height, ch.width = 8, 18
        ch.add_data(Reference(wb["Comparison"], min_col=cols[0], max_col=cols[1], min_row=3, max_row=3 + n),
                    titles_from_data=True)
        ch.set_categories(Reference(wb["Comparison"], min_col=1, min_row=4, max_row=3 + n))
        cs.add_chart(ch, anchor)
    # cols in Comparison: Year=1; each metric block is 3 cols starting at 2
    line("% Unprofitable (NI) by weight: R2KG vs 600G", (2, 3), "A1")
    line("% No-revenue by weight: R2KG vs 600G", (8, 9), "A18")
    line("ROIC ($agg): R2KG vs 600G", (20, 21), "A35")
    line("Operating margin (wavg): R2KG vs 600G", (14, 15), "A52")

    # ---- README ----
    rd = wb.create_sheet("README")
    for i, ln in enumerate([
        "Russell 2000 Growth vs S&P SmallCap 600 Growth -- fundamental quality & composition.",
        "Same point-in-time methodology as r2k_step3_analytics (FY0, average-denominator ratios).",
        "REQUIRES the fundamentals CSV to cover BOTH universes. Build it with the DERA pipeline:",
        "   r2k_build_maps.py (builds universe_ciks.csv incl. 600G) -> r2k_dera_index/extract/classify",
        "   -> r2k_dera_to_fundamentals.py. See WORKFLOW.md.",
        "Annual spine: per index, the snapshot nearest month %d (SNAP_MONTH); common years only." % TARGET_MONTH,
        "THE THESIS: S&P 600 requires positive trailing GAAP earnings to enter; R2000G does not. The Comparison tab's",
        "   %Unprofitable / %No-revenue / Never-profitable-weight diffs quantify R2000G's larger low-quality tail --",
        "   the structural reason earnings-disciplined (600-like) managers lagged when that tail rallied.",
        "Cross-check: the realized-return side of this story is in step 4 (index scoreboard) and step 5 (cohort attribution).",
    ], 1): rd.cell(row=i + 1, column=1, value=ln)
    rd.column_dimensions["A"].width = 116
    wb.move_sheet("README", -(len(wb.sheetnames) - 1))

    wb.save(OUT)
    print(f"\n  DONE -> {OUT.name}")
    def covpct(q): return 100 * q["wt_cov"] / (q["wt_all"] or 1)
    cr = sum(covpct(qr[y]) for y in years) / len(years)
    cs_ = sum(covpct(qs[y]) for y in years) / len(years)
    print(f"  avg fundamental coverage by weight:  R2000G {cr:.1f}%   S&P 600 Growth {cs_:.1f}%")
    if cs_ < 80:
        print("  !! S&P 600 Growth coverage is LOW -- ensure r2k_build_maps.py ran (universe_ciks.csv")
        print("     covers 600G) and the DERA pipeline + r2k_dera_to_fundamentals.py were rebuilt. See WORKFLOW.md.")
    for y in (years[0], years[-1]):
        print(f"  {y}: R2KG unprof-NI {_p(qr[y].get('unprof_ni'))}%  no-rev {_p(qr[y].get('no_rev'))}%  "
              f"|  600G unprof-NI {_p(qs[y].get('unprof_ni'))}%  no-rev {_p(qs[y].get('no_rev'))}%")


if __name__ == "__main__":
    build()
