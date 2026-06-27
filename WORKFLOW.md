# R2000G Small Cap Growth Benchmark Review — Workflow

End-to-end run order. Two layers: **(A) build the identity-validated fundamentals from DERA**,
then **(B) run the analytics** (unchanged — they read the same `edgar_annual_fundamentals_ASFILED.csv`).

Run everything from the project folder (`...\Benchmark Analysis`).

---

## 0. Inputs that must be present
- `financial_statement_data_sets/zips/` — DERA quarterly sets (2009q1 … current), each a folder/zip with sub/num/pre/tag.
- Morningstar Income / Balance / Cash workbooks (`*Morningstar*.xlsx`).
- Holdings workbooks: R2000G (`*Russell*Growth*Holding*.xlsx`) and S&P 600 Growth (`*600*Growth*Holding*.xlsx`).
- Performance workbook(s) (`*Performance*.xlsx`).
- FactSet underlying-data workbook (for validation only).
- **Maps:** `security_cik_map.json`, `temporal_cik_map.json` (holdings ticker→CIK).
  These are produced by `r2k_step2_asfiled.py` / `r2k_build_sp600g_universe.py`. Build once
  (you already have them); rebuild only when holdings change. *(They do NOT depend on the
  fundamental values, so the DERA rebuild does not affect them.)*

---

## A. Build the fundamentals (DERA engine)

| # | Command | Output | When |
|---|---------|--------|------|
| A1 | `python r2k_morningstar_parse.py` | `morningstar_long.csv`, `securities_crosswalk.csv`, `cusip2cik.json`, `ticker2cik.json` | once / when Morningstar refreshed |
| A2 | `python r2k_dera_index.py` | `dera_filing_index.csv` | once / when DERA quarters added |
| A3 | `python r2k_dera_extract.py` | `dera_facts.csv` | once / when DERA quarters added (LONG — streams every quarter) |
| A4 | `python r2k_dera_classify.py` | `fundamentals_dera.csv`, `tieout_report.csv` | each refresh (fast — reads cached facts) |
| A5 | `python r2k_dera_to_fundamentals.py` | **`edgar_annual_fundamentals_ASFILED.csv`** | each refresh (back up the old one first) |

A1–A3 are the one-time heavy lift. After that, a data refresh is just **A4 → A5**.

**Universe coverage (important for step 6):** A2's default target is the Morningstar crosswalk
(R2000-derived). To guarantee the S&P 600 Growth names are present for the step-6 comparison,
either point A2 at a combined CIK list (`R2KG_CIK_FILE=<both universes>`) or index everything
(`R2KG_INDEX_ALL=1`). Verify after A5 that 600G constituents appear in the fundamentals.

---

## B. Run the analytics (unchanged)
Run steps 3, 4, 5, 6, 8, 9 in any order, then 7 (which consolidates them).

| # | Command | Output |
|---|---------|--------|
| B1 | `python r2k_build_sp600g_universe.py` | S&P 600 Growth universe / CIK map (if not already built) |
| B2 | `python r2k_step3_analytics.py` | `Russell2000Growth_Analytics.xlsx` |
| B3 | `python r2k_step4_performance.py` | `R2000G_vs_SP600G_Performance.xlsx` |
| B4 | `python r2k_step5_cohort_attribution.py` | `R2000G_Cohort_Attribution.xlsx` |
| B5 | `python r2k_step6_index_comparison.py` | `R2000G_vs_SP600G_Quality.xlsx` |
| B6 | `python r2k_step8_concentration.py` | `R2000G_Concentration.xlsx` |
| B7 | `python r2k_step9_biotech.py` | `R2000G_Biotech.xlsx` |
| B8 | `python r2k_step7_consolidate.py` | **`R2000G_SmallCapGrowth_Benchmark_Review.xlsx`** (the IC workbook) |

Knobs that affect the analysis: `WINDOW_MONTHS`/`WINDOW_START` (manager window, default trailing 36m
to 4/30/2026), `SNAP_MONTH=4`, `BIOTECH_KEYWORDS`, `RET_MODE=auto`.

---

## C. Charts (preserve formatting)
Re-running step 7 rebuilds charts through openpyxl and degrades formatting. To keep your
hand-formatted charts and only refresh the data:

```
python r2k_refresh_charts_data.py    # data from step-7 output -> your charts backup, header-matched
```
Outputs `R2000G_Benchmark_Review_charted.xlsx`. (Uses `R2000G_charts_backup.xlsx`.)

---

## D. Validation / QA (recommended each refresh)
- **`tieout_report.csv`** — per-filing accounting-identity tie-outs. ~94% tie; the `breaks`
  column flags the rest (immaterial to index aggregates).
- **Aggregate sanity check** vs FactSet + Morningstar (gross/net margin validated; operating
  margin is as-filed GAAP, ~2pp below normalized vendor figures — a methodology footnote, not a bug).

---

## Quick reference — a normal data refresh (after the one-time A1–A3)
```
python r2k_dera_classify.py
python r2k_dera_to_fundamentals.py        # back up edgar_annual_fundamentals_ASFILED.csv first
python r2k_step3_analytics.py
python r2k_step4_performance.py
python r2k_step5_cohort_attribution.py
python r2k_step6_index_comparison.py
python r2k_step8_concentration.py
python r2k_step9_biotech.py
python r2k_step7_consolidate.py
python r2k_refresh_charts_data.py
```
