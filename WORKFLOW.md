# R2000G Small Cap Growth Benchmark Review — Workflow

Code-by-code run order for the whole project, from raw SEC DERA filings to the Investment
Committee workbook.

**A note on the naming.** The `stepN_*` scripts are the original **analytics pipeline** — numbered
stages that turn the fundamentals into the IC workbook. Everything else (`dera_*`, `*_probe`,
`audit`, `plausibility`, …) is the **data engine and diagnostics** built underneath and around it.
They aren't numbered because they either run *before* the steps (building the data) or *on demand*
(investigating data quality) — they are not part of the linear stage sequence.

Run everything from the project folder.

---

## PHASE 1 — One-time setup (re-run only when the underlying inputs change)

| # | Command | Produces | When |
|---|---------|----------|------|
| 1 | `python r2k_morningstar_parse.py` | `morningstar_long.csv`, `securities_crosswalk.csv`, `cusip2cik.json`, `ticker2cik.json` | Morningstar workbooks refreshed |
| 2 | `python r2k_build_maps.py` | `security_cik_map.json`, `temporal_cik_map.json`, **`universe_ciks.csv`** | holdings change |
| 3 | `python r2k_dera_index.py` | `dera_filing_index.csv` (every 10-K / 20-F / 40-F) | DERA quarters added |
| 4 | `python r2k_dera_extract.py` | **`dera_facts.csv`** (all as-filed facts) | DERA quarters added — **LONG (streams every quarter)** |

`r2k_build_maps.py` reads **both** the R2000G and S&P 600 Growth holdings, so it builds the
ticker→CIK maps for steps 3/5/6 *and* `universe_ciks.csv`. `r2k_dera_index.py` auto-detects
`universe_ciks.csv`, so the DERA dataset covers both indices with no extra configuration.

---

## PHASE 2 — Data build (the recurring run — the accounting engine)

| # | Command | Produces |
|---|---------|----------|
| 5 | `python r2k_dera_classify.py` | **`fundamentals_dera.csv`** + **`tieout_report.csv`** |
| 6 | `python r2k_dera_to_fundamentals.py` | **`edgar_annual_fundamentals_ASFILED.csv`** (back up the old one first) |

**`r2k_dera_classify.py` is the accounting brain.** It reconstructs all three statements with
tag-informed, identity-driven value selection (the tag supplies the candidate components; the
accounting identity picks the value that makes the statements articulate; every adoption is
double-count-safe and never plugs a vendor number). It emits, per filing: the standardized
cascade, ~15 identity tie-outs, structural debt (funded + lease-adjusted, incl. fleet/floorplan),
and a **`provenance`** column recording how every engineered value was built. Self-test:
`python r2k_dera_classify.py --selftest`.

`r2k_dera_to_fundamentals.py` auto-prefers `fundamentals_dera_resolved.csv` if present
(see `r2k_resolve.py`), else `fundamentals_dera.csv`.

---

## PHASE 3 — Verification (run each refresh; gates what is safe to aggregate)

| # | Command | Produces |
|---|---------|----------|
| 7 | `python r2k_plausibility.py` | `plausibility_report.txt`, **`plausibility_flags.csv`** |
| 8 | `python r2k_audit.py` | `audit_report.txt`, `audit_findings.csv` |

- **`r2k_plausibility.py`** — the verification leg. Catches self-consistent-but-WRONG values the
  identities can't (revenue<0, GM>100%, neg D&A, debt>liabilities, implausible YoY) and combines
  them with the tie-out into a per-name **reliability tier** (clean / watch / review), weighted by
  index weight. **Required for the step-7 Data Reliability tab.**
- **`r2k_audit.py`** — comprehensive anomaly sweep across all company-years: identity-break
  severity (scaled by company size), an extended line-item sanity battery, and YoY discontinuities,
  ranked by **index weight × severity**. Most findings are growth-index characteristics
  (pre-revenue NI≫revenue, M&A/de-SPAC jumps), not bugs — triage to the accounting-*impossible* set.

---

## PHASE 4 — Analytics (the `step` scripts) and the IC workbook

Run 9–14 in any order, then **7-consolidate last** (it reads all of them + the reliability flags).

| # | Command | Produces |
|---|---------|----------|
| 9  | `python r2k_step3_analytics.py` | `Russell2000Growth_Analytics.xlsx` |
| 10 | `python r2k_step4_performance.py` | `R2000G_vs_SP600G_Performance.xlsx` |
| 11 | `python r2k_step5_cohort_attribution.py` | `R2000G_Cohort_Attribution.xlsx` |
| 12 | `python r2k_step6_index_comparison.py` | `R2000G_vs_SP600G_Quality.xlsx` |
| 13 | `python r2k_step8_concentration.py` | `R2000G_Concentration.xlsx` |
| 14 | `python r2k_step9_biotech.py` | `R2000G_Biotech.xlsx` |
| 15 | `python r2k_step7_consolidate.py` | **`R2000G_SmallCapGrowth_Benchmark_Review.xlsx`** (the IC workbook) |
| 16 | `python r2k_refresh_charts_data.py` | `R2000G_Benchmark_Review_charted.xlsx` (preserves hand-made charts) |

**`r2k_step7_consolidate.py`** assembles the IC workbook and adds the **Data Reliability** tab
(front of the book): per constituent, its reliability tier (clean/watch/review, color-coded),
three-statement tie-out confidence, which identities break, the plausibility flags, and the
provenance of engineered values — sorted by index weight, headed by the % of index weight that is
CLEAN. It reads `plausibility_flags.csv` (phase 3) and `fundamentals_dera.csv`; if the flags file
is absent the tab is skipped with a notice.

Knobs that affect the analysis: `WINDOW_MONTHS` / `WINDOW_START` (manager window, default trailing
36m to 4/30/2026), `SNAP_MONTH=4`, `BIOTECH_KEYWORDS`, `RET_MODE=auto`.

---

## Quick reference — a normal data refresh (after the one-time PHASE 1)

```
python r2k_dera_classify.py            # back up edgar_annual_fundamentals_ASFILED.csv first
python r2k_dera_to_fundamentals.py
python r2k_plausibility.py
python r2k_step3_analytics.py
python r2k_step4_performance.py
python r2k_step5_cohort_attribution.py
python r2k_step6_index_comparison.py
python r2k_step8_concentration.py
python r2k_step9_biotech.py
python r2k_step7_consolidate.py
python r2k_refresh_charts_data.py
```

---

## Never run directly — shared library
`r2k_perf_io.py` — Morningstar Direct performance parser, imported by steps 4 / 5 / 6.

---

## On-demand diagnostics (run ONLY to investigate a specific data-quality issue)

These are the probe-first toolkit. Each reads the cached outputs (no pipeline re-run) and writes a
focused report; you then make one precise fix in `r2k_dera_classify.py`, re-run phase 2, and measure.

- `python r2k_identity_probe.py <IDENTITY> <STMT>` — for ANY identity (e.g. `IS_NI IS`, `IS_GP IS`,
  `IS_NCI IS`), find the as-filed tag whose value equals the break's residual.
- `python r2k_bsfoots_probe.py` — balance-sheet foot: tags that equal the A−(L+E+mezz) gap.
- `python r2k_bsfoots_diagnose.py` — BS_FOOTS breaks bucketed by ROOT CAUSE (assets/liab/equity
  mis-selection, equity-should-include-NCI, NCI-not-in-equity, immaterial foot, multi-component).
- `python r2k_mezz_probe.py` — find the missing mezzanine (single tag vs summed multi-series).
- `python r2k_mezz_error_probe.py` — diagnose impossible mezz values (mezz>assets / mezz<0) via provenance.
- `python r2k_debt_probe.py` — pinpoint debt over-capture behind `debt>liabilities` (the exact double-counted tags).
- `python r2k_debt_reconcile.py` — split the DERA-vs-Morningstar debt gap into funded debt vs operating leases.
- `python r2k_debt_materiality.py` — weight the debt residual by INDEX WEIGHT.
- `python r2k_is_error_probe.py` — diagnose revenue<0 / GM>100% / COGS<0 / NItoCommon>parentNI selection errors.
- `python r2k_scale_probe.py [cik …]` — diagnose scale/mapping anomalies (1000× mis-tags, CIK collisions).
- `python r2k_name_audit.py [cik …]` — lay bare one or more names' balance sheets (defaults to the high-weight worklist).
- `python r2k_probe_company.py` — diagnose why a single company's net income / income statement looks off.
- `python r2k_tag_diagnose.py` — reverse-engineer a Morningstar value to our as-filed tags (diagnose, don't plug).
- `python r2k_dual_reconstruct_pilot.py` — rebuild a hard set from BOTH DERA and Morningstar; 2×2 verdict (diagnostic only).
- `python r2k_resolve.py` — **LAST RESORT**: for the residual `tag_diagnose` marks `NONE`, adopt the
  vendor value WITH provenance → `fundamentals_dera_resolved.csv` (then `to_fundamentals` auto-prefers it).
- `python r2k_dera_inspect.py` — peek at the DERA schema / prototype reconstructions.

---

## Housekeeping / utilities (run when you need them)
- `python r2k_inventory.py` — list and classify every file in the folder.
- `python r2k_cleanup.py` — safely archive old / superseded files.
- `python r2k_snapshot_charts.py` — back up hand-made charts to `R2000G_charts_backup.xlsx`.
- `python r2k_inspect_perf.py` — dump the Morningstar performance workbook structure.

---

## Validation / QA notes (each refresh)
- **`tieout_report.csv`** — per-filing three-statement identity tie-outs: balance sheet foots
  (A=L+E+mezz, equity=parent+NCI), income cascades (Rev−COGS=GP, Pretax−Tax+Disc=Consol NI,
  Consol−Parent=NCI), and the cash flow articulates (CF_FOOT, CF_BS_CASH, cross-year CASH_ROLL).
  ~98% of filings tie on every gating leg; balance sheet foots for ~99.9%. CF legs are judged on
  materiality (1%/$2M); BS/IS stay tight (0.5%/$5K). The `breaks` column flags the rest;
  `confidence` = fraction of applicable gating legs that tie.
- **`provenance` column** (in `fundamentals_dera.csv`) — records the non-obvious derivations
  (split COGS, disposal gain, temp-equity = A−L−E, current+deferred tax, …) so every engineered
  number is auditable.
- **Aggregate sanity** vs FactSet + Morningstar (gross/net margin validated; operating margin is
  as-filed GAAP, ~2pp below normalized vendor figures — a methodology footnote, not a bug).

---

## Deprecated / superseded — do **NOT** run
The DERA pipeline above replaces the earlier project phase. Do not run:
`r2k_step2_asfiled.py`, `r2k_step2b_asfiled_pilot.py`, `r2k_build_sp600g_universe.py`, and the
earlier-phase QA one-offs now superseded: `r2k_recover_missing.py`, `r2k_unknown_diagnostic.py`,
`r2k_reconcile_sources.py`, `r2k_override_reconcile.py`, `r2k_identity_backstop.py`,
`r2k_calibrate_tags.py`, `r2k_completeness_check.py`, `r2k_biotech_check.py`. They remain in the
repo for reference only.
