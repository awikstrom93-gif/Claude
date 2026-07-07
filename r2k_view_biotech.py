"""
r2k_view_biotech.py  --  step9's FUNDAMENTALS-only biotech tabs as pure projections of the canonical
panel: "Biotech Weight & Quality", "Biotech in the Tail", "Unprofitable by Theme", "Unprofitable by
Industry".  (step9's returns-based tabs -- Biotech Contribution / Counterfactual -- need monthly
returns and are handled with the performance layer, not here.)

Classification (is_bio / is_lifesci / theme_of / THEMES) is imported straight from r2k_step9_biotech
so it is identical -- no re-implementation.  step9 already used the canonical universe/identity, so
these should diff IDENTICAL vs the current R2000G_Biotech.xlsx (the panel reproduces step9 exactly).

RUN:  python r2k_view_biotech.py
"""
from r2k_universe import get_panel, by_index_year, diff_sheet, print_sheet, BASE
from r2k_step9_biotech import is_bio, is_lifesci, theme_of, THEMES

BIO = BASE / "R2000G_Biotech.xlsx"


def _years(grp):
    return sorted(y for (ix, y) in grp if ix == "R2KG")


# ---- Biotech Weight & Quality (both indices) ----
BWQ_HDR = ["Year", "R2KG biotech wt%", "R2KG biotech #", "Biotech %unprofitable (NI)",
           "Biotech %no-revenue", "600G biotech wt%", "600G biotech #", "Wt diff (R2KG-600G)"]


def bio_weight_quality_rows(panel):
    grp = by_index_year(panel)
    out = []
    for y in _years(grp):
        rmem = grp[("R2KG", y)]
        tw = sum(r["weight"] for r in rmem) or 1e-9
        bio = [r for r in rmem if is_bio(r)]
        bw = sum(r["weight"] for r in bio) / tw * 100
        un_w = nr_w = cov_w = 0.0
        for r in bio:
            if not r["covered"]:
                continue
            cov_w += r["weight"]
            if r["prof_ni"] is False: un_w += r["weight"]
            if not r["has_rev"] and not r.get("is_financial"): nr_w += r["weight"]
        un = 100 * un_w / cov_w if cov_w else None
        nr = 100 * nr_w / cov_w if cov_w else None
        sw = sb = None
        smem = grp.get(("SP600G", y))
        if smem:
            stw = sum(r["weight"] for r in smem) or 1e-9
            sbio = [r for r in smem if is_bio(r)]
            sw = sum(r["weight"] for r in sbio) / stw * 100
            sb = len(sbio)
        out.append([y, round(bw, 1), len(bio), round(un, 1) if un is not None else None,
                    round(nr, 1) if nr is not None else None,
                    round(sw, 1) if sw is not None else None, sb,
                    round(bw - sw, 1) if sw is not None else None])
    return out


# ---- Biotech in the Tail (R2000G) ----
TAIL_HDR = ["Year", "R2KG unprofitable wt%", "Biotech (pts)", "Biotech share of unprofitable %",
            "Life-sci (pts)", "Life-sci share of unprofitable %", "Never-prof wt%",
            "Biotech share of never-prof %", "Life-sci share of never-prof %"]


def bio_in_tail_rows(panel):
    grp = by_index_year(panel)
    out = []
    for y in _years(grp):
        rmem = grp[("R2KG", y)]
        tw = sum(r["weight"] for r in rmem) or 1e-9
        un_w = un_bio = un_ls = nev_w = nev_bio = nev_ls = 0.0
        for r in rmem:
            if not r["covered"]:
                continue
            if r["prof_ni"] is False:
                un_w += r["weight"]
                if is_bio(r): un_bio += r["weight"]
                if is_lifesci(r): un_ls += r["weight"]
            if r["cohort"] == "never_profitable":
                nev_w += r["weight"]
                if is_bio(r): nev_bio += r["weight"]
                if is_lifesci(r): nev_ls += r["weight"]
        out.append([y, round(100 * un_w / tw, 1), round(100 * un_bio / tw, 1),
                    round(100 * un_bio / un_w, 1) if un_w else None,
                    round(100 * un_ls / tw, 1), round(100 * un_ls / un_w, 1) if un_w else None,
                    round(100 * nev_w / tw, 1), round(100 * nev_bio / nev_w, 1) if nev_w else None,
                    round(100 * nev_ls / nev_w, 1) if nev_w else None])
    return out


# ---- Unprofitable tail composition (theme + industry) ----
def _unprof_comp(panel):
    """{year: (industry_weights, theme_weights)} over R2000G covered unprofitable names."""
    grp = by_index_year(panel)
    comp_ind, comp_thm = {}, {}
    for y in _years(grp):
        ind_w, thm_w = {}, {}
        for r in grp[("R2KG", y)]:
            if not r["covered"] or r["prof_ni"] is not False:
                continue
            ind = (r.get("ms_industry") or "Unknown").strip() or "Unknown"
            ind_w[ind] = ind_w.get(ind, 0.0) + r["weight"]
            t = theme_of(ind)
            thm_w[t] = thm_w.get(t, 0.0) + r["weight"]
        comp_ind[y], comp_thm[y] = ind_w, thm_w
    return _years(grp), comp_ind, comp_thm


THEME_ORDER = [lab for lab, _ in THEMES] + ["Other"]
THEME_HDR = ["Year"] + THEME_ORDER


def unprof_by_theme_rows(panel):
    years, _ci, comp_thm = _unprof_comp(panel)
    out = []
    for y in years:
        tot = sum(comp_thm[y].values()) or 1e-9
        out.append([y] + [round(100 * comp_thm[y].get(t, 0) / tot, 1) for t in THEME_ORDER])
    return out


def _top_industries(comp_ind, n=14):
    tot_by_ind = {}
    for y in comp_ind:
        for ind, w in comp_ind[y].items():
            tot_by_ind[ind] = tot_by_ind.get(ind, 0) + w
    return [i for i, _ in sorted(tot_by_ind.items(), key=lambda x: -x[1])[:n]]


def unprof_by_industry(panel):
    """Returns (header, rows) -- header is data-dependent (top-14 industries)."""
    years, comp_ind, _ct = _unprof_comp(panel)
    topinds = _top_industries(comp_ind)
    hdr = ["Year"] + topinds + ["All other"]
    rows = []
    for y in years:
        tot = sum(comp_ind[y].values()) or 1e-9
        shown = [comp_ind[y].get(i, 0) for i in topinds]
        rows.append([y] + [round(100 * w / tot, 1) for w in shown] +
                    [round(100 * (tot - sum(shown)) / tot, 1)])
    return hdr, rows


def main():
    panel = get_panel(index=None)
    bwq = bio_weight_quality_rows(panel)
    print("\n  Biotech Weight & Quality (panel-derived):")
    print_sheet(BWQ_HDR, bwq, ncols=8)
    diff_sheet("Biotech Weight & Quality", BWQ_HDR, bwq, src=BIO)
    diff_sheet("Biotech in the Tail", TAIL_HDR, bio_in_tail_rows(panel), src=BIO)
    diff_sheet("Unprofitable by Theme", THEME_HDR, unprof_by_theme_rows(panel), src=BIO)
    ind_hdr, ind_rows = unprof_by_industry(panel)
    diff_sheet("Unprofitable by Industry", ind_hdr, ind_rows, src=BIO)


if __name__ == "__main__":
    main()
