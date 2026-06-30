"""
r2k_view_dupont.py  --  DuPont (dollar-aggregate ROE decomposition) sheet as a pure projection of
the canonical panel.  Reproduces step3's "DuPont": index ROE = Net margin x Asset turnover x
Leverage, all dollar-aggregated over the covered constituents.

RUN:  python r2k_view_dupont.py
"""
from r2k_universe import get_panel, diff_sheet, print_sheet
from r2k_step3_analytics import dollar_agg, _p, _x

SHEET = "DuPont"
TITLE_TEXT = "DuPont: index ROE = Net margin x Asset turnover x Leverage (dollar-aggregate)"
HDR = ["Snapshot", "Net margin", "Asset turnover (x)", "Leverage (Assets/Equity, x)",
       "Implied ROE", "ROE $agg (check)"]


def dupont_rows(panel):
    years = sorted({int(r["year"]) for r in panel})
    out = []
    for yr in years:
        cov = [r for r in panel if int(r["year"]) == yr and r["covered"]]
        nm = dollar_agg([(r["net_income"], r["revenue"]) for r in cov])
        at = dollar_agg([(r["revenue"], r["assets"]) for r in cov])
        lev = dollar_agg([(r["assets"], r["equity"]) for r in cov])
        roe_da = dollar_agg([(r["net_income"], r["equity"]) for r in cov])
        implied = (nm * at * lev) if (nm is not None and at is not None and lev is not None) else None
        out.append([f"{yr}-04-30", _p(nm), _x(at), _x(lev), _p(implied), _p(roe_da)])
    return out


def write_sheet(wb, panel):
    from openpyxl.styles import Font
    ws = wb.create_sheet(SHEET)
    ws.cell(row=1, column=1, value=TITLE_TEXT).font = Font(bold=True, size=12)
    for c, h in enumerate(HDR, 1):
        ws.cell(row=3, column=c, value=h).font = Font(bold=True)
    for i, row in enumerate(dupont_rows(panel), start=4):
        for c, v in enumerate(row, 1):
            ws.cell(row=i, column=c, value=v)
    return ws


def main():
    panel = get_panel()
    rows = dupont_rows(panel)
    print(f"\n  {SHEET} (panel-derived):")
    print_sheet(HDR, rows, ncols=6)
    diff_sheet(SHEET, HDR, rows)


if __name__ == "__main__":
    main()
