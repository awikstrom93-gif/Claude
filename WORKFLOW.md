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
| 1b | `python r2k_dera_name_index.py` | `dera_name_index.csv` (universe-independent name→CIK) | DERA quarters added |
| 2 | `python r2k_build_maps.py` | `security_cik_map.json`, `temporal_cik_map.json`, **`universe_ciks.csv`**, `name_resolved_review.csv` | holdings change |
| 3 | `python r2k_dera_index.py` | `dera_filing_index.csv` (every 10-K / 20-F / 40-F) | DERA quarters added |
| 4 | `python r2k_dera_extract.py` | **`dera_facts.csv`** (all as-filed facts) | DERA quarters added — **LONG (streams every quarter)** |

`r2k_build_maps.py` reads **both** the R2000G and S&P 600 Growth holdings, so it builds the
ticker→CIK maps for steps 3/5/6 *and* `universe_ciks.csv`. `r2k_dera_index.py` auto-detects
`universe_ciks.csv`, so the DERA dataset covers both indices with no extra configuration.

**Step 1b (`r2k_dera_name_index.py`) is the no-CIK backstop.** Holdings resolve CIK → CUSIP →
ticker → **name**. The name tier catches delisted names (mostly biotechs: ADRO/Aduro,
ADXS/Advaxis, ALVR/AlloVir, ANGN/Angion, AVRO/AVROBIO, CARA…) that arrive with no CIK/CUSIP and
would otherwise land in the "Unknown (no fundamentals)" attribution bucket. It scans **every**
filer in `sub.txt` (not the resolved universe, so the missing names are present), keeps only
**unambiguous** normalized names that have a real annual filing, and matches each holding by the
name it carried *at that snapshot* (point-in-time). Every rescue is written to
**`name_resolved_review.csv`** (ticker / holdings-name vs matched DERA name + SIC) — **review it**
before trusting a build; a wrong homonym shows up as a mismatched SIC. Re-run 1b only when new
DERA quarters are added; the newly-resolved CIKs then flow automatically into `universe_ciks.csv`
→ index → extract → classify.

---

## PHASE 2 — Data build (the recurring run — the accounting engine)

| # | Command | Produces |
|---|---------|----------|
| 5 | `python r2k_dera_classify.py` | **`fundamentals_dera.csv`** + **`tieout_report.csv`** (also learns any recovery-validated tags from `validated_tags.csv` — re-run after 5b to close the loop) |
| 5b | `python r2k_metric_recover.py` | **`fundamentals_dera_resolved.csv`** (recovers blank revenue/op-income/equity/cash/gross-profit/FCF, IDENTITY-first then as-filed tag) + `metric_recovery_audit.csv` |
| 6 | `python r2k_dera_to_fundamentals.py` | **`edgar_annual_fundamentals_ASFILED.csv`** (back up the old one first) |

**`r2k_dera_classify.py` is the accounting brain.** It reconstructs all three statements with
tag-informed, identity-driven value selection (the tag supplies the candidate components; the
accounting identity picks the value that makes the statements articulate; every adoption is
double-count-safe and never plugs a vendor number). It emits, per filing: the standardized
cascade, ~15 identity tie-outs, structural debt (funded + lease-adjusted, incl. fleet/floorplan),
and a **`provenance`** column recording how every engineered value was built. Self-test:
`python r2k_dera_classify.py --selftest`.

**`r2k_revenue_recover.py`** closes a real extraction gap: some company-years have the rest of the
income statement (net income, etc.) but a BLANK revenue, because the filer's revenue XBRL tag isn't in
the classifier's `REV` priority list (refiners/healthcare/hotels especially — Western Refining fy2015
had net income $406M but no revenue; the true figure is $9.8B). Left unfixed these are miscounted as
"no-revenue" names, which inflates the no-revenue weight in the EARLY years (the gap shrinks over time)
and makes "% with revenue" look like a rising trend when it is mostly improving data capture. **It stays
faithful to the "diagnose, don't plug" principle**: it does NOT adopt Morningstar's number — it uses
Morningstar `Total Revenue` only as a *target* to locate the correct AS-FILED DERA tag (a single
top-line tag, or the SUM of segment lines like `FoodAndBeverageRevenue+OccupancyRevenue+…`), then adopts
that as-filed value — the ORIGINAL filing's figure (point-in-time, not a restatement; it dedups
multi-filing years to the accession whose own latest year == fy). Morningstar (which may carry restated
numbers) never enters the data. Only if no as-filed fact reconciles to the target does it fall back to
the Morningstar value, stamped `morningstar:fallback` in the audit so it is rare and visible. Each
adoption records the tag(s) used and how far as-filed sits from Morningstar (= restatements avoided).
On the R2000G panel it cuts the 2015 no-revenue weight from ~7.7% to ~2.2% and flattens the trend.
Requires `dera_facts.csv` (phase 1) in addition to `fundamentals_dera.csv` and `morningstar_long.csv`.

**`r2k_metric_recover.py`** generalizes the revenue recovery (which it supersedes) to every metric
`r2k_metric_gaps.py` flags as a real hole. It is IDENTITY-FIRST: (1) reconstruct the blank from
as-filed COMPONENT tags via an accounting identity (`total_equity = assets − liabilities − NCI`,
`gross_profit = revenue − COGS`, `free_cash_flow = CFO − capex`) — no vendor, self-checking, adopted
even when Morningstar lacks the value; (2) else locate the metric's own as-filed tag verified against
the Morningstar target (the revenue approach); (3) else Morningstar fallback, stamped. Each field gets
a `<field>_src` provenance column, and the audit records the method per recovery. NOTE: the "present
but inconsistent" class of hole is already caught upstream by the classifier's identity tie-outs
(`tieout_report.csv`, the ~98% gating), so this focuses on BLANKS.

**`r2k_validated_tags.py` — the closed loop that shrinks the recovery residual.** Whenever
`r2k_metric_recover.py` or `r2k_revenue_recover.py` ADOPTS a specific as-filed tag (provenance
`asfiled:<tag>`), that tag WAS the right line for the role and reconciled to Morningstar — a validated
promotion candidate. The recovery pass records it (distinct company-years, by MAX so re-runs are
idempotent) in **`validated_tags.csv`**. On the next build, `r2k_dera_classify.py` reads that file and
APPENDS each validated tag to the matching role list (`revenue→REV`, `cost_of_revenue→COGS`,
`operating_income→OINC`, `total_equity→EQ_PARENT`, `cash→CASH`, `capex→CAPEX`) at **lowest priority** —
so every curated tag still wins and a promoted tag fires only for a filing that would otherwise be blank
for that role (exactly the profile it was validated on). The effect: the classifier learns the
non-standard tags at the SOURCE, those roles stop coming out blank, and the recovery pass has less to do
each cycle — the hole closes permanently instead of being re-patched every run. Safety: only
target-validated tags promote by default (env `R2KG_TAGPROMOTE_MIN`, default 1); solo/no-target
adoptions (cash, capex) need ≥2 company-years (`R2KG_TAGPROMOTE_MIN_NOTARGET`, default 2); the
classifier's identity/tie-out still gates the value — promotion only widens the candidate set.
Segment-SUM and looser "closest" recoveries are NOT promoted (they aren't a single reusable tag).
**Operationally: after a recovery pass reports "N tag(s) now promotable", re-run `r2k_dera_classify.py`
(step 5) once so they fold in; watch the step-5b recovery count fall on the following build.** Inspect
the current promotable set anytime with `python r2k_validated_tags.py`.

`r2k_dera_to_fundamentals.py` auto-prefers `fundamentals_dera_resolved.csv` if present
(from `r2k_metric_recover.py` and/or `r2k_resolve.py`), else `fundamentals_dera.csv`.

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

## PHASE 4 — Analytics and the IC workbook (panel-first, ONE command)

```
python r2k_report.py
```

`r2k_report.py` builds the whole IC workbook from the **canonical panel** and runs a consistency
guard at the end. It orchestrates, in order: panel + analytics → performance → attribution →
comparison → concentration → biotech → consolidate → guard. Flags: `--guard` re-runs only the
consistency check on the existing workbook; `--skip step4_performance,…` omits named steps.

### The panel architecture (why there is now one command)

Every fundamental figure in the workbook is a projection of **one** table, the *panel*, so the tabs
can never disagree on who is in the index, which CIK a ticker maps to, which fiscal year is used, or
what a name's metrics are. (This replaced a structure where each `stepN` re-derived the universe
independently — the source of the 2016 CZR identity bug.)

| Layer | Module | Role |
|---|---|---|
| Universe / identity / quality | **`r2k_universe.py`** | one holdings loader, one **point-in-time** CIK resolver (`resolve_identity`), one `annual_spine`, one `pick_fy0`, one `company_metrics`, the `index_quality` reducer, and `build_panel()` → **`r2k_panel.csv`** (one row per index×snapshot×constituent, both R2000G and S&P 600 Growth). The single source of truth. Also `build_quarterly_panel()` → **`r2k_panel_q.csv`** (the 3/6/9/12 cadence, via `--quarterly`). |
| Views (`panel → sheet`) | `r2k_view_quality_trends/cohorts/dupont/composition` (step3 tabs), `r2k_view_comparison` (step6 tabs), `r2k_view_biotech` (step9 fundamentals tabs), `r2k_view_concentration` (step8 weight tab), `r2k_view_reliability` (Data Reliability), and the four panel-native exhibits `r2k_factor_spreads` / `r2k_view_solvency` / `r2k_view_persistence` / `r2k_view_valuation` | thin, pure projections of the panel; each has a `write_sheet(wb, panel)` and a `diff_sheet` self-check vs the current workbook. |
| Analytics assembler | **`r2k_build_analytics.py`** | rebuilds the panel + writes `Russell2000Growth_Analytics.xlsx` from the views. **Run this instead of the retired `r2k_step3_analytics.py`.** |
| Returns / performance | `r2k_step4/5/8/9` + the returns parts of step6 | unchanged math, now importing the shared helpers from `r2k_universe` (no duplicated `norm_facts`/`fund_for`/`ticker_cik_map`/`annual_spine`). |
| Assembly + guard | `r2k_step7_consolidate.py`, **`r2k_report.py`** | step7 assembles the IC workbook (incl. the Data Reliability tab via `r2k_view_reliability`); `r2k_report.py` runs the whole chain + the guard. |

**Consistency guard** (`r2k_report.py --guard`): recomputes the R2000G headline straight off the
panel and asserts that BOTH the panel-sourced `R2KG Quality Trends` tab AND the independently-built
step6 `Qual R2000G` tab agree with it. This is the tripwire that makes the original step3-vs-step6
divergence impossible to ship silently.

**One holdings resolver + staleness tripwires.** Now that quarterly holdings files share the folder,
the glob `*Russell*Growth*Holding*.xlsx` matches BOTH the annual and quarterly workbooks, so an
unguarded `list(glob)[0]` could silently load the wrong file (or, for an index with no annual file,
load nothing). To prevent that: `r2k_universe.find_annual(index)` is the **single** holdings resolver
— it excludes `*quarterly*`, sorts for determinism, and falls back to the quarterly workbook when no
annual file exists — and step6/8/9, `r2k_perf_io`, `r2k_plausibility`, and `r2k_benchmark_reconcile`
all route through it (or its hardened equivalents) so every tab resolves the SAME file for an index.
Two staleness tripwires back it up: `get_panel`/`get_quarterly_panel` warn if a cached
`r2k_panel*.csv` is older than the holdings or `fundamentals_dera.csv`, and the **Data Reliability**
tab prints a red STALE banner if `plausibility_flags.csv` predates `fundamentals_dera.csv` (the tiers
wouldn't reflect the latest engine). The **Up-Down Capture** tab is self-reconciling: it shows the
up-leg and down-leg growth-of-$1 for both indices and asserts `up-leg × down-leg` rebuilds each
index's Summary-tab cumulative exactly, so a capture ratio can never be read against the wrong
period's return.

### Point-in-time identity (`resolve_identity`)

A reused ticker maps, in every vendor file, to its **current** issuer — so a base-first lookup gives
the wrong entity for an earlier year (2016 CZR → today's Caesars, not the Caesars that filed then;
2016 BBBY → Overstock). `resolve_identity` resolves each (ticker, snapshot) by **fiscal-year
freshness**, but **conservatively**: it keeps the base-map CIK *unless that entity is defunct or
stale as of the snapshot* (its newest fiscal year `< snapshot_year − 1`), in which case it switches
to the candidate CIK whose newest fy ≤ snapshot is the most recent — i.e. the entity actually filing
under that ticker at that time. Healthy names never flip; only dead-entity tickers are reassigned.

The change set is fully auditable: `python r2k_identity_changes.py` prints every constituent-year the
rule moves vs pure base-first (old vs new as-filed name + revenue/NI + net index impact); each row is
a defunct→current-issuer fix. `python r2k_cik_audit.py` lays out the side-by-side evidence (as-filed
DERA name, fundamentals, Morningstar ticker match) for every (ticker, year) where the base and
temporal maps disagree, ranked by weight, so each is resolved from facts rather than guesswork.
`resolve_identity` is shared by both the panel build and `r2k_step6_index_comparison.snapshot_quality`
so the two can never diverge again.

### The four panel-native exhibits

Built directly off the panel (and the quarterly panel via `--quarterly`), injected by step7:

- **Quality Factor Spreads** (`r2k_factor_spreads.py`) — cap-weighted quintile return spreads for
  ROIC, GP/Assets, accruals (low=good), net margin, FCF margin, plus a composite z-score. Forward
  returns winsorized 1/99; names capped at 5% of a quintile's weight; a **median** row damps the
  single-year melt-up outliers. Annual (forward-12m) and quarterly (forward-3m) cadence.
- **Solvency Tail** (`r2k_view_solvency.py`) — joins the panel to `fundamentals_dera` (EBITDA,
  interest expense): %can't-cover-interest, %coverage<2×, median coverage, %net-debt/EBITDA>4×,
  %negative-EBITDA, %negative-EBITDA-with-net-debt; R2000G vs S&P 600 Growth.
- **Cohort Persistence** (`r2k_view_persistence.py`) — year-over-year transition matrix of the
  profitable / fallen-angel / never-profitable cohorts (same / unknown / exited).
- **Valuation of the Tail** (`r2k_view_valuation.py`) — cohort aggregate P/S via weight-as-float-cap,
  expressed as a multiple of the index P/S (the cap constant cancels exactly).

`python r2k_cf_articulation_probe.py` checks the cash-flow legs articulate under the cash-relative
tolerance (`CF_TOL_CASH` in `r2k_dera_classify.py`).

**`r2k_step7_consolidate.py`** assembles the IC workbook and adds the **Data Reliability** tab
(front of the book): per constituent, its reliability tier (clean/watch/review, color-coded),
three-statement tie-out confidence, which identities break, the plausibility flags, and the
provenance of engineered values — sorted by index weight, headed by the % of index weight that is
CORE-reliable / fully clean. It reads `plausibility_flags.csv` (phase 3) and `fundamentals_dera.csv`.

After the build, `python r2k_refresh_charts_data.py` produces `R2000G_Benchmark_Review_charted.xlsx`
(preserves hand-made charts).

Knobs that affect the analysis: `WINDOW_MONTHS` / `WINDOW_START` (manager window, default trailing
36m to 4/30/2026), `SNAP_MONTH=4`, `BIOTECH_KEYWORDS`, `RET_MODE=auto`.

**Quarterly cadence.** The annual spine is the 4/30 reconstitution snapshot; the quarterly spine
(`QUARTER_MONTHS = (3,6,9,12)`) is built from the quarterly holdings files. `find_annual` skips any
holdings filename containing "quarterly"; `find_quarterly` selects them. Build with
`python r2k_universe.py --quarterly` → `r2k_panel_q.csv`; the factor-spread and valuation exhibits
take `--quarterly` to emit their robust forward-3m / snapshot-grouped versions, which are less
exposed to a single fiscal year's outlier than the annual forward-12m cut.

---

## Quick reference — a normal data refresh (after the one-time PHASE 1)

```
python r2k_dera_classify.py            # back up edgar_annual_fundamentals_ASFILED.csv first
python r2k_metric_recover.py           # recover blank revenue/op-inc/equity/cash/GP/FCF (identity-first) -> fundamentals_dera_resolved.csv
python r2k_dera_to_fundamentals.py
python r2k_plausibility.py
python r2k_report.py                   # panel + step4/5/6/8/9 + consolidate + consistency guard
python r2k_refresh_charts_data.py
```

---

## Never run directly — shared libraries
- `r2k_universe.py` — the panel / identity / quality single source of truth, imported by every view
  and by step5/8/9. (Running it directly rebuilds `r2k_panel.csv` and prints the headline — useful
  as a check, but the report build does this for you.)
- `r2k_perf_io.py` — Morningstar Direct performance parser, imported by steps 4 / 5 / 6 / 8 / 9.

## Validate a single tab vs the current workbook (on demand)
Each view runs standalone and prints a cell-by-cell diff vs the current workbook tab, e.g.
`python r2k_view_quality_trends.py`, `python r2k_view_comparison.py`, `python r2k_view_biotech.py`.
Use these to see exactly what a change moved before assembling the full report.

---

## On-demand diagnostics (run ONLY to investigate a specific data-quality issue)

These are the probe-first toolkit. Each reads the cached outputs (no pipeline re-run) and writes a
focused report; you then make one precise fix in `r2k_dera_classify.py`, re-run phase 2, and measure.

- `python r2k_metric_gaps.py` — the revenue-gap audit generalized to EVERY fundamental: for each
  panel metric, the index-weighted `blank%` (missing among covered names) and `recoverable%` (of that,
  how much Morningstar has a value for = a real extraction gap, like revenue was) vs genuinely-absent.
  Ranks which metrics have revenue-style holes worth an as-filed recovery pass. → `metric_gaps_report.txt`,
  `metric_gaps.csv`. Found: operating_income/equity/cash (~5–7% recoverable, directly reported),
  gross_profit (~21%, but largely derived from COGS), fcf (~6%, derived from capex).
- `python r2k_metric_crosscheck.py` — the OTHER half: finds PRESENT-but-WRONG values (filled and
  internally consistent, so the tie-out passes, but still off — e.g. an understated revenue with GP
  derived from it). Internal identities can't see these; only an external reference can. Compares every
  present value to Morningstar, index-weighted: the MEDIAN ratio flags SYSTEMATIC bias (a mapping /
  selection problem), off>10%/off>25% flag individual review candidates, and it writes a worklist
  ranked by index weight × gap. → `metric_crosscheck_report.txt`, `metric_crosscheck.csv`. On the
  current data all metrics are clean (median 1.000) except operating_income (median 1.000 but ~19% of
  weight >10% apart — mostly definitional: what counts as "operating"). A gap is a candidate, not proof
  of error (restatements / adjusted / gross-vs-net). Diagnostic only.
  **Regression gate (guards tag-widening):** because adding tags to the classifier/recovery can only
  ever *fill blanks* (low-priority, fill-only) but a bad tag could still put a wrong value in a
  previously-empty cell, this now records each metric's median ratio and >25% dispersion to
  `metric_crosscheck_baseline.csv` and, on the next run, prints prior→now and **WARNS** if any metric
  (a) drifts past `|median−1|>0.05` (persistent bias), (b) has its median worsen >0.02 vs the prior run,
  or (c) has its >25%-disagreement weight rise >1.0pp. So a widened tag list can never *silently*
  over/understate a metric — the number that would move is the number being watched. Run it after every
  classifier/recovery change; OVERALL prints PASS or the warning count. Thresholds via
  `R2KG_XCHECK_ABS_TOL` / `R2KG_XCHECK_DELTA_TOL` / `R2KG_XCHECK_OFF25_DELTA`.
- `python r2k_validated_tags.py` — inspect the recovery→classifier feedback set: the as-filed tags
  recovery has validated (`validated_tags.csv`) and which would be promoted into the classifier's role
  lists on the next build. Read-only. This is the loop that shrinks the step-5b recovery residual over
  time (see `r2k_validated_tags.py` in Phase 2). Recovery writes it automatically; re-run step 5 to fold.
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
`r2k_step3_analytics.py` (**superseded by `r2k_build_analytics.py`** — the panel-based analytics
workbook; `r2k_report.py` calls it), `r2k_step2_asfiled.py`, `r2k_step2b_asfiled_pilot.py`,
`r2k_build_sp600g_universe.py`, and the
earlier-phase QA one-offs now superseded: `r2k_recover_missing.py`, `r2k_unknown_diagnostic.py`,
`r2k_reconcile_sources.py`, `r2k_override_reconcile.py`, `r2k_identity_backstop.py`,
`r2k_calibrate_tags.py`, `r2k_completeness_check.py`, `r2k_biotech_check.py`. They remain in the
repo for reference only.
