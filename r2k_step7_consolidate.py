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
ANALYTICS = BASE / "Russell2000Growth_Analytics.xlsx"   # step 3 (optional)
CONC = BASE / "R2000G_Concentration.xlsx"               # step 8 (optional)
BIO = BASE / "R2000G_Biotech.xlsx"                       # step 9 (optional)
OUT = BASE / "R2000G_SmallCapGrowth_Benchmark_Review.xlsx"
CHART_BACKUP = BASE / os.environ.get("R2KG_CHART_BACKUP", "R2000G_charts_backup.xlsx")
FLAGS = BASE / "plausibility_flags.csv"      # reliability tier per (cik, fy)  -- from r2k_plausibility
FUND = BASE / "fundamentals_dera.csv"        # confidence / breaks / provenance -- from r2k_dera_classify

TITLE = Font(bold=True, size=14, color="1F4E5F")
H = Font(bold=True, size=11, color="1F4E5F")
BODY = Font(size=10)
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)

# (source path, original sheet, new name)  -- skip per-file README/Charts
SHEETS = [
    (PERF, "Summary", "Perf Summary"), (PERF, "Trailing Returns", "Perf Trailing"),
    (PERF, "Calendar Year", "Perf Calendar Yr"), (PERF, "Monthly & Cumulative", "Perf Monthly"),
    (PERF, "Rolling 12m Excess", "Perf Rolling 12m"), (PERF, "Up-Down Capture", "Perf Capture"),
    (PERF, "Drawdown", "Perf Drawdown"), (PERF, "Window Proof", "Perf Window Proof"),
    (QUAL, "Comparison", "Qual Comparison"), (QUAL, "R2000G Quality", "Qual R2000G"),
    (QUAL, "SP600G Quality", "Qual SP600G"), (QUAL, "Cohort Weights", "Qual Cohort Wt"),
    (QUAL, "Sector Mix", "Qual Sector Mix"), (QUAL, "Concentration", "Qual Concentration"),
    (ATTR, "Cohort Contribution", "Attr Contribution"), (ATTR, "Cohort Weights", "Attr Cohort Wt"),
    (ATTR, "Cohort Returns", "Attr Cohort Ret"), (ATTR, "Counterfactual", "Attr Counterfactual"),
    (ATTR, "Reconstruction", "Attr Reconstruction"),
    # ---- concentration deep-dive (step 8, optional) ----
    (CONC, "Weight Concentration", "Conc Weight"), (CONC, "Return Breadth", "Conc Breadth"),
    (CONC, "Return Concentration", "Conc Return"),
    # ---- biotech deep-dive (step 9, optional) ----
    (BIO, "Biotech Weight & Quality", "Bio Weight & Quality"), (BIO, "Biotech in the Tail", "Bio In Tail"),
    (BIO, "Unprofitable by Theme", "Bio Unprof by Theme"), (BIO, "Unprofitable by Industry", "Bio Unprof by Industry"),
    (BIO, "Biotech Contribution", "Bio Contribution"), (BIO, "Biotech Counterfactual", "Bio Counterfactual"),
    # ---- R2000G internal quality trends (step 3, optional appendix) ----
    (ANALYTICS, "Index Quality Trends", "R2KG Quality Trends"),
    (ANALYTICS, "Profitability Cohorts", "R2KG Prof Cohorts"),
    (ANALYTICS, "DuPont", "R2KG DuPont"), (ANALYTICS, "Composition Change", "R2KG Composition"),
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
    # biotech line (only if the step-9 source is present)
    if "bio" in srcs:
        bq = srcs["bio"]["Biotech Weight & Quality"]; bt = srcs["bio"]["Biotech in the Tail"]
        bcf = srcs["bio"]["Biotech Counterfactual"]
        def last_data_row(ws):
            rs = [r for r in range(1, (ws.max_row or 1) + 1) if isinstance(ws.cell(r, 1).value, (int, float))]
            return rs[-1] if rs else None
        lb = last_data_row(bq); lt = last_data_row(bt)
        bio_r = n(bq.cell(lb, 2).value) if lb else None      # R2KG biotech wt
        bio_s = n(bq.cell(lb, 6).value) if lb else None      # 600G biotech wt
        bio_unp = n(bq.cell(lb, 4).value) if lb else None    # %unprofitable within biotech
        bio_nev = n(bt.cell(lt, 7).value) if lt else None    # biotech share of never-prof
        cf_idx_w2 = n(val(bcf, "Manager window", 2)); cf_exb_w = n(val(bcf, "Manager window", 3))
        bio_cost = n(cf_idx_w2 - cf_exb_w, 1) if (cf_idx_w2 is not None and cf_exb_w is not None) else None
        line(f"Biotech makes this concrete: ~{bio_r}% of R2000G vs ~{bio_s}% of S&P600G, ~{bio_unp}% of it "
             f"unprofitable, and ~{bio_nev}% of R2000G's never-profitable weight. Removing biotech from "
             f"R2000G's own names lowers the window return from {cf_idx_w2}% to {cf_exb_w}% "
             f"(~{bio_cost} pts a disciplined manager would have missed). See the 'Bio *' tabs.")
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


TAB_GUIDE = [
    ("Performance — R2000G vs S&P 600 Growth", None, None),
    ("Perf Summary", "Headline scoreboard for both indices over the full window.",
     "Cumulative & annualized return, volatility, return/vol, max drawdown, and % of months R2000G beat 600G."),
    ("Perf Trailing", "Trailing-period returns plus the manager-underperformance window.",
     "Periods >1y are annualized; the manager window is shown CUMULATIVE (the lived gap, not annualized)."),
    ("Perf Calendar Yr", "Calendar-year total return for each index and the excess.",
     "2015 (from May) and 2026 (through Apr) are partial years."),
    ("Perf Monthly", "Monthly returns and growth-of-$1 paths; basis for the Growth-of-$1 chart.",
     "Cum excess = compounded R2000G-minus-600G monthly difference."),
    ("Perf Rolling 12m", "Rolling 12-month return for each index and the rolling excess.",
     "Shows when the relative-performance gap opened and closed."),
    ("Perf Capture", "Up/down capture of S&P 600 Growth vs R2000G (R2000G = benchmark).",
     "Up capture = compounded 600G return / compounded R2000G return in months R2000G rose; down capture likewise."),
    ("Perf Drawdown", "Peak-to-trough drawdown path for each index.", ""),
    ("Perf Window Proof", "Data-driven justification for the manager-window dates.",
     "Finds when R2000G's cumulative excess over S&P 600 Growth troughed (its relative low) and began a "
     "persistent run; a candidate-window table shows the choice isn't cherry-picked. The trailing-3-year "
     "window brackets that rising leg."),
    ("Quality & composition — the structural 'why'", None, None),
    ("Qual Comparison", "The earnings-screen gap, year by year, side by side.",
     "For each metric: R2000G | 600G | Diff (R2000G minus 600G). Positive %unprofitable/%no-revenue diffs = R2000G's larger low-quality tail."),
    ("Qual R2000G", "Full as-filed quality profile of R2000G per year.",
     "Coverage, profitability cohorts by weight, margins, ROE/ROIC, growth, leverage."),
    ("Qual SP600G", "Same quality profile for the S&P 600 Growth.", "Directly comparable to Qual R2000G."),
    ("Qual Cohort Wt", "Profitability-cohort weights for both indices side by side.",
     "Profitable / Fallen / Never-profitable as % of index weight; the Never-weight diff is the cleanest screen effect."),
    ("Qual Sector Mix", "GICS sector weights for both indices in the latest snapshot.", "With the R2000G-minus-600G difference."),
    ("Qual Concentration", "Top-N weight, HHI, effective-N for both indices over time.", "See the Conc * tabs for the deeper breadth analysis."),
    ("Cohort attribution — the realized cost", None, None),
    ("Attr Contribution", "R2000G's return split across quality cohorts (Carino-linked).",
     "Contribution columns + 'Unexplained' sum to the index's cumulative return for the full period and the manager window."),
    ("Attr Cohort Wt", "Month-by-month cohort weights, incl. the combined unprofitable tail.", ""),
    ("Attr Cohort Ret", "Month-by-month return of each cohort sub-portfolio vs the index.", ""),
    ("Attr Counterfactual", "R2000G's own names rebuilt as profitable-only / ex-never-profitable.",
     "The gap between 'R2000G index' and 'Profitable-only' is the realized cost (or benefit) of an earnings screen."),
    ("Attr Reconstruction", "Bottom-up vs actual index return — the coverage/accuracy check.",
     "Matched-weight ~95% and small monthly diffs confirm the attribution is trustworthy."),
    ("Concentration deep-dive (step 8)", None, None),
    ("Conc Weight", "How top-heavy each benchmark is, over time.",
     "Top-10/25 weight, largest single name, HHI, effective number of stocks."),
    ("Conc Breadth", "How narrow R2000G's leadership was each year.",
     "% of names positive, % that BEAT the index, cap-weighted-minus-median spread, and the top-10/25 share of the year's gains."),
    ("Conc Return", "Who actually drove R2000G over the manager window.",
     "Share of the index's return from the top 10/25/50 names, plus the leading contributors."),
    ("Biotech deep-dive (step 9)", None, None),
    ("Bio Weight & Quality", "Biotech weight in each index + its quality in R2000G.",
     "Biotech weight R2000G vs 600G over time, and %unprofitable / %no-revenue within R2000G biotech."),
    ("Bio In Tail", "How much of R2000G's low-quality tail is biotech / life sciences.",
     "Strict biotech AND broad life-sciences share of the unprofitable and never-profitable weight, by year."),
    ("Bio Unprof by Theme", "What makes up the unprofitable tail, by theme, over time.",
     "Shows the tail rotating (biotech + software in 2021 -> biotech + hardware/electrical/semis in 2026)."),
    ("Bio Unprof by Industry", "The unprofitable tail by Morningstar Industry (top 14 + other).",
     "The detailed breakdown behind the themes; % of each year's unprofitable weight."),
    ("Bio Contribution", "Biotech vs non-biotech contribution to R2000G's return.",
     "Carino-linked; compare biotech's contribution share to its weight share."),
    ("Bio Counterfactual", "R2000G's own names with biotech removed vs the index.",
     "Ex-biotech and biotech-only growth-of-$1; the gap is biotech's realized swing on the benchmark."),
    ("R2000G internal trends (step 3 appendix)", None, None),
    ("R2KG Quality Trends", "R2000G's own quality evolution 2015-2026 (three aggregation views).",
     "Weight-weighted, median, and dollar-aggregate views of margins, returns, growth, leverage."),
    ("R2KG Prof Cohorts", "R2000G unprofitable cohorts by count and weight over time.",
     "Never (confirmed vs limited-history) vs fallen; profitability persistence."),
    ("R2KG DuPont", "Index ROE decomposed: net margin x asset turnover x leverage.", "Dollar-aggregate DuPont identity."),
    ("R2KG Composition", "What moved the index's quality: within-name vs turnover vs reweighting.",
     "Brinson-style decomposition separating 'the index changed' from 'the same companies changed'."),
]

GLOSSARY = [
    ("Profitability cohort", "Point-in-time label from as-filed net income history.",
     "Profitable = NI>0 in the latest filed FY; Fallen = NI<=0 now but profitable in a prior year; "
     "Never-profitable = no profitable year on record; Unknown = NI not reported."),
    ("Unprofitable by weight", "Index weight in names with negative net income (or operating income).",
     "The headline structural difference; OI version strips below-the-line items."),
    ("No-revenue weight", "Index weight in names reporting no/zero revenue.", "Pre-commercial / development-stage companies."),
    ("Point-in-time (no look-ahead)", "Each name's fiscal year = the latest 10-K FILED before the snapshot.",
     "Avoids using financials that weren't yet public; survivorship-free membership."),
    ("As-filed", "Values as originally reported in each 10-K (by original accession).", "No restatement/vintage blending."),
    ("Weight-weighted average (wavg)", "Index-weight-weighted mean of a per-company ratio.", "Per-name ratios winsorized before averaging."),
    ("Median", "The typical (middle) constituent's value.", "Robust to outliers; pairs with wavg to show skew."),
    ("Dollar-aggregate ($agg)", "Sum of numerators / sum of denominators across the index.",
     "E.g. ROE $agg = total net income / total equity; treats the index as one big company."),
    ("ROE / ROA / ROIC", "Return on equity / assets / invested capital.",
     "Average (opening+closing) denominators (CFA convention). ROIC uses NOPAT / invested capital."),
    ("Operating / Net / Gross margin", "Operating income, net income, gross profit as a % of revenue.", ""),
    ("Rev YoY / 3y CAGR", "One-year and three-year compound revenue growth.", ""),
    ("D/Capital, D/Equity", "Total interest-bearing debt / (debt+equity), and / equity.", "Operating leases excluded from debt."),
    ("GP/Assets", "Gross profit / average assets (Novy-Marx gross profitability).", "A robust quality signal."),
    ("Accruals", "(Net income - operating cash flow) / average assets (Sloan).", "High accruals = lower earnings quality."),
    ("Cash conversion", "Operating cash flow / net income.", "How much reported profit shows up as cash."),
    ("Biotech", "Holdings whose Morningstar Industry contains 'biotech'.",
     "Clinical-stage / pre-revenue names; overwhelmingly unprofitable, so largely excluded by the S&P 600 earnings screen."),
    ("HHI", "Herfindahl index = sum of squared percent weights.", "Higher = more concentrated."),
    ("Effective N", "1 / sum(weight share squared).", "The number of equal-weight names that would give the same concentration."),
    ("% Beat index", "Share of constituents whose calendar-year return exceeded the index return.",
     "Low values = narrow leadership, a headwind for diversified active managers."),
    ("Cap-wtd minus median (spread)", "Cap-weighted return minus the median stock's return.",
     "Positive = a few large winners pulled the index above the typical name."),
    ("Carino linking", "A smoothing method so single-period contributions sum to a multi-period total.",
     "Lets cohort contributions add up exactly to the index's cumulative return."),
    ("Up / Down capture", "Compounded portfolio return / compounded benchmark return in up / down benchmark months.",
     "R2000G is treated as the benchmark."),
    ("Manager window", "The period over which active managers were measured -- trailing 3 years to 4/30/2026.",
     "Default trailing 36 months (May 2023-Apr 2026); set WINDOW_START to pin an exact date. Shown cumulative. "
     "See the 'Perf Window Proof' tab for the data-driven justification of these dates."),
]


def reading_guide(wb):
    ws = wb.create_sheet("Reading Guide")
    ws.column_dimensions["A"].width = 24; ws.column_dimensions["B"].width = 62; ws.column_dimensions["C"].width = 70
    ws.cell(1, 1, "Reading Guide — what each tab shows").font = TITLE
    _hdr_row(ws, 3, ["Tab", "What it shows", "How to read it"])
    r = 4
    for a, b, c in TAB_GUIDE:
        if b is None:                       # section header
            cell = ws.cell(r, 1, a); cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="7A3B2E")
            for cc in (2, 3): ws.cell(r, cc).fill = PatternFill("solid", fgColor="7A3B2E")
        else:
            ws.cell(r, 1, a).font = Font(bold=True, size=10)
            ws.cell(r, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
            ws.cell(r, 3, c).alignment = Alignment(wrap_text=True, vertical="top")
        r += 1
    ws.freeze_panes = "A4"


def glossary(wb):
    ws = wb.create_sheet("Glossary")
    ws.column_dimensions["A"].width = 28; ws.column_dimensions["B"].width = 60; ws.column_dimensions["C"].width = 64
    ws.cell(1, 1, "Glossary — metric definitions").font = TITLE
    _hdr_row(ws, 3, ["Metric", "Definition", "Notes"])
    for i, (a, b, c) in enumerate(GLOSSARY, 4):
        ws.cell(i, 1, a).font = Font(bold=True, size=10)
        ws.cell(i, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(i, 3, c).alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A4"


def _hdr_row(ws, row, headers):
    for c, h in enumerate(headers, 1):
        x = ws.cell(row, c, h); x.fill = HDR; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)


def contents(wb, names):
    ws = wb.create_sheet("Contents")
    ws.column_dimensions["A"].width = 26; ws.column_dimensions["B"].width = 70
    ws.cell(1, 1, "Contents").font = TITLE
    groups = [("Performance (R2000G vs S&P 600 Growth)", "Perf "),
              ("Quality & composition", "Qual "), ("Cohort attribution", "Attr "),
              ("Concentration & breadth", "Conc "), ("Biotech deep-dive", "Bio "),
              ("R2000G internal trends (appendix)", "R2KG ")]
    r = 3
    for title, pre in groups:
        ws.cell(r, 1, title).font = H; r += 1
        for nm in names:
            if nm.startswith(pre):
                ws.cell(r, 2, nm).font = BODY; r += 1
        r += 1


def preserve_charts(wb):
    """Lift hand-made charts (e.g. Claude Excel plugin work) from the existing output --
    or a backup snapshot -- onto the rebuilt tabs, so re-running never destroys them.
    Charts reference sheets by name + cell range, which stay valid because every tab is
    rebuilt with the SAME layout. Returns (n_moved, source_name)."""
    src = OUT if OUT.exists() else (CHART_BACKUP if CHART_BACKUP.exists() else None)
    if src is None:
        return 0, None
    try:
        old = openpyxl.load_workbook(src)
    except Exception as e:
        print(f"  (could not read existing charts from {src.name}: {e})")
        return 0, None
    moved = 0
    for s in wb.sheetnames:
        if s in old.sheetnames:
            for ch in list(getattr(old[s], "_charts", [])):
                wb[s].add_chart(ch); moved += 1
    return moved, src.name


def key_charts(wb):
    ws = wb["Key Charts"] if "Key Charts" in wb.sheetnames else wb.create_sheet("Key Charts")
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
    # Bio Weight & Quality: header row 3; R2KG biotech wt (col2) and 600G biotech wt (col6) -- non-contiguous
    if "Bio Weight & Quality" in wb.sheetnames:
        bs = wb["Bio Weight & Quality"]
        last = max([r for r in range(4, bs.max_row + 1) if isinstance(bs.cell(r, 1).value, (int, float))], default=3)
        ch = LineChart(); ch.title = "Biotech weight: R2000G vs S&P 600 Growth"; ch.height, ch.width = 8, 18
        ch.add_data(Reference(bs, min_col=2, max_col=2, min_row=3, max_row=last), titles_from_data=True)
        ch.add_data(Reference(bs, min_col=6, max_col=6, min_row=3, max_row=last), titles_from_data=True)
        ch.set_categories(Reference(bs, min_col=1, min_row=4, max_row=last))
        ws.add_chart(ch, "A69")


def data_reliability(wb):
    """A per-name DATA RELIABILITY tab: for the latest snapshot of every constituent, its reliability
    tier (clean/watch/review), three-statement tie-out confidence, which identities break, the
    plausibility flags, and the provenance of any engineered values -- so the Committee can see, at a
    glance, that each number is tied, sanity-checked, and auditable. Degrades gracefully if inputs are
    absent. Returns the sheet name (or None)."""
    import csv as _csv, json as _json
    if not FLAGS.exists():
        return None
    flags = list(_csv.DictReader(open(FLAGS, encoding="utf-8")))
    # latest snapshot row per cik
    latest = {}
    for r in flags:
        c, fy = r.get("cik"), r.get("fiscal_year", "")
        if fy.isdigit() and (c not in latest or fy > latest[c]["fiscal_year"]):
            latest[c] = r
    # provenance + breaks from fundamentals (latest year per cik)
    prov = {}
    if FUND.exists():
        for r in _csv.DictReader(open(FUND, encoding="utf-8")):
            c, fy = r.get("cik"), r.get("fiscal_year", "")
            if fy.isdigit() and (c not in prov or fy > prov[c]["fiscal_year"]):
                prov[c] = r
    # index weight + ticker (optional, via r2k_plausibility loaders + the cik map)
    cik2w, cik2tkr = {}, {}
    try:
        from r2k_plausibility import load_weights, load_cikmap
        weights, cm = load_weights(), load_cikmap()
        for nt, w in (weights or {}).items():
            c = cm.get(nt)
            if c:
                cik2w[c] = w
    except Exception:
        pass
    cmap = BASE / "security_cik_map.json"
    if cmap.exists():
        try:
            for tk, v in _json.load(open(cmap)).items():
                c = v.get("cik") if isinstance(v, dict) else v
                c = str(int(c)) if str(c).isdigit() else str(c)
                cik2tkr.setdefault(c, tk)
        except Exception:
            pass

    def _short(s, n=180):
        return s if len(s) <= n else s[:n - 1] + "…"

    rows = []
    for c, fr in latest.items():
        pr = prov.get(c, {})
        rows.append({
            "tkr": cik2tkr.get(c, ""), "cik": c, "wt": cik2w.get(c, 0.0),
            "fy": fr.get("fiscal_year", ""), "tier": fr.get("tier", ""),
            "conf": fr.get("confidence", "") or pr.get("confidence", ""),
            "breaks": pr.get("breaks", ""),
            "flags": ";".join(x for x in (fr.get("critical", ""), fr.get("watch", "")) if x),
            "prov": _short(pr.get("provenance", "")),
        })
    # focus on the CURRENT index constituents (those carrying weight); the full universe -- S&P 600
    # Growth comparison names and former holdings -- is in fundamentals_dera.csv / plausibility_flags.csv.
    if any(r["wt"] for r in rows):
        rows = [r for r in rows if r["wt"] > 0]
    rows.sort(key=lambda x: (-x["wt"], x["cik"]))
    tw = sum(r["wt"] for r in rows) or 0.0
    clean_w = sum(r["wt"] for r in rows if r["tier"] == "clean")
    from collections import Counter as _Counter
    tc = _Counter(r["tier"] for r in rows)

    ws = wb.create_sheet("Data Reliability")
    ws.cell(1, 1, "Data Reliability — three-statement tie-out, sanity, and provenance").font = TITLE
    sub = (f"{len(rows):,} index constituents (latest fiscal year). Reliability = identities tie (three "
           f"statements articulate) AND values are plausible. "
           + (f"{100*clean_w/tw:.1f}% of index weight is CLEAN." if tw else
              "(index weights unavailable — name-count view.)"))
    ws.cell(2, 1, sub).font = BODY
    ws.cell(3, 1, f"by name:  clean {tc['clean']}   |   watch {tc['watch']}   |   review {tc['review']}"
                  "        clean = ties out and plausible · watch = a cash-flow leg or benign flag · "
                  "review = a P&L/balance-sheet break or critical flag (resolve before relying on it)"
            ).font = Font(size=9, italic=True, color="555555")
    headers = ["Ticker", "CIK", "Index Wt %", "Latest FY", "Reliability", "Tie-out conf",
               "Identity breaks", "Plausibility flags", "Provenance (engineered values)"]
    _hdr_row(ws, 5, headers)
    tier_fill = {"clean": PatternFill("solid", fgColor="E2EFDA"),
                 "watch": PatternFill("solid", fgColor="FFF2CC"),
                 "review": PatternFill("solid", fgColor="FCE4D6")}
    for i, r in enumerate(rows, start=6):
        ws.cell(i, 1, r["tkr"]); ws.cell(i, 2, r["cik"])
        ws.cell(i, 3, round(r["wt"], 3) if r["wt"] else None)
        ws.cell(i, 4, r["fy"])
        tc = ws.cell(i, 5, r["tier"]); tc.fill = tier_fill.get(r["tier"], PatternFill())
        ws.cell(i, 6, r["conf"]); ws.cell(i, 7, r["breaks"])
        ws.cell(i, 8, r["flags"]); ws.cell(i, 9, r["prov"])
        for col in range(1, 10):
            ws.cell(i, col).font = BODY
    widths = [10, 12, 11, 10, 12, 12, 24, 26, 60]
    for col, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + col)].width = w
    ws.freeze_panes = "A6"
    print(f"  Data Reliability tab: {len(rows)} names"
          + (f", {100*clean_w/tw:.1f}% weight clean" if tw else ""))
    return "Data Reliability"


def main():
    missing = [f.name for f in (PERF, QUAL, ATTR) if not f.exists()]
    if missing:
        raise SystemExit(f"!! missing input workbook(s): {missing}. Run steps 4-6 first.")
    paths = {PERF: "perf", QUAL: "qual", ATTR: "attr", ANALYTICS: "analytics", CONC: "conc", BIO: "bio"}
    srcwb = {tag: openpyxl.load_workbook(p, data_only=True) for p, tag in paths.items() if p.exists()}
    for p, tag in paths.items():
        if not p.exists() and tag in ("analytics", "conc", "bio"):
            print(f"  (optional source not found, skipping: {p.name})")
    wb = openpyxl.Workbook(); wb.remove(wb.active)

    exec_summary(wb, srcwb)
    reading_guide(wb); glossary(wb)
    rel_tab = data_reliability(wb)
    if rel_tab is None:
        print("  (Data Reliability tab skipped: plausibility_flags.csv not found -- run r2k_plausibility.py)")
    copied = []
    for src, orig, new in SHEETS:
        tag = paths[src]
        if tag not in srcwb: continue
        sw = srcwb[tag]
        if orig not in sw.sheetnames:
            print(f"  (skip: '{orig}' not in {src.name})"); continue
        copy_sheet(sw[orig], wb.create_sheet(new[:31])); copied.append(new[:31])
    wb.create_sheet("Key Charts")          # home for charts (always present)
    contents(wb, copied)
    # preserve hand-made charts from the existing file; only auto-generate on a true first run
    moved, csrc = preserve_charts(wb)
    if moved:
        print(f"  preserved {moved} existing charts from {csrc} (skipped auto Key Charts)")
    else:
        key_charts(wb)
    # explicit tab order: Exec Summary, Reading Guide, Glossary, Contents, Key Charts, then data tabs
    front = ["Executive Summary", "Reading Guide", "Glossary", "Data Reliability", "Contents", "Key Charts"]
    order = [s for s in front if s in wb.sheetnames] + [nm for nm in copied if nm not in front]
    wb._sheets.sort(key=lambda s: order.index(s.title) if s.title in order else 999)
    wb.save(OUT)
    print(f"  DONE -> {OUT.name}  ({len(wb.sheetnames)} tabs)")


if __name__ == "__main__":
    main()
