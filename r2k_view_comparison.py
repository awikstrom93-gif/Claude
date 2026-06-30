"""
r2k_view_comparison.py  --  step6's index-comparison tabs as pure projections of the canonical panel:
"Comparison" (the earnings-screen gap R2000G vs S&P 600 Growth), "R2000G Quality" and "SP600G Quality"
(per-index full quality snapshots).  Uses the shared index_quality() reducer, so these match step6
exactly except for the base-first identity + canonical universe already validated on the R2KG tabs.
SP600G is unchanged vs the old build (its holdings carry CIKs, so it never had the temporal bug).

RUN:  python r2k_view_comparison.py        # builds the 3 sheets + diffs vs R2000G_vs_SP600G_Quality.xlsx
"""
from r2k_universe import get_panel, by_index_year, index_quality, diff_sheet, print_sheet, BASE

QUAL = BASE / "R2000G_vs_SP600G_Quality.xlsx"


def _p(v, nd=1):  return round(v, nd) if v is not None else None        # already-in-% inputs
def _pp(v, nd=1): return round(100 * v, nd) if v is not None else None   # fraction -> %
def _x(v, nd=2):  return round(v, nd) if v is not None else None


# ---- Comparison sheet ----
COMP_METR = [("%Unprofitable (NI) wt", "unprof_ni", "p"), ("%Unprofitable (OI) wt", "unprof_oi", "p"),
             ("%No-revenue wt", "no_rev", "p"), ("Never-profitable wt", "w_never", "p"),
             ("Op margin ($agg)", "op_da", "pp"), ("Net margin ($agg)", "net_da", "pp"),
             ("ROIC ($agg)", "roic_da", "pp"), ("ROE ($agg)", "roe_da", "pp"),
             ("Rev YoY (wavg)", "rev_yoy", "pp"), ("Rev 3y CAGR (med)", "rev_cagr3", "pp"),
             ("D/Capital (wavg)", "dcap_w", "pp")]
COMP_HDR = ["Year"]
for _lbl, _k, _kind in COMP_METR:
    COMP_HDR += [f"{_lbl} R2KG", f"{_lbl} 600G", f"{_lbl} Diff"]

# ---- per-index full quality tab ----
FULL_COLS = ["Year", "Snapshot", "Members", "Covered", "%Wt cov", "%Unprof NI wt", "%Unprof OI wt",
             "%No-Rev wt", "Prof wt", "Fallen wt", "Never wt", "Tot Rev $B",
             "OpMgn $agg", "NetMgn $agg", "GrossMgn $agg", "OpMgn wavg", "GrossMgn wavg",
             "ROE wavg", "ROE $agg", "ROIC wavg", "ROIC $agg", "GP/Assets med", "Accruals med",
             "CashConv med", "RevYoY wavg", "Rev3yCAGR med", "RuleOf40 med", "D/E wavg", "D/Cap wavg"]


def full_row(year, snap, q):
    return [year, snap, q["n_members"], q["n_cov"],
            _p(100 * q["wt_cov"] / (q["wt_all"] or 1)), _p(q.get("unprof_ni")), _p(q.get("unprof_oi")),
            _p(q.get("no_rev")), _p(q.get("w_prof")), _p(q.get("w_fallen")), _p(q.get("w_never")),
            _x(q.get("tot_rev"), 1),
            _pp(q.get("op_da")), _pp(q.get("net_da")), _pp(q.get("gross_da")), _pp(q.get("op_m")), _pp(q.get("gross_m")),
            _pp(q.get("roe_w")), _pp(q.get("roe_da")), _pp(q.get("roic_w")), _pp(q.get("roic_da")),
            _pp(q.get("gp_assets")), _pp(q.get("accruals")), _x(q.get("cashconv")),
            _pp(q.get("rev_yoy")), _pp(q.get("rev_cagr3")), _pp(q.get("rule40")),
            _x(q.get("de_w")), _pp(q.get("dcap_w"))]


def _quality_by_year(panel):
    grp = by_index_year(panel)
    qr = {y: index_quality(grp[("R2KG", y)]) for (ix, y) in grp if ix == "R2KG"}
    qs = {y: index_quality(grp[("SP600G", y)]) for (ix, y) in grp if ix == "SP600G"}
    years = sorted(set(qr) & set(qs))
    snap = {(ix, y): grp[(ix, y)][0]["snapshot"] for (ix, y) in grp}
    return years, qr, qs, snap


def comparison_rows(panel):
    years, qr, qs, _ = _quality_by_year(panel)
    conv = {"p": _p, "pp": _pp}
    out = []
    for y in years:
        row = [y]
        for _lbl, key, kind in COMP_METR:
            f = conv[kind]
            a, b = f(qr[y].get(key)), f(qs[y].get(key))
            row += [a, b, (round(a - b, 1) if (a is not None and b is not None) else None)]
        out.append(row)
    return out


def quality_rows(panel, index):
    years, qr, qs, snap = _quality_by_year(panel)
    q = qr if index == "R2KG" else qs
    return [full_row(y, snap[(index, y)], q[y]) for y in years]


COHORT_HDR = ["Year", "R2KG Prof", "R2KG Fallen", "R2KG Never", "600G Prof", "600G Fallen",
              "600G Never", "Never wt diff (R2KG-600G)"]


def cohort_weights_rows(panel):
    years, qr, qs, _ = _quality_by_year(panel)
    out = []
    for y in years:
        a, b = qr[y], qs[y]
        nd = (a.get("w_never") or 0) - (b.get("w_never") or 0)
        out.append([y, _p(a.get("w_prof")), _p(a.get("w_fallen")), _p(a.get("w_never")),
                    _p(b.get("w_prof")), _p(b.get("w_fallen")), _p(b.get("w_never")), round(nd, 1)])
    return out


SECTOR_HDR = ["GICS Sector", "R2000G wt%", "S&P 600 Growth wt%", "Diff"]


def sector_mix_rows(panel):
    years, qr, qs, _ = _quality_by_year(panel)
    ly = years[-1]
    sa, sb = qr[ly]["sectors"], qs[ly]["sectors"]
    ta, tb = sum(sa.values()) or 1, sum(sb.values()) or 1
    out = []
    for sec in sorted(set(sa) | set(sb), key=lambda s: -(sa.get(s, 0) / ta)):
        wa, wb_ = 100 * sa.get(sec, 0) / ta, 100 * sb.get(sec, 0) / tb
        out.append([sec, round(wa, 1), round(wb_, 1), round(wa - wb_, 1)])
    return out


TOP_NS = [10, 25, 50]
CONC_HDR = ["Year"] + [f"R2KG Top{n}" for n in TOP_NS] + ["R2KG HHI", "R2KG EffN"] + \
           [f"600G Top{n}" for n in TOP_NS] + ["600G HHI", "600G EffN"]


def concentration_rows(panel):
    years, qr, qs, _ = _quality_by_year(panel)
    out = []
    for y in years:
        a, b = qr[y], qs[y]
        out.append([y] + [a["topn"][n] for n in TOP_NS] + [a["hhi"], a["effn"]] +
                   [b["topn"][n] for n in TOP_NS] + [b["hhi"], b["effn"]])
    return out


def main():
    panel = get_panel(index=None)
    comp = comparison_rows(panel)
    print("\n  Comparison (panel-derived):")
    print_sheet(COMP_HDR, comp, ncols=7)
    diff_sheet("Comparison", COMP_HDR, comp, src=QUAL)
    diff_sheet("R2000G Quality", FULL_COLS, quality_rows(panel, "R2KG"), src=QUAL)
    diff_sheet("SP600G Quality", FULL_COLS, quality_rows(panel, "SP600G"), src=QUAL)
    diff_sheet("Cohort Weights", COHORT_HDR, cohort_weights_rows(panel), src=QUAL)
    diff_sheet("Sector Mix", SECTOR_HDR, sector_mix_rows(panel), src=QUAL)
    diff_sheet("Concentration", CONC_HDR, concentration_rows(panel), src=QUAL)


if __name__ == "__main__":
    main()
