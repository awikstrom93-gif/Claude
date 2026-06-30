"""
r2k_build_analytics.py  --  drop-in replacement for r2k_step3_analytics.py's workbook output.
Writes Russell2000Growth_Analytics.xlsx with the four sheets step7 consolidates into the IC
workbook (Index Quality Trends, Profitability Cohorts, DuPont, Composition Change) -- but sourced
entirely from the canonical panel, so they carry the base-first identity fix and the unified
universe.  Run this INSTEAD of r2k_step3_analytics.py; the rest of the chain (step4/5/6/8/9 ->
step7_consolidate) is unchanged and already correct.

RUN:  python r2k_build_analytics.py        # rebuilds Russell2000Growth_Analytics.xlsx from the panel
"""
import openpyxl

from r2k_universe import (build_panel, write_panel, PANEL_CSV, BASE,
                          build_quarterly_panel, PANEL_Q_CSV)
import r2k_view_quality_trends as qt
import r2k_view_cohorts as co
import r2k_view_dupont as du
import r2k_view_composition as cc

OUT = BASE / "Russell2000Growth_Analytics.xlsx"
VIEWS = [qt, co, du, cc]   # each exposes write_sheet(wb, panel) with the step7 sheet name


def main():
    # always rebuild BOTH panels so no downstream tab can be stale vs the current engine: the annual
    # panel (most tabs) AND the quarterly panel (the timing-sensitive factor-spread / valuation cuts).
    panel = build_panel(verbose=True)
    write_panel(panel)
    print(f"  -> {PANEL_CSV.name}")
    qpanel = build_quarterly_panel(verbose=True)
    if qpanel:
        write_panel(qpanel, PANEL_Q_CSV)
        print(f"  -> {PANEL_Q_CSV.name}")
    r2kg = [r for r in panel if r["index"] == "R2KG"]

    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for v in VIEWS:
        v.write_sheet(wb, r2kg)
        print(f"  wrote sheet: {v.SHEET}")
    wb.save(OUT)
    print(f"\n  DONE -> {OUT.name}  ({len(wb.sheetnames)} sheets, all panel-sourced)")
    print("  Next: run step4/5/6/8/9 then r2k_step7_consolidate.py to assemble the IC workbook.")


if __name__ == "__main__":
    main()
