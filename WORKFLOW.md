# R2000G Small Cap Growth Benchmark Review — Workflow

End-to-end run order. Two layers: **(A) build the identity-validated fundamentals from DERA**,
then **(B) run the analytics** (unchanged — they read the same `edgar_annual_fundamentals_ASFILED.csv`).

Run everything from the project folder (`...\Benchmark Analysis`).

---

## Scripts at a glance — what you run vs. what you don't

**RUN — setup (once; re-run only when inputs/holdings change)**
`r2k_morningstar_parse.py` → `r2k_build_maps.py` → `r2k_dera_index.py` → `r2k_dera_extract.py`

**RUN — every data refresh (the recurring run)**
`r2k_dera_classify.py` → `r2k_debt_reconcile.py` → `r2k_plausibility.py` → `r2k_resolve.py` →
`r2k_dera_to_fundamentals.py` → `r2k_step3_analytics.py` →
`r2k_step4_performance.py` → `r2k_step5_cohort_attribution.py` → `r2k_step6_index_comparison.py` →
`r2k_step8_concentration.py` → `r2k_step9_biotech.py` → `r2k_step7_consolidate.py` →
`r2k_refresh_charts_data.py`

> Verification chain: `classify` rebuilds + ties out → `debt_reconcile` + `plausibility` flag what
> doesn't tie / isn't plausible → `resolve` adopts Morningstar where ours breaks and theirs foots
> (provenance in `resolution_audit.csv`) → `to_fundamentals` auto-prefers `fundamentals_dera_resolved.csv`.

**NEVER run directly — shared library** (imported by the steps)
`r2k_perf_io.py`

**RUN only when you need it — utilities / optional QA**
- `r2k_dual_reconstruct_pilot.py` — **dual reconstruction**: rebuild a hard set of filings from BOTH
  DERA (as-filed) and Morningstar (standardized), run the same identity battery on each, and emit a
  per-line 2×2 verdict (LOCKED / DEFINITIONAL_FORK / TRUST_DERA / TRUST_MS / SINGLE_SOURCE). Diagnostic
  only — writes `dual_pilot_report.txt` + `dual_pilot_lines.csv`, touches no production data.
  Run `python r2k_dual_reconstruct_pilot.py` (auto-picks a diverse hard set) or pass `--targets cik:fy,...`.
- `r2k_debt_reconcile.py` — **debt diagnostic**: splits the DERA-vs-Morningstar debt difference into
  FUNDED debt vs OPERATING-LEASE liabilities and reconciles each across the whole universe, by count,
  dollar, and sector. Answers "is DERA funded debt clean, or is the gap just leases?" Writes
  `debt_reconcile_report.txt` + `debt_reconcile_detail.csv` (real funded-debt misses on top). Diagnostic only.
- `r2k_debt_materiality.py` — **weights the debt residual by INDEX WEIGHT** (not name count): what % of
  the R2000G's weight has lease-adjusted debt that ties Morningstar vs forks vs is missing, with the
  highest-weight disagreements listed. Reads `debt_reconcile_detail.csv` + holdings + `security_cik_map.json`.
  Writes `debt_materiality_report.txt`. Diagnostic only.
- `r2k_plausibility.py` — **the verification leg**: catches self-consistent-but-WRONG values the identities
  can't (revenue<0, GM>100%, negative D&A, debt>liabilities, implausible YoY), and combines them with the
  tie-out into a per-name RELIABILITY tier (clean / watch / review), weighted by index weight. Tells you
  what % of index weight is safe to aggregate and ranks the names still needing resolution. Reads
  `fundamentals_dera.csv` (+ holdings/maps). Writes `plausibility_report.txt` + `plausibility_flags.csv`.
- `r2k_snapshot_charts.py` — after you add/edit charts, save them to `R2000G_charts_backup.xlsx`
- `r2k_dera_inspect.py` — peek at the DERA schema / reconstruct a few filings
- `r2k_reconcile_sources.py`, `r2k_calibrate_tags.py`, `r2k_identity_backstop.py` — optional QA vs Morningstar
- `r2k_inventory.py`, `r2k_cleanup.py` — housekeeping (list/classify files; archive old ones)

> **Deprecated — do NOT run:** `r2k_step2_asfiled.py`, `r2k_build_sp600g_universe.py`, and the old
> `r2k_step1*/step2_pull*/step4_validate/step5_tag_audit/step6_verify*/step10/step11`, `build_*`,
> `audit_*` scripts from the earlier project phase. The DERA pipeline above replaces all of them.

---

## 0. Inputs that must be present
- `financial_statement_data_sets/zips/` — DERA quarterly sets (2009q1 … current), each a folder/zip with sub/num/pre/tag.
- Morningstar Income / Balance / Cash workbooks (`*Morningstar*.xlsx`).
- Holdings workbooks: R2000G (`*Russell*Growth*Holding*.xlsx`) and S&P 600 Growth (`*600*Growth*Holding*.xlsx`).
- Performance workbook(s) (`*Performance*.xlsx`).
- FactSet underlying-data workbook (for validation only).

> **Deprecated / do NOT run:** `r2k_step2_asfiled.py` (old companyfacts tag-priority engine),
> `r2k_build_sp600g_universe.py`. Everything they did is now covered by `r2k_build_maps.py` +
> the DERA pipeline below. They remain in the repo only for reference.

---

## A. Build the fundamentals (DERA engine)

| # | Command | Output | When |
|---|---------|--------|------|
| A1 | `python r2k_morningstar_parse.py` | `morningstar_long.csv`, `securities_crosswalk.csv`, `cusip2cik.json`, `ticker2cik.json` | once / when Morningstar refreshed |
| A2 | `python r2k_build_maps.py` | `security_cik_map.json`, `temporal_cik_map.json`, **`universe_ciks.csv`** | once / when holdings change |
| A3 | `python r2k_dera_index.py` | `dera_filing_index.csv` | once / when DERA quarters added |
| A4 | `python r2k_dera_extract.py` | `dera_facts.csv` | once / when DERA quarters added (LONG — streams every quarter) |
| A5 | `python r2k_dera_classify.py` | `fundamentals_dera.csv`, `tieout_report.csv` | each refresh (fast — reads cached facts) |
| A6 | `python r2k_dera_to_fundamentals.py` | **`edgar_annual_fundamentals_ASFILED.csv`** | each refresh (back up the old one first) |

A1–A4 are the one-time setup. After that, a data refresh is just **A5 → A6**.

`r2k_build_maps.py` (A2) reads **both** the R2000G and S&P 600 Growth holdings, so it builds the
ticker→CIK maps for steps 3/5/6 *and* `universe_ciks.csv` (both indices' CIKs). `r2k_dera_index.py`
(A3) **auto-detects `universe_ciks.csv`**, so the DERA dataset covers both indices and step 6's
comparison works with no extra configuration.

---

## B. Run the analytics (unchanged)
Run steps 3, 4, 5, 6, 8, 9 in any order, then 7 (which consolidates them).

| # | Command | Output |
|---|---------|--------|
| B1 | `python r2k_step3_analytics.py` | `Russell2000Growth_Analytics.xlsx` |
| B2 | `python r2k_step4_performance.py` | `R2000G_vs_SP600G_Performance.xlsx` |
| B3 | `python r2k_step5_cohort_attribution.py` | `R2000G_Cohort_Attribution.xlsx` |
| B4 | `python r2k_step6_index_comparison.py` | `R2000G_vs_SP600G_Quality.xlsx` |
| B5 | `python r2k_step8_concentration.py` | `R2000G_Concentration.xlsx` |
| B6 | `python r2k_step9_biotech.py` | `R2000G_Biotech.xlsx` |
| B7 | `python r2k_step7_consolidate.py` | **`R2000G_SmallCapGrowth_Benchmark_Review.xlsx`** (the IC workbook) |

(The old `r2k_build_sp600g_universe.py` is no longer needed — `r2k_build_maps.py` already covers
the S&P 600 Growth names.)

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
- **`tieout_report.csv`** — per-filing **three-statement** accounting-identity tie-outs:
  balance sheet foots (A=L+E+mezz, equity=parent+NCI), income statement cascades (Rev−COGS=GP,
  Pretax−Tax+Disc=Consol NI, Consol−Parent=NCI), and the **cash flow articulates** — CF_FOOT
  (CFO+CFI+CFF+FX=ΔCash), CF_BS_CASH (CF ending cash = BS cash + restricted), and the cross-year
  CASH_ROLL (cash[t]=cash[t−1]+ΔCash[t]). Per-leg tie rates run 95–99.5%; ~88% of filings tie on
  **every** gating leg (a full three-statement articulation). CF legs are judged on materiality
  (1%/$2M reconciliation noise); BS/IS stay tight (0.5%/$5K). CF_BS_CASH is reported but non-gating
  (its restricted-cash decomposition is fragile; CASH_ROLL proves the CF↔BS linkage robustly). The
  `breaks` column flags the rest (M&A cash roll-forward, equity-method/other-income cascade items,
  balance-sheet edge cases); `confidence` = fraction of applicable gating legs that tie.
- **Aggregate sanity check** vs FactSet + Morningstar (gross/net margin validated; operating
  margin is as-filed GAAP, ~2pp below normalized vendor figures — a methodology footnote, not a bug).

---

## Quick reference — a normal data refresh (after the one-time A1–A4)
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
