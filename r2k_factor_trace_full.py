"""
r2k_factor_trace_full.py -- the FULL-DATASET calculation trace, as flat data.

One row per (index, month, name) with EVERY intermediate exposed: the fiscal year used, the as-filed
line items, the derived ratios, the momentum/lowvol windows, then the raw factor, the raw z-score and the
sector-neutral z-score, and the next-month return. This is the complete "show your work" for the whole
benchmark -- filterable in Excel -- and it self-validates: each recomputed raw factor is checked against
the production engine (r2k_factor_analysis.build_factor_scores) and any mismatch is reported.

Companion to r2k_factor_granular.py (which emits the per-month regression coefficients). Same inputs.

RUN:  python r2k_factor_trace_full.py
OUTPUT:  audit_factor_trace_full.csv
"""
import os
import csv
import math
from pathlib import Path

import r2k_factor_analysis as fa

BASE = Path(os.environ.get("R2KG_BASE", "."))
OUT = BASE / "audit_factor_trace_full.csv"


def _avail_mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


def _r(v, n=8):
    return round(v, n) if isinstance(v, (int, float)) else ""


def main():
    months, ret, t2c, c2t = fa.load_returns()
    mcap = fa.load_mktcap(months)
    fund = fa.load_fundamentals()
    midx = {m: i for i, m in enumerate(months)}
    FF = fa.FACTORS

    head = (["index", "month", "cik", "ticker", "gics_sector", "index_weight", "fiscal_year_used",
             # momentum / size / lowvol
             "mom_window_start", "mom_window_end", "momentum_raw",
             "size_mktcap", "size_raw",
             "lowvol_window_start", "lowvol_window_end", "lowvol_n", "lowvol_stdev", "lowvol_raw",
             # value
             "val_net_income", "val_total_equity", "val_revenue", "val_mktcap_usd",
             "val_EP", "val_BP", "val_SP", "value_raw",
             # quality (inputs then components)
             "q_operating_income", "q_gross_profit", "q_total_assets", "q_revenue", "q_net_income",
             "q_total_debt", "q_total_equity", "q_operating_cash_flow", "q_invested_capital",
             "q_ROIC", "q_GP_to_assets", "q_net_margin", "q_neg_leverage", "q_neg_accruals",
             "q_profitable_flag", "quality_raw",
             # growth
             "g_revenue_fy0", "g_revenue_fy1", "g_revenue_fy3", "g_YoY", "g_CAGR3", "growth_raw"]
            + [f"z_{ff}" for ff in FF] + [f"zneutral_{ff}" for ff in FF]
            + ["fwd_return_next_month"])

    rows = []
    n_checked = 0; n_mismatch = 0
    for pat, label in [("*Russell*Growth*Holdings*.xlsx", "R2000G"), ("*600*Growth*Holdings*.xlsx", "S&P600G")]:
        univ, sect = fa.load_holdings(pat)
        hmonths = sorted(univ)

        def snap(ym):
            prior = [h for h in hmonths if h <= ym]
            return prior[-1] if prior else None

        for ym in months:
            if int(ym[:4]) < fa.Y0:
                continue
            mi = midx.get(ym); s = snap(ym)
            if mi is None or mi + 1 >= len(months) or s is None:
                continue
            univ_ym, sect_ym = univ[s], sect[s]
            names = [c for c in univ_ym if (mi + 1) in ret.get(c, {})]
            if len(names) < fa.MIN_NAMES:
                continue
            year, month = int(ym[:4]), int(ym[5:7])
            raw = fa.build_factor_scores(names, mi, ym, ret, mcap, fund)     # authoritative
            zr = {ff: fa.zscore(raw[ff]) for ff in FF}                        # raw z (regressor)
            zsn = {ff: fa.zscore(raw[ff], {c: sect_ym.get(c, "?") for c in names}) for ff in FF}

            mstart = months[mi - fa.MOM_LOOK + 1] if mi - fa.MOM_LOOK + 1 >= 0 else ""
            mend = months[mi - fa.MOM_SKIP + 1 - 1] if mi - fa.MOM_SKIP >= 0 else ""     # last month in the window
            lvs = months[mi - fa.VOL_LOOK] if mi - fa.VOL_LOOK >= 0 else ""
            lve = months[mi - 1] if mi - 1 >= 0 else ""

            for c in names:
                rc = ret.get(c, {})
                # ---- momentum / size / lowvol intermediates ----
                mom = raw["Momentum"].get(c)
                mc = mcap.get(c, {}).get(mi)
                size = raw["Size"].get(c)
                w = [rc[j] for j in range(mi - fa.VOL_LOOK, mi) if j in rc]
                lv_n = len(w)
                lv_sd = ((sum((x - sum(w) / len(w)) ** 2 for x in w) / (len(w) - 1)) ** 0.5) if lv_n >= 6 else None
                lowvol = raw["LowVol"].get(c)
                # ---- fundamentals ----
                fy = fa.pit_fy(fund.get(c, {}), year, month)
                fr = fund.get(c, {}).get(fy) if fy is not None else None
                eq = (fr.get("total_equity") or fr.get("parent_equity")) if fr else None
                ni = fr.get("net_income") if fr else None
                rev = fr.get("revenue") if fr else None
                Musd = (mc * 1e6) if (mc and mc > 0) else None
                ep = (ni / Musd) if (ni is not None and Musd) else None
                bp = (eq / Musd) if (eq is not None and Musd) else None
                sp = (rev / Musd) if (rev is not None and Musd) else None
                # quality inputs + components
                ta = fr.get("total_assets") if fr else None
                oi = fr.get("operating_income") if fr else None
                gp = fr.get("gross_profit") if fr else None
                debt = fr.get("total_debt") if fr else None
                cfo = (fr.get("cfo") or fr.get("operating_cash_flow")) if fr else None
                ic = (debt or 0) + (eq or 0)
                q_roic = (oi / ic) if (oi is not None and ic > 0) else None
                q_gpa = (gp / ta) if (gp is not None and ta and ta > 0) else None
                q_nm = (ni / rev) if (ni is not None and rev and rev > 0) else None
                q_lev = (-(debt / eq)) if (debt is not None and eq and eq > 0) else None
                q_acc = (-((ni - cfo) / ta)) if (ni is not None and cfo is not None and ta and ta > 0) else None
                q_prof = (1.0 if ni > 0 else 0.0) if (ni is not None) else None
                # growth
                fc = fund.get(c, {})
                r0 = fc.get(fy, {}).get("revenue") if fy is not None else None
                r1 = fc.get(fy - 1, {}).get("revenue") if fy is not None else None
                r3 = fc.get(fy - 3, {}).get("revenue") if fy is not None else None
                yoy = (r0 / r1 - 1) if (r0 and r0 > 0 and r1 and r1 > 0) else None
                cagr = ((r0 / r3) ** (1 / 3) - 1) if (r0 and r0 > 0 and r3 and r3 > 0) else None

                # ---- validate the recomputed raws tie to the engine ----
                for ff, rv in (("Value", _avail_mean([ep, bp, sp])),
                               ("Quality", _avail_mean([q_roic, q_gpa, q_nm, q_lev, q_acc, q_prof])),
                               ("Growth", _avail_mean([yoy, cagr]))):
                    ev = raw[ff].get(c)
                    if ev is not None and rv is not None:
                        n_checked += 1
                        if abs(ev - rv) > 1e-6:
                            n_mismatch += 1

                rows.append([label, ym, c, c2t.get(c, ""), sect_ym.get(c, "?"), _r(univ_ym[c], 6),
                             fy if fy is not None else "",
                             mstart, mend, _r(mom),
                             _r(mc, 4), _r(size),
                             lvs, lve, lv_n, _r(lv_sd), _r(lowvol),
                             _r(ni, 4), _r(eq, 4), _r(rev, 4), _r(Musd, 2),
                             _r(ep), _r(bp), _r(sp), _r(raw["Value"].get(c)),
                             _r(oi, 4), _r(gp, 4), _r(ta, 4), _r(rev, 4), _r(ni, 4), _r(debt, 4),
                             _r(eq, 4), _r(cfo, 4), _r(ic, 4),
                             _r(q_roic), _r(q_gpa), _r(q_nm), _r(q_lev), _r(q_acc), _r(q_prof),
                             _r(raw["Quality"].get(c)),
                             _r(r0, 4), _r(r1, 4), _r(r3, 4), _r(yoy), _r(cagr), _r(raw["Growth"].get(c))]
                            + [_r(zr[ff].get(c), 6) for ff in FF]
                            + [_r(zsn[ff].get(c), 6) for ff in FF]
                            + [_r(rc.get(mi + 1))])

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f); wtr.writerow(head); wtr.writerows(rows)
    print(f"  wrote {OUT.name}  ({len(rows)} name-months)")
    print(f"  self-check: {n_checked} fundamental-factor recomputes vs engine, {n_mismatch} mismatch(es).")


if __name__ == "__main__":
    main()
