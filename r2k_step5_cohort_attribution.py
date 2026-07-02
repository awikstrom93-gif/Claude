"""
============================================================
r2k_step5_cohort_attribution.py  --  decompose Russell 2000 Growth's realized
return into fundamental-quality cohorts (profitable / fallen / never-profitable),
and run the "earnings-screen" counterfactual on R2000G's OWN names.
============================================================
Joins three inputs to answer WHY a profitability-disciplined manager (whose universe
looks like the earnings-screened S&P 600 Growth) lagged R2000G:

    holdings (monthly weights, CIK)         -> r2k_perf_io.load_monthly_holdings
    constituent monthly returns (CIK)       -> r2k_perf_io.load_performance
    as-filed fundamentals + cohort logic    -> r2k_step3_analytics

For each month it uses BEGINNING-of-month weights (prior month-end snapshot), assigns
every name a point-in-time cohort (FY0 = latest 10-K filed before that month), and
attributes the index's monthly return to cohorts. Multi-period contributions are
Carino-linked so cohort contributions sum exactly to the index's cumulative return.

Also builds two counterfactual sub-portfolios from R2000G's own constituents:
    PROFITABLE-ONLY   hold only names with positive trailing NI (the S&P 600-style screen)
    EX-NEVER-PROFIT   drop only the never-profitable tail
reweighted to sum to 1 each month -- isolating the screen effect from index construction.

OUTPUT
    R2000G_Cohort_Attribution.xlsx
      README, Reconstruction, Cohort Weights, Cohort Contribution, Cohort Returns,
      Counterfactual, Charts
============================================================
"""
from datetime import date, timedelta
import os, math

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference

from r2k_perf_io import load_performance, load_monthly_holdings, find_holdings_file, BASE, ntk
from r2k_step3_analytics import load_fundamentals, pick_fy0, company_metrics, load_maps
from r2k_universe import norm_facts, fund_for, ticker_cik_map, find_quarterly   # consolidated: one definition

OUT = BASE / "R2000G_Cohort_Attribution.xlsx"
WINDOW_MONTHS = int(os.environ.get("WINDOW_MONTHS", "36"))
WINDOW_START = os.environ.get("WINDOW_START")
COHORTS = ["profitable", "fallen", "never_profitable", "unknown"]
COH_LABEL = {"profitable": "Profitable", "fallen": "Fallen (was profitable)",
             "never_profitable": "Never profitable", "unknown": "Unknown (no NI)"}


# ticker_cik_map / norm_facts / fund_for are imported from r2k_universe (single source of truth).


def nearest_prior(sorted_dates, target):
    """latest holdings snapshot strictly before `target` (beginning-of-month weights)."""
    prev = None
    for d in sorted_dates:
        if d < target: prev = d
        else: break
    return prev


def load_finest_holdings():
    """Beginning-of-month weights at the FINEST available cadence, to minimize weight-drift error in
    the bottom-up reconstruction. The ANNUAL R2000G holdings carry only one snapshot per April, so a
    month can be reweighted with April weights up to ~12 months stale -- and in the concentrated,
    high-dispersion 2025-26 tape that stale weighting, NOT return coverage (matched weight stayed
    ~95%), was the dominant reconstruction error (e.g. 2026-04 rebuilt +10.5% vs actual +14.7%).
    Merge the annual AND quarterly holdings files so each month uses the nearest PRIOR snapshot: <=3
    months stale wherever the quarterly file reaches, falling back to annual for any earlier gap.
    Quarterly wins on a date collision (finer methodology); returns {month_end_date: [rows]}."""
    merged, sources = {}, []
    finders = [("annual", find_holdings_file), ("quarterly", lambda: find_quarterly("R2KG"))]
    for tag, finder in finders:
        try:
            path = finder()
        except Exception:
            path = None
        if not path:
            continue
        snaps = load_monthly_holdings(path, verbose=False)
        for d, rows in snaps.items():
            merged[d] = rows            # quarterly is iterated last -> wins any date collision
        sources.append(f"{tag}:{path.name} ({len(snaps)} snaps)")
    if not merged:
        raise FileNotFoundError("no R2000G holdings workbook found (annual or quarterly)")
    ds = sorted(merged)
    print(f"  holdings (finest cadence): {len(merged)} snapshots {ds[0]:%Y-%m}..{ds[-1]:%Y-%m}  "
          f"[{'; '.join(sources)}]")
    return merged


def carino(actual_cum, monthly_actual):
    """Return (K, [k_t]) Carino smoothing factors so sum_t k_t/K * contrib = cumulative."""
    A = actual_cum
    K = math.log(1 + A) / A if abs(A) > 1e-12 else 1.0
    ks = []
    for a in monthly_actual:
        ks.append(math.log(1 + a) / a if abs(a) > 1e-12 else 1.0)
    return K, ks


HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
TITLE = Font(bold=True, size=12)
def _hdr(ws, row, hs):
    for c, h in enumerate(hs, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = HDR; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)
def _p(v, nd=2): return round(100 * v, nd) if v is not None else None


def build():
    series, idx, perf_dates = load_performance()
    holdings = load_finest_holdings()
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    def hcik(h): return h["cik"] or tmap.get(h["nt"])   # R2000G holdings lack a CIK column
    if "R2KG" not in idx:
        raise SystemExit("!! R2000G index row not found in performance file")
    R = idx["R2KG"]

    # constituent return lookup: prefer CIK, fall back to ticker/isin/cusip
    by_cik, by_nt, by_id = {}, {}, {}
    for rec in series:
        m = rec["meta"]
        if m["cik"]: by_cik[m["cik"]] = rec
        if m["nt"]: by_nt.setdefault(m["nt"], rec)
        for k in (m["isin"], m["cusip"]):
            if k: by_id.setdefault(k, rec)

    def ret_for(hrow, d):
        rec = (by_cik.get(hcik(hrow)) or by_nt.get(hrow["nt"]))
        return rec["ret"].get(d) if rec else None

    hdates = sorted(holdings)
    coh_cache = {}                                   # (cik, fy0) -> cohort
    def cohort_of(cik, snap_dt):
        cf = fund_for(facts, cik)
        if not cf: return "unknown"
        fy0 = pick_fy0(cf, snap_dt)
        if fy0 is None: return "unknown"
        key = (cik, fy0)
        if key not in coh_cache:
            coh_cache[key] = company_metrics(cf, fy0)["cohort"]
        return coh_cache[key]

    # months we can attribute: have an actual R2KG return AND a prior-month holdings snapshot
    months = [d for d in perf_dates if R["ret"].get(d) is not None and nearest_prior(hdates, d)]
    if not months:
        raise SystemExit("!! no attributable months (check holdings vs performance date alignment)")

    # per-month attribution
    rows = []                                        # one dict per month
    for d in months:
        snap = holdings[nearest_prior(hdates, d)]
        traw = sum(h["weight"] for h in snap) or 1.0
        contrib = {c: 0.0 for c in COHORTS}; matched_w = 0.0; recon = 0.0
        # counterfactual accumulators
        cf_prof = {"wsum": 0.0, "rw": 0.0}; cf_exnev = {"wsum": 0.0, "rw": 0.0}
        coh_sub = {c: {"wsum": 0.0, "rw": 0.0} for c in COHORTS}
        coh_wt = {c: 0.0 for c in COHORTS}
        for h in snap:
            wf = h["weight"] / traw
            coh = cohort_of(hcik(h), d)
            coh_wt[coh] += wf
            r = ret_for(h, d)
            if r is None: continue
            matched_w += wf; recon += wf * r
            contrib[coh] += wf * r
            coh_sub[coh]["wsum"] += h["weight"]; coh_sub[coh]["rw"] += h["weight"] * r
            if coh == "profitable":
                cf_prof["wsum"] += h["weight"]; cf_prof["rw"] += h["weight"] * r
            if coh != "never_profitable":
                cf_exnev["wsum"] += h["weight"]; cf_exnev["rw"] += h["weight"] * r
        actual = R["ret"][d]
        rows.append({"d": d, "actual": actual, "recon": recon, "matched_w": matched_w,
                     "contrib": contrib, "residual": actual - recon, "coh_wt": coh_wt,
                     "coh_ret": {c: (coh_sub[c]["rw"] / coh_sub[c]["wsum"]) if coh_sub[c]["wsum"] else None
                                 for c in COHORTS},
                     "prof_ret": (cf_prof["rw"] / cf_prof["wsum"]) if cf_prof["wsum"] else None,
                     "exnev_ret": (cf_exnev["rw"] / cf_exnev["wsum"]) if cf_exnev["wsum"] else None})

    # window slice
    if WINDOW_START:
        ws_start = date.fromisoformat(WINDOW_START); win = [r for r in rows if r["d"] >= ws_start]
    else:
        win = rows[max(0, len(rows) - WINDOW_MONTHS):]

    def attribute(slice_rows):
        """Carino-linked cohort contribution to the slice's cumulative ACTUAL return."""
        if not slice_rows: return None, {}, 0.0
        acum = 1.0
        for r in slice_rows: acum *= (1 + r["actual"])
        acum -= 1.0
        K, _ = carino(acum, [r["actual"] for r in slice_rows])
        out = {c: 0.0 for c in COHORTS}; resid = 0.0
        for r in slice_rows:
            k = math.log(1 + r["actual"]) / r["actual"] if abs(r["actual"]) > 1e-12 else 1.0
            for c in COHORTS: out[c] += (k / K) * r["contrib"][c]
            resid += (k / K) * r["residual"]
        return acum, out, resid

    full_cum, full_attr, full_resid = attribute(rows)
    win_cum, win_attr, win_resid = attribute(win)

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Cohort Contribution ----
    wa = wb.create_sheet("Cohort Contribution")
    wa.cell(row=1, column=1, value="R2000G cumulative return attributed to quality cohorts (Carino-linked)").font = TITLE
    _hdr(wa, 3, ["Cohort", "Full period contrib %", "Avg weight % (full)",
                 "Manager-window contrib %", "Avg weight % (window)"])
    awt = {c: sum(r["coh_wt"][c] for r in rows) / len(rows) for c in COHORTS}
    wwt = {c: (sum(r["coh_wt"][c] for r in win) / len(win)) if win else None for c in COHORTS}
    ar = 4
    for c in COHORTS:
        for col, v in enumerate([COH_LABEL[c], _p(full_attr[c]), _p(awt[c]),
                                 _p(win_attr.get(c)), _p(wwt[c])], 1):
            wa.cell(row=ar, column=col, value=v)
        ar += 1
    for col, v in enumerate(["Unexplained (no return / timing)", _p(full_resid), None,
                             _p(win_resid), None], 1):
        wa.cell(row=ar, column=col, value=v);
    ar += 1
    for col, v in enumerate(["TOTAL = index return", _p(full_cum), 100.0, _p(win_cum), 100.0], 1):
        wa.cell(row=ar, column=col, value=v)
    wa.cell(row=ar + 2, column=1, value="Unprofitable tail = Fallen + Never profitable. "
            "Contribution columns sum to the index's cumulative return (Carino linking).")

    # ---- Cohort Weights (monthly) ----
    ww = wb.create_sheet("Cohort Weights")
    _hdr(ww, 1, ["Month"] + [COH_LABEL[c] + " wt%" for c in COHORTS] + ["Unprofitable wt%"])
    for i, r in enumerate(rows, 2):
        unprof = r["coh_wt"]["fallen"] + r["coh_wt"]["never_profitable"]
        vals = [f"{r['d']:%Y-%m-%d}"] + [_p(r["coh_wt"][c]) for c in COHORTS] + [_p(unprof)]
        for c, v in enumerate(vals, 1): ww.cell(row=i, column=c, value=v)
    ww.freeze_panes = "A2"

    # ---- Cohort Returns (monthly sub-portfolio returns) ----
    wcr = wb.create_sheet("Cohort Returns")
    _hdr(wcr, 1, ["Month", "Index actual %"] + [COH_LABEL[c] + " %" for c in COHORTS])
    for i, r in enumerate(rows, 2):
        vals = [f"{r['d']:%Y-%m-%d}", _p(r["actual"])] + [_p(r["coh_ret"][c]) for c in COHORTS]
        for c, v in enumerate(vals, 1): wcr.cell(row=i, column=c, value=v)
    wcr.freeze_panes = "A2"

    # ---- Counterfactual ----
    wcf = wb.create_sheet("Counterfactual")
    wcf.cell(row=1, column=1, value="Earnings-screen counterfactual on R2000G's OWN names (growth of $1)").font = TITLE
    _hdr(wcf, 3, ["Month", "R2000G index", "Profitable-only", "Ex-never-profitable"])
    gI = gP = gE = 1.0; cr = 4
    for r in rows:
        gI *= (1 + r["actual"])
        if r["prof_ret"] is not None: gP *= (1 + r["prof_ret"])
        if r["exnev_ret"] is not None: gE *= (1 + r["exnev_ret"])
        for c, v in enumerate([f"{r['d']:%Y-%m-%d}", round(gI, 4), round(gP, 4), round(gE, 4)], 1):
            wcf.cell(row=cr, column=c, value=v)
        cr += 1
    # window summary
    def cum(seq, key):
        g = 1.0
        for r in seq:
            v = r[key] if key != "actual" else r["actual"]
            if v is not None: g *= (1 + v)
        return g - 1.0
    wcf.cell(row=cr + 1, column=1, value="Cumulative return:")
    _hdr(wcf, cr + 2, ["Window", "R2000G index", "Profitable-only", "Ex-never-profitable"])
    for k, (label, seq) in enumerate([("Full period", rows),
                                      (f"Manager window ({WINDOW_MONTHS}m)" if not WINDOW_START else f"From {WINDOW_START}", win)]):
        for c, v in enumerate([label, _p(cum(seq, "actual")), _p(cum(seq, "prof_ret")), _p(cum(seq, "exnev_ret"))], 1):
            wcf.cell(row=cr + 3 + k, column=c, value=v)

    # ---- Reconstruction (coverage / accuracy check) ----
    wre = wb.create_sheet("Reconstruction")
    wre.cell(row=1, column=1, value="Bottom-up reconstruction vs actual index return (coverage check)").font = TITLE
    _hdr(wre, 3, ["Month", "Actual %", "Reconstructed %", "Diff (bps)", "Matched weight %"])
    for i, r in enumerate(rows):
        vals = [f"{r['d']:%Y-%m-%d}", _p(r["actual"]), _p(r["recon"]),
                round(1e4 * (r["actual"] - r["recon"]), 0), _p(r["matched_w"], 1)]
        for c, v in enumerate(vals, 1): wre.cell(row=4 + i, column=c, value=v)
    avg_match = sum(r["matched_w"] for r in rows) / len(rows)
    avg_abs_diff = sum(abs(r["actual"] - r["recon"]) for r in rows) / len(rows)
    wre.cell(row=4 + len(rows) + 1, column=1,
             value=f"Avg matched weight {100*avg_match:.1f}%  |  avg |diff| {1e4*avg_abs_diff:.0f} bps/mo "
                   f"(gap = names without a return stream + residual weight drift between snapshots). "
                   f"Weights use the finest available holdings cadence (annual + quarterly merged), so a "
                   f"month is reweighted with a snapshot <=3 months old wherever quarterly holdings reach.")
    wre.freeze_panes = "A4"

    # ---- Charts ----
    cs = wb.create_sheet("Charts"); n = len(rows)
    def line(title, sheet, cols, anchor, rstart, rows_n):
        ch = LineChart(); ch.title = title; ch.height, ch.width = 8, 18
        ch.add_data(Reference(wb[sheet], min_col=cols[0], max_col=cols[1], min_row=rstart, max_row=rstart + rows_n),
                    titles_from_data=True)
        ch.set_categories(Reference(wb[sheet], min_col=1, min_row=rstart + 1, max_row=rstart + rows_n))
        cs.add_chart(ch, anchor)
    line("Cohort weights over time", "Cohort Weights", (2, 5), "A1", 1, n)
    line("Unprofitable-tail weight", "Cohort Weights", (6, 6), "A18", 1, n)
    line("Earnings-screen counterfactual (growth of $1)", "Counterfactual", (2, 4), "A35", 3, n)

    # ---- README ----
    rd = wb.create_sheet("README")
    for i, ln in enumerate([
        "R2000G cohort attribution -- WHY a profitability-disciplined manager lagged the benchmark.",
        "Point-in-time cohorts: each name's FY0 = latest 10-K filed before that month; cohort from as-filed NI history.",
        "  profitable (NI>0), fallen (NI<=0 but profitable before), never_profitable (no profitable year on record), unknown (no NI).",
        "Weights: BEGINNING-of-month = nearest PRIOR holdings snapshot, finest available cadence",
        "  (annual + quarterly holdings merged, so <=3 months stale where quarterly reaches), normalized to snapshot total.",
        "Cohort Contribution: Carino-linked so cohort contributions + unexplained = the index's cumulative return.",
        "  'Unexplained' = constituents lacking a return stream (delisted/missing) + intra-month weight drift; see Reconstruction.",
        "Counterfactual: rebuilds two sub-portfolios from R2000G's OWN names, reweighted monthly --",
        "  PROFITABLE-ONLY approximates the S&P 600 positive-earnings screen; EX-NEVER-PROFIT drops only the never-profitable tail.",
        "  The gap between R2000G and Profitable-only is the realized cost of NOT screening for earnings.",
        f"Window: WINDOW_MONTHS={WINDOW_MONTHS}, WINDOW_START={WINDOW_START or '(trailing)'}.",
    ], 1): rd.cell(row=i + 1, column=1, value=ln)
    rd.column_dimensions["A"].width = 118
    wb.move_sheet("README", -(len(wb.sheetnames) - 1))

    wb.save(OUT)
    print(f"\n  DONE -> {OUT.name}")
    print(f"  attributed {len(rows)} months ({rows[0]['d']:%Y-%m}..{rows[-1]['d']:%Y-%m}); "
          f"avg matched weight {100*avg_match:.1f}%")
    print(f"  full-period index {_p(full_cum)}%  ->  " +
          "  ".join(f"{COH_LABEL[c]} {_p(full_attr[c])}%" for c in COHORTS) +
          f"  unexplained {_p(full_resid)}%")
    print(f"  counterfactual cumulative: index {_p(cum(rows,'actual'))}%  "
          f"profitable-only {_p(cum(rows,'prof_ret'))}%  ex-never {_p(cum(rows,'exnev_ret'))}%")


if __name__ == "__main__":
    build()
