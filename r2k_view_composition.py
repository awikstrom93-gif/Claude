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


def write_sheet(wb, panel):
    from openpyxl.styles import Font
    ws = wb.create_sheet(SHEET)
    ws.cell(row=1, column=1, value=TITLE_TEXT).font = Font(bold=True, size=12)
    for c, h in enumerate(HDR, 1):
        ws.cell(row=3, column=c, value=h).font = Font(bold=True)
    for i, row in enumerate(composition_rows(panel), start=4):
        for c, v in enumerate(row, 1):
            ws.cell(row=i, column=c, value=v)
    return ws


def main():
    panel = get_panel()
    rows = composition_rows(panel)
    print(f"\n  {SHEET} (panel-derived):")
    print_sheet(HDR, rows, ncols=7)
    diff_sheet(SHEET, HDR, rows, keycols=2)


if __name__ == "__main__":
    main()
