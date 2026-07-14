# Methodology & Reader's Guide
### Russell 2000 Growth vs. S&P SmallCap 600 Growth — benchmark review

*Companion to `d1bd8ff9-R2000G_SmallCapGrowth_Benchmark_Review.xlsx` (42 tabs) and `R2000G_SCG_Benchmark_Review_Memo.md`. This guide is generated from the workbook's own Reading Guide and Glossary, so the tab list and definitions below always match the workbook; the method narrative is stable process documentation.*

---

## What this review answers

Active US Small-Cap Growth managers, benchmarked to the **Russell 2000 Growth (R2000G)**, lagged that
index over the recent manager window. This review tests one explanation: that the shortfall is
**structural** — a property of how the benchmark is built — rather than a loss of manager skill. It
does so by comparing R2000G against the **S&P SmallCap 600 Growth (S&P600G)**, whose one decisive
difference is an **earnings screen** (a company needs positive trailing GAAP earnings to enter the
S&P 600). A quality- or earnings-disciplined manager's portfolio tends to resemble the S&P600G, so the
gap between the two indices is a clean proxy for the cost — or benefit — of that discipline.

The argument runs in three moves, each with its own tabs: **(1)** R2000G carries a much larger tail of
unprofitable, often pre-revenue companies; **(2)** that tail *led* the benchmark over the exact window
managers were judged on, so avoiding it mechanically caused underperformance; **(3)** over the full
cycle the same discipline *won* — more return at lower risk. The memo (`R2000G_SCG_Benchmark_Review_Memo.md`)
states the current figures; this document explains how each was produced.

## How the fundamentals were built (the data foundation)

Every fundamental in this review is reconstructed from the SEC's own filings, not bought from a vendor
feed, so each number is traceable to an as-filed 10-K.

- **Source: SEC DERA / XBRL Financial Statement Data Sets.** Each quarter's structured facts from every
  10-K, 20-F and 40-F are indexed and extracted, then a classification engine reconstructs all three
  statements (income statement, balance sheet, cash flow) for each company-year.
- **As-filed, by original accession.** Values are taken as *originally* reported in each 10-K — no
  restatement or vintage blending. What the market saw at the time is what the analysis uses.
- **Point-in-time, no look-ahead.** A constituent's fiscal year at any snapshot is the **latest 10-K
  filed before that snapshot** — never financials that were not yet public. Index membership is
  historical (survivorship-free): a name is in a year only if it was actually in the index then.
- **Identity-checked reconstruction.** The engine doesn't just copy tags; it enforces accounting
  identities (the income statement foots to net income, the balance sheet foots, cash-flow D&A ties to
  the income statement) and only accepts a value when the statement ties out. Blanks left by
  non-standard XBRL tags are recovered **identity-first** (reconstruct from what must be true), then
  from validated as-filed tags — a closed loop that shrinks the gap at the source rather than plugging
  numbers.
- **Deliberate, documented adjustments** for cases where the raw filing would mislead an index
  aggregate. The clearest example: a **commodity/securities broker-dealer** (e.g. StoneX) reports
  "Revenues" grossed up by pass-through physical-commodity sales — tens of billions that are not
  comparable to an operating company's revenue and would swamp any index revenue or margin total. Such
  a filer is carried on a **net operating-revenue** basis (revenue net of the pass-through cost), the
  same convention banks and insurers already use, and only where a strict matched-book signature holds
  (tiny gross margin *and* tiny net margin), so genuine low-margin operating companies are untouched.
- **Reliability is measured, not assumed.** The `Data Reliability` tab reports, by index weight, how
  much of the reconstruction ties out cleanly versus is flagged for review, so the reader can weight
  the conclusions. Headline results are dominated by the high-confidence core.

## How the analysis was done (conventions)

- **Two aggregation lenses, stated explicitly.** Index-level figures use the **dollar-aggregate**
  convention (sum of numerators / sum of denominators — the index treated as one big company), which is
  the index-representative measure and is validated against FactSet. Per-name distribution views also
  show weight-weighted-average and median, because tiny-revenue loss-makers distort a simple average.
- **Profitability cohorts are point-in-time labels** from each name's as-filed net-income history:
  *Profitable* (net income > 0 in the latest filed year), *Fallen* (was profitable, now not),
  *Never-profitable* (no profitable year on record), *Unknown* (net income not reported). The
  *never-profitable* weight is the cleanest expression of the earnings-screen gap.
- **Attribution is Carino-linked** so that single-period cohort contributions sum *exactly* to the
  index's multi-period cumulative return — cohort contributions add up to the whole, with a small
  explicitly-labelled reconstruction residual (coverage + weight drift between snapshots).
- **The counterfactual is the cleanest test.** Rather than compare two different indices, it rebuilds
  R2000G's **own** constituents as a "profitable-only" (or ex-biotech) portfolio, reweighted monthly.
  The gap between the real index path and the screened path *is* the realized cost (or benefit) of the
  screen, holding the universe fixed. Its full-period result independently lands near the actual
  S&P600G return — two constructions agreeing that the earnings screen is the mechanism.
- **The manager window is data-driven, not chosen.** The `Perf Window Proof` tab finds when R2000G's
  cumulative excess over S&P600G troughed and began a persistent run, and shows a table of candidate
  windows so the conclusion doesn't hinge on the exact start month.
- **Ratios use average (opening + closing) denominators** (CFA convention); per-name ratios are
  winsorized before any averaging.

---

## Talking through it in one minute

1. **Start with `Qual Comparison` (the "why").** One rule — the S&P 600 earnings screen — creates a
   persistent gap: R2000G runs many more points of unprofitable and never-profitable weight every year.
   `Bio Weight & Quality` shows biotech is the embodiment of that gap.
2. **Move to `Attr Contribution` + `Attr Counterfactual` (the "cost").** Decompose R2000G's return by
   quality cohort: over the manager window the unprofitable tail led. The counterfactual rebuilds
   R2000G's own names profitable-only — the sign flips vs. the full cycle, and that flip *is* the
   manager's shortfall.
3. **Close with `Perf Summary` + `Perf Window Proof` (the "context").** Over the full cycle the
   screened index delivered more return at lower risk; the recent window is the cost of the discipline
   during a low-quality rally, and the window dates are justified from the data, not picked.

**Bottom line:** the underperformance is a benchmark-construction effect, not lost skill — which is why
the S&P SmallCap 600 Growth is often the more representative yardstick for a quality-disciplined mandate.

---

## What each tab shows

### Performance — R2000G vs S&P 600 Growth

| Tab | What it shows | How to read it |
|---|---|---|
| **Perf Summary** | Headline scoreboard for both indices over the full window. | Cumulative & annualized return, volatility, return/vol, max drawdown, and % of months R2000G beat 600G. |
| **Perf Trailing** | Trailing-period returns plus the manager-underperformance window. | Periods >1y are annualized; the manager window is shown CUMULATIVE (the lived gap, not annualized). |
| **Perf Calendar Yr** | Calendar-year total return for each index and the excess. | 2015 (from May) and 2026 (through Apr) are partial years. |
| **Perf Monthly** | Monthly returns and growth-of-$1 paths; basis for the Growth-of-$1 chart. | Cum excess = compounded R2000G-minus-600G monthly difference. |
| **Perf Rolling 12m** | Rolling 12-month return for each index and the rolling excess. | Shows when the relative-performance gap opened and closed. |
| **Perf Capture** | Up/down capture of S&P 600 Growth vs R2000G (R2000G = benchmark), Morningstar geometric-mean convention. | Up capture = per-period geomean of 600G / per-period geomean of R2000G in months R2000G rose (geomean = growth^(1/n)−1); down capture likewise. Displayed geomean-leg columns rebuild the ratio exactly; growth-of-$1 legs rebuild each index's cumulative. |
| **Perf Drawdown** | Peak-to-trough drawdown path for each index. |  |
| **Perf Window Proof** | Data-driven justification for the manager-window dates. | Finds when R2000G's cumulative excess over S&P 600 Growth troughed (its relative low) and began a persistent run; a candidate-window table shows the choice isn't cherry-picked. The trailing-3-year window brackets that rising leg. |

### Quality & composition — the structural 'why'

| Tab | What it shows | How to read it |
|---|---|---|
| **Qual Comparison** | The earnings-screen gap, year by year, side by side. | For each metric: R2000G \| 600G \| Diff (R2000G minus 600G). Positive %unprofitable/%no-revenue diffs = R2000G's larger low-quality tail. |
| **Qual R2000G** | Full as-filed quality profile of R2000G per year. | Coverage, profitability cohorts by weight, margins, ROE/ROIC, growth, leverage. |
| **Qual SP600G** | Same quality profile for the S&P 600 Growth. | Directly comparable to Qual R2000G. |
| **Qual Cohort Wt** | Profitability-cohort weights for both indices side by side. | Profitable / Fallen / Never-profitable as % of index weight; the Never-weight diff is the cleanest screen effect. |
| **Qual Sector Mix** | GICS sector weights for both indices in the latest snapshot. | With the R2000G-minus-600G difference. |
| **Qual Concentration** | Top-N weight, HHI, effective-N for both indices over time. | See the Conc * tabs for the deeper breadth analysis. |

### Cohort attribution — the realized cost

| Tab | What it shows | How to read it |
|---|---|---|
| **Attr Contribution** | R2000G's return split across quality cohorts (Carino-linked). | Contribution columns + 'Unexplained' sum to the index's cumulative return for the full period and the manager window. |
| **Attr Cohort Wt** | Month-by-month cohort weights, incl. the combined unprofitable tail. |  |
| **Attr Cohort Ret** | Month-by-month return of each cohort sub-portfolio vs the index. |  |
| **Attr Counterfactual** | R2000G's own names rebuilt as profitable-only / ex-never-profitable. | The gap between 'R2000G index' and 'Profitable-only' is the realized cost (or benefit) of an earnings screen. |
| **Attr Reconstruction** | Bottom-up vs actual index return — the coverage/accuracy check. | Matched-weight ~95% and small monthly diffs confirm the attribution is trustworthy. |

### Concentration deep-dive (step 8)

| Tab | What it shows | How to read it |
|---|---|---|
| **Conc Weight** | How top-heavy each benchmark is, over time. | Top-10/25 weight, largest single name, HHI, effective number of stocks. |
| **Conc Breadth** | How narrow R2000G's leadership was each year. | % of names positive, % that BEAT the index, cap-weighted-minus-median spread, and the top-10/25 share of the year's gains. |
| **Conc Return** | Who actually drove R2000G over the manager window. | Share of the index's return from the top 10/25/50 names, plus the leading contributors. |

### Biotech deep-dive (step 9)

| Tab | What it shows | How to read it |
|---|---|---|
| **Bio Weight & Quality** | Biotech weight in each index + its quality in R2000G. | Biotech weight R2000G vs 600G over time, and %unprofitable / %no-revenue within R2000G biotech. |
| **Bio In Tail** | How much of R2000G's low-quality tail is biotech / life sciences. | Strict biotech AND broad life-sciences share of the unprofitable and never-profitable weight, by year. |
| **Bio Unprof by Theme** | What makes up the unprofitable tail, by theme, over time. | Shows the tail rotating (biotech + software in 2021 -> biotech + hardware/electrical/semis in 2026). |
| **Bio Unprof by Industry** | The unprofitable tail by Morningstar Industry (top 14 + other). | The detailed breakdown behind the themes; % of each year's unprofitable weight. |
| **Bio Contribution** | Biotech vs non-biotech contribution to R2000G's return. | Carino-linked; compare biotech's contribution share to its weight share. |
| **Bio Counterfactual** | R2000G's own names with biotech removed vs the index. | Ex-biotech and biotech-only growth-of-$1; the gap is biotech's realized swing on the benchmark. |

### R2000G internal trends (step 3 appendix)

| Tab | What it shows | How to read it |
|---|---|---|
| **R2KG Quality Trends** | R2000G's own quality evolution 2015-2026 (three aggregation views). | Weight-weighted, median, and dollar-aggregate views of margins, returns, growth, leverage. |
| **R2KG Prof Cohorts** | R2000G unprofitable cohorts by count and weight over time. | Never (confirmed vs limited-history) vs fallen; profitability persistence. |
| **R2KG DuPont** | Index ROE decomposed: net margin x asset turnover x leverage. | Dollar-aggregate DuPont identity. |
| **R2KG Composition** | What moved the index's quality: within-name vs turnover vs reweighting. | Brinson-style decomposition separating 'the index changed' from 'the same companies changed'. |

### Front matter & standalone exhibits

| Tab | What it shows |
|---|---|
| **Executive Summary** | The one-page version of the whole review: the question, the three-part answer, and the bottom line, each pointing to the tabs that prove it. |
| **Reading Guide** | What each analytical tab shows and how to read it (the source of the tab guide below). |
| **Glossary** | Plain-language definition of every metric and convention used in the workbook. |
| **Contents** | The tab index, grouped by section. |
| **Key Charts** | The exhibit charts (Growth of $1, % unprofitable by weight, the earnings-screen counterfactual) in one place. |
| **Data Reliability** | How trustworthy the reconstructed fundamentals are, by weight: the share of index weight whose three statements tie out cleanly vs. flagged for review, so a reader can weight the conclusions accordingly. |
| **Quality Factor Spreads** | R2000G-vs-S&P600G spread on each quality factor (profitability, accruals, gross-profitability, leverage) -- the factor view of the same quality gap. |
| **Solvency Tail** | The distressed/low-solvency tail of each index (interest coverage, leverage, cash burn) -- the balance-sheet counterpart to the earnings tail. |
| **Cohort Persistence** | Whether a name's profitability label sticks year to year -- how much of the never-profitable tail is structurally, not transiently, unprofitable. |
| **Valuation of the Tail** | What the market pays for the unprofitable tail (sales multiples, price/book) -- context for whether the tail's leadership was a re-rating. |

---

## Glossary

| Term | Definition | Notes |
|---|---|---|
| **Profitability cohort** | Point-in-time label from as-filed net income history. | Profitable = NI>0 in the latest filed FY; Fallen = NI<=0 now but profitable in a prior year; Never-profitable = no profitable year on record; Unknown = NI not reported. |
| **Unprofitable by weight** | Index weight in names with negative net income (or operating income). | The headline structural difference; OI version strips below-the-line items. |
| **No-revenue weight** | Index weight in names reporting no/zero revenue. | Pre-commercial / development-stage companies. |
| **Point-in-time (no look-ahead)** | Each name's fiscal year = the latest 10-K FILED before the snapshot. | Avoids using financials that weren't yet public; survivorship-free membership. |
| **As-filed** | Values as originally reported in each 10-K (by original accession). | No restatement/vintage blending. |
| **Weight-weighted average (wavg)** | Index-weight-weighted mean of a per-company ratio. | Per-name ratios winsorized before averaging. |
| **Median** | The typical (middle) constituent's value. | Robust to outliers; pairs with wavg to show skew. |
| **Dollar-aggregate ($agg)** | Sum of numerators / sum of denominators across the index. | E.g. ROE $agg = total net income / total equity; treats the index as one big company. |
| **ROE / ROA / ROIC** | Return on equity / assets / invested capital. | Average (opening+closing) denominators (CFA convention). ROIC uses NOPAT / invested capital. |
| **Operating / Net / Gross margin** | Operating income, net income, gross profit as a % of revenue. |  |
| **Rev YoY / 3y CAGR** | One-year and three-year compound revenue growth. |  |
| **D/Capital, D/Equity** | Total interest-bearing debt / (debt+equity), and / equity. | Operating leases excluded from debt. |
| **GP/Assets** | Gross profit / average assets (Novy-Marx gross profitability). | A robust quality signal. |
| **Accruals** | (Net income - operating cash flow) / average assets (Sloan). | High accruals = lower earnings quality. |
| **Cash conversion** | Operating cash flow / net income. | How much reported profit shows up as cash. |
| **Biotech** | Holdings whose Morningstar Industry contains 'biotech'. | Clinical-stage / pre-revenue names; overwhelmingly unprofitable, so largely excluded by the S&P 600 earnings screen. |
| **HHI** | Herfindahl index = sum of squared percent weights. | Higher = more concentrated. |
| **Effective N** | 1 / sum(weight share squared). | The number of equal-weight names that would give the same concentration. |
| **% Beat index** | Share of constituents whose calendar-year return exceeded the index return. | Low values = narrow leadership, a headwind for diversified active managers. |
| **Cap-wtd minus median (spread)** | Cap-weighted return minus the median stock's return. | Positive = a few large winners pulled the index above the typical name. |
| **Carino linking** | A smoothing method so single-period contributions sum to a multi-period total. | Lets cohort contributions add up exactly to the index's cumulative return. |
| **Up / Down capture** | Compounded portfolio return / compounded benchmark return in up / down benchmark months. | R2000G is treated as the benchmark. |
| **Manager window** | The period over which active managers were measured -- trailing 3 years to 4/30/2026. | Default trailing 36 months (May 2023-Apr 2026); set WINDOW_START to pin an exact date. Shown cumulative. See the 'Perf Window Proof' tab for the data-driven justification of these dates. |

---

*Generated by `r2k_methodology.py` from the workbook. Re-run after any workbook change so the tab guide and glossary stay in sync.*
