"""
r2k_view_composition.py  --  "Composition Change" sheet as a pure projection of the canonical panel.
Reproduces step3's decomposition of the year-over-year change in weighted op-margin and
%unprofitable into within-name / reweight / entrants / leavers effects.  Two rows per period.

RUN:  python r2k_view_composition.py
"""
from r2k_universe import get_panel, diff_sheet, print_sheet
from r2k_step3_analytics import _p

SHEET = "Composition Change"
TITLE_TEXT = "What drove the change: within-name vs turnover vs reweighting"
HDR = ["Period", "Metric", "Total change", "Within-name", "Reweight", "Entrants", "Leavers"]
METRICS = [("op_margin", False, "Op margin (wt)"), ("prof_ni", True, "% unprofitable (wt)")]


def _snap_cov(panel):
    """{year: {nt: (metrics_row, weight)}} over covered constituents (keyed by ticker, like step3)."""
    years = sorted({int(r["year"]) for r in panel})
    by = {y: {} for y in years}
    for r in panel:
        if r["covered"]:
            by[int(r["year"])][r["nt"]] = (r, r["weight"])
    return years, by


def composition_rows(panel):
    years, by = _snap_cov(panel)
    out = []
    for i in range(1, len(years)):
        py, cy = years[i - 1], years[i]
        pcov, ccov = by[py], by[cy]
        for key, flag, label in METRICS:
            def val(m):
                v = m[key]
                return (0.0 if v else 1.0) if flag else v   # %unprofitable = 1 - prof
            common = set(pcov) & set(ccov)
            pw = sum(w for _, w in pcov.values()) or 1
            cw = sum(w for _, w in ccov.values()) or 1
            within = reweight = entr = leav = 0.0
            for t in common:
                mp, wp = pcov[t]; mc, wc_ = ccov[t]
                if mp[key] is not None and mc[key] is not None:
                    within += (wp / pw) * (val(mc) - val(mp))
                    reweight += (wc_ / cw - wp / pw) * val(mc)
            for t in set(ccov) - common:
                mc, wc_ = ccov[t]
                if mc[key] is not None: entr += (wc_ / cw) * val(mc)
            for t in set(pcov) - common:
                mp, wp = pcov[t]
                if mp[key] is not None: leav -= (wp / pw) * val(mp)
            tot = within + reweight + entr + leav
            out.append([f"{py}->{cy}", label, _p(tot), _p(within), _p(reweight), _p(entr), _p(leav)])
    return out


SUBHDR = ["Period", "Total change", "Within-name", "Reweight", "Entrants", "Leavers"]
BLOCK_ORDER = ["% unprofitable (wt)", "Op margin (wt)"]   # thesis metric on the left


def write_sheet(wb, panel):
    """Two metric blocks side by side (cols 1-6 and 8-13), each a self-contained decomposition table
    with the same header row -- so each is chartable as a stacked column and the layout is robust to
    adding years (both blocks just grow downward)."""
    from openpyxl.styles import Font
    ws = wb.create_sheet(SHEET)
    ws.cell(row=1, column=1, value=TITLE_TEXT).font = Font(bold=True, size=12)
    ws.cell(row=2, column=1, value="Each column decomposes the year-over-year change into: within-name "
            "(same names' fundamentals), reweight (existing names' weight shifts), entrants (new index "
            "members), leavers (removed members). The four sum to the total change.").font = Font(size=9, italic=True, color="555555")
    groups = {}
    for r in composition_rows(panel):
        groups.setdefault(r[1], []).append(r)              # r[1] = metric label
    for gi, metric in enumerate(BLOCK_ORDER):
        c0 = 1 + gi * 7                                     # block 1: cols 1-6; block 2: cols 8-13
        ws.cell(row=3, column=c0, value=metric).font = Font(bold=True)
        for j, h in enumerate(SUBHDR):
            ws.cell(row=4, column=c0 + j, value=h).font = Font(bold=True)
        for i, r in enumerate(groups.get(metric, []), start=5):
            for j, v in enumerate([r[0], r[2], r[3], r[4], r[5], r[6]]):   # Period, Total, Within, Reweight, Entrants, Leavers
                ws.cell(row=i, column=c0 + j, value=v)
    return ws


def main():
    panel = get_panel()
    rows = composition_rows(panel)
    print(f"\n  {SHEET} (panel-derived):")
    print_sheet(HDR, rows, ncols=7)
    diff_sheet(SHEET, HDR, rows, keycols=2)


if __name__ == "__main__":
    main()
