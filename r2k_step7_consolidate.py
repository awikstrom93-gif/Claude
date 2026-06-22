"""
============================================================
r2k_step7_consolidate.py  --  merge the performance, quality, and cohort-attribution
workbooks into ONE Investment-Committee deliverable with an Executive Summary up top.
============================================================
Reads the three step outputs in R2KG_BASE:
    R2000G_vs_SP600G_Performance.xlsx     (step 4)
    R2000G_vs_SP600G_Quality.xlsx         (step 6)
    R2000G_Cohort_Attribution.xlsx        (step 5)
and writes:
    R2000G_SmallCapGrowth_Benchmark_Review.xlsx
      Executive Summary  (the story, with figures pulled LIVE from the tabs)
      Contents
      Perf *   (Summary, Trailing, Calendar Yr, Monthly, Rolling 12m, Capture, Drawdown)
      Qual *   (Comparison, R2000G, SP600G, Cohort Wt, Sector Mix, Concentration)
      Attr *   (Contribution, Cohort Wt, Cohort Ret, Counterfactual, Reconstruction)
      Key Charts

Sheets are copied cell-by-cell (values + styles) so the file is self-contained; the
headline charts are re-created against the copied tabs.
RUN: python r2k_step7_consolidate.py
============================================================
"""
from pathlib import Path
from copy import copy
import os

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference

BASE = Path(os.environ.get("R2KG_BASE", "."))
PERF = BASE / "R2000G_vs_SP600G_Performance.xlsx"
QUAL = BASE / "R2000G_vs_SP600G_Quality.xlsx"
ATTR = BASE / "R2000G_Cohort_Attribution.xlsx"
OUT = BASE / "R2000G_SmallCapGrowth_Benchmark_Review.xlsx"

TITLE = Font(bold=True, size=14, color="1F4E5F")
H = Font(bold=True, size=11, color="1F4E5F")
BODY = Font(size=10)
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)

# (source path, original sheet, new name)  -- skip per-file README/Charts
SHEETS = [
    (PERF, "Summary", "Perf Summary"), (PERF, "Trailing Returns", "Perf Trailing"),
    (PERF, "Calendar Year", "Perf Calendar Yr"), (PERF, "Monthly & Cumulative", "Perf Monthly"),
    (PERF, "Rolling 12m Excess", "Perf Rolling 12m"), (PERF, "Up-Down Capture", "Perf Capture"),
    (PERF, "Drawdown", "Perf Drawdown"),
    (QUAL, "Comparison", "Qual Comparison"), (QUAL, "R2000G Quality", "Qual R2000G"),
    (QUAL, "SP600G Quality", "Qual SP600G"), (QUAL, "Cohort Weights", "Qual Cohort Wt"),
    (QUAL, "Sector Mix", "Qual Sector Mix"), (QUAL, "Concentration", "Qual Concentration"),
    (ATTR, "Cohort Contribution", "Attr Contribution"), (ATTR, "Cohort Weights", "Attr Cohort Wt"),
    (ATTR, "Cohort Returns", "Attr Cohort Ret"), (ATTR, "Counterfactual", "Attr Counterfactual"),
    (ATTR, "Reconstruction", "Attr Reconstruction"),
]


def label_row(ws, label):
    for r in range(1, (ws.max_row or 0) + 1):
        v = ws.cell(r, 1).value
        if v and label.lower() in str(v).lower(): return r
    return None


def val(ws, label, col):
    r = label_row(ws, label)
    return ws.cell(r, col).value if r else None


def n(x, d=1):
    try: return round(float(x), d)
    except (TypeError, ValueError): return None


def copy_sheet(src_ws, dst_ws):
    widths = {}
    for row in src_ws.iter_rows():
        for c in row:
            d = dst_ws.cell(row=c.row, column=c.column, value=c.value)
            if c.has_style:
                d.font = copy(c.font); d.fill = copy(c.fill)
                d.alignment = copy(c.alignment); d.number_format = c.number_format
            widths[c.column] = max(widths.get(c.column, 8), min(28, len(str(c.value)) + 2) if c.value else 8)
    from openpyxl.utils import get_column_letter
    for col, w in widths.items():
        dst_ws.column_dimensions[get_column_letter(col)].width = w
    if src_ws.freeze_panes: dst_ws.freeze_panes = src_ws.freeze_panes


def exec_summary(wb, srcs):
    p, q, a = srcs["perf"], srcs["qual"], srcs["attr"]
    ps = p["Summary"]; pt = p["Trailing Returns"]
    qc = q["Comparison"]; qr = q["R2000G Quality"]; qsx = q["SP600G Quality"]
    ac = a["Cohort Contribution"]; acf = a["Counterfactual"]

    cum_r, cum_s = n(val(ps, "Cumulative total return", 2)), n(val(ps, "Cumulative total return", 3))
    ann_r, ann_s = n(val(ps, "Annualized return", 2)), n(val(ps, "Annualized return", 3))
    vol_r, vol_s = n(val(ps, "Annualized volatility", 2)), n(val(ps, "Annualized volatility", 3))
    win_r, win_s = n(val(pt, "Manager window", 2)), n(val(pt, "Manager window", 3))
    win_x = n(val(pt, "Manager window", 4))
    # quality comparison: peak R2KG unprofitable + latest diff
    hr = label_row(qc, "Year")
    rows = [r for r in range(hr + 1, (qc.max_row or hr) + 1) if isinstance(qc.cell(r, 1).value, (int, float))]
    peak_un = peak_yr = None; latest = rows[-1] if rows else None
    for r in rows:
        v = qc.cell(r, 2).value
        if v is not None and (peak_un is None or v > peak_un): peak_un, peak_yr = v, qc.cell(r, 1).value
    diff_latest = n(qc.cell(latest, 4).value) if latest else None
    un_r_latest = n(qc.cell(latest, 2).value) if latest else None
    un_s_latest = n(qc.cell(latest, 3).value) if latest else None
    # never-profitable weight latest (R2000G col 11, SP600G col 11)
    def latest_never(ws):
        h = label_row(ws, "Year")
        rs = [r for r in range(h + 1, (ws.max_row or h) + 1) if isinstance(ws.cell(r, 1).value, (int, float))]
        return n(ws.cell(rs[-1], 11).value) if rs else None
    nev_r, nev_s = latest_never(qr), latest_never(qsx)
    # attribution
    nev_winC = n(val(ac, "Never profitable", 4)); nev_winW = n(val(ac, "Never profitable", 5))
    tot_win = n(val(ac, "TOTAL", 4))
    nev_share = n(100 * nev_winC / tot_win, 0) if (nev_winC and tot_win) else None
    cf_idx_f, cf_prof_f = n(val(acf, "Full period", 2)), n(val(acf, "Full period", 3))
    cf_idx_w, cf_prof_w = n(val(acf, "Manager window", 2)), n(val(acf, "Manager window", 3))
    screen_cost_w = n(cf_idx_w - cf_prof_w, 1) if (cf_idx_w is not None and cf_prof_w is not None) else None
    screen_help_f = n(cf_prof_f - cf_idx_f, 1) if (cf_prof_f is not None and cf_idx_f is not None) else None

    ws = wb.create_sheet("Executive Summary")
    ws.column_dimensions["A"].width = 4; ws.column_dimensions["B"].width = 112
    def line(txt, font=BODY, r=[1]):
        c = ws.cell(row=r[0], column=2, value=txt); c.font = font; c.alignment = Alignment(wrap_text=True, vertical="top"); r[0] += 1
    line("US Small Cap Growth Benchmark Review", TITLE)
    line("Why active managers underperformed the Russell 2000 Growth", H)
    line("")
    line("THE QUESTION", H)
    line("Active US Small Cap Growth managers, benched to the Russell 2000 Growth (R2000G), lagged the "
         "benchmark over the trailing ~2.5 years. This review uses as-filed 10-K fundamentals for both "
         "R2000G and the earnings-screened S&P SmallCap 600 Growth (S&P600G) to explain why.")
    line("")
    line("1. THE STRUCTURAL DIFFERENCE  ->  tab 'Qual Comparison'", H)
    line(f"The S&P 600 requires positive trailing GAAP earnings to enter; R2000G does not. As a result "
         f"R2000G carries a much larger unprofitable tail: in {qc.cell(latest,1).value if latest else 'the latest year'} "
         f"{un_r_latest}% of R2000G by weight was unprofitable vs {un_s_latest}% of S&P600G "
         f"(a {diff_latest}-point gap), and R2000G's unprofitable weight peaked near {peak_un}% in {peak_yr}. "
         f"Never-profitable names are ~{nev_r}% of R2000G by weight vs ~{nev_s}% of S&P600G -- the screen "
         f"all but eliminates that cohort.")
    line("")
    line("2. THE REALIZED COST  ->  tabs 'Attr Contribution', 'Attr Counterfactual'", H)
    line(f"Over the trailing manager window, the never-profitable cohort was ~{nev_winW}% of R2000G by weight "
         f"but produced ~{nev_winC}% of the index's {tot_win}% return -- about {nev_share}% of the gain, "
         f"punching well above its weight. Rebuilding R2000G's OWN names as a profitable-only portfolio "
         f"(the S&P 600-style screen) returned {cf_prof_w}% over the window vs the index's {cf_idx_w}% -- "
         f"i.e. screening for earnings COST ~{screen_cost_w} points exactly when managers were measured.")
    line("")
    line("3. THE LONG-RUN CONTEXT  ->  tabs 'Perf Summary', 'Attr Counterfactual'", H)
    line(f"Over the full period the discipline helped: S&P600G returned {cum_s}% vs R2000G's {cum_r}% "
         f"(annualized {ann_s}% vs {ann_r}%, with lower volatility {vol_s}% vs {vol_r}%), and the profitable-only "
         f"rebuild returned {cf_prof_f}% vs the index's {cf_idx_f}% (+{screen_help_f} pts). The recent window is a "
         f"reversal, not a regime change: R2000G beat S&P600G by {win_x} points over the window "
         f"({win_r}% vs {win_s}%) on the back of its lower-quality tail.")
    line("")
    line("BOTTOM LINE", H)
    line("The underperformance is structural and explainable, not manager skill loss: a quality/earnings "
         "discipline that resembles the S&P 600 Growth lagged because R2000G's unprofitable, often "
         "pre-earnings tail -- absent from a disciplined portfolio -- led the benchmark over the window. "
         "The same discipline added value over the full cycle and carries lower volatility and drawdown.")
    line("")
    line("Methodology: as-filed 10-K values by original accession; point-in-time membership (no look-ahead); "
         "average-denominator ratios; cohort attribution Carino-linked to the index return. See per-tab notes "
         "and the source workbooks (steps 4-6).", Font(italic=True, size=9))
    return ws


def contents(wb, names):
    ws = wb.create_sheet("Contents")
    ws.column_dimensions["A"].width = 26; ws.column_dimensions["B"].width = 70
    ws.cell(1, 1, "Contents").font = TITLE
    groups = [("Performance (R2000G vs S&P 600 Growth)", "Perf "),
              ("Quality & composition", "Qual "), ("Cohort attribution", "Attr ")]
    r = 3
    for title, pre in groups:
        ws.cell(r, 1, title).font = H; r += 1
        for nm in names:
            if nm.startswith(pre):
                ws.cell(r, 2, nm).font = BODY; r += 1
        r += 1


def key_charts(wb):
    ws = wb.create_sheet("Key Charts")
    def line(title, sheet, cols, hdr_row, n_rows, anchor):
        if sheet not in wb.sheetnames: return
        ch = LineChart(); ch.title = title; ch.height, ch.width = 8, 18
        ch.add_data(Reference(wb[sheet], min_col=cols[0], max_col=cols[1], min_row=hdr_row, max_row=hdr_row + n_rows),
                    titles_from_data=True)
        ch.set_categories(Reference(wb[sheet], min_col=1, min_row=hdr_row + 1, max_row=hdr_row + n_rows))
        ws.add_chart(ch, anchor)
    # Qual Comparison: header row 3, ~12 data rows; %unprof NI R2KG(2) vs 600G(3)
    line("% Unprofitable by weight: R2000G vs S&P 600 Growth", "Qual Comparison", (2, 3), 3, 12, "A1")
    # Perf Monthly: header row 1; growth of $1 cols 5,6
    line("Growth of $1: R2000G vs S&P 600 Growth", "Perf Monthly", (5, 6), 1, 132, "A18")
    # Attr Cohort Wt: header row 1; never(4) & unprofitable(6)
    line("R2000G unprofitable-tail weight over time", "Attr Cohort Wt", (4, 4), 1, 131, "A35")
    # Attr Counterfactual: header row 3; index(2), profitable-only(3), ex-never(4)
    line("Earnings-screen counterfactual (growth of $1)", "Attr Counterfactual", (2, 4), 3, 131, "A52")


def main():
    missing = [f.name for f in (PERF, QUAL, ATTR) if not f.exists()]
    if missing:
        raise SystemExit(f"!! missing input workbook(s): {missing}. Run steps 4-6 first.")
    srcwb = {"perf": openpyxl.load_workbook(PERF, data_only=True),
             "qual": openpyxl.load_workbook(QUAL, data_only=True),
             "attr": openpyxl.load_workbook(ATTR, data_only=True)}
    wb = openpyxl.Workbook(); wb.remove(wb.active)

    exec_summary(wb, srcwb)
    names = [nm for _, _, nm in SHEETS]
    contents(wb, names)
    keymap = {PERF: "perf", QUAL: "qual", ATTR: "attr"}
    for src, orig, new in SHEETS:
        sw = srcwb[keymap[src]]
        if orig not in sw.sheetnames:
            print(f"  (skip: '{orig}' not in {src.name})"); continue
        copy_sheet(sw[orig], wb.create_sheet(new[:31]))
    key_charts(wb)
    # explicit tab order: Executive Summary, Contents, Key Charts, then data tabs (SHEETS order)
    front = ["Executive Summary", "Contents", "Key Charts"]
    order = [s for s in front if s in wb.sheetnames] + \
            [nm[:31] for _, _, nm in SHEETS if nm[:31] in wb.sheetnames and nm[:31] not in front]
    wb._sheets.sort(key=lambda s: order.index(s.title) if s.title in order else 999)
    wb.save(OUT)
    print(f"  DONE -> {OUT.name}  ({len(wb.sheetnames)} tabs)")


if __name__ == "__main__":
    main()
