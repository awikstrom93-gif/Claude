# Attribution JSON Builder — Phase 2

Converts Morningstar Direct attribution exports into per-manager JSON that
answers one question for the battle book commentary agent:

> **Why did this manager outperform or underperform?**

Phase 1 (`build_quarterly_json.py`) tells the agent *what* happened — return,
benchmark, excess, percentile, history, trends, market context. Phase 2 tells it
*why*.

---

## 1. Quick start

```bash
pip install -r requirements.txt

# Recommended - runs both phases in the correct order
python build_battle_book_data.py --quarter "2026 Q2"

# Or run the phases individually (Phase 1 must come first)
python build_quarterly_json.py   --quarter "2026 Q2"
python build_attribution_json.py --quarter "2026 Q2"
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--quarter` | `latest` | Quarter to build (`"2026 Q2"`, `2026Q2`, `Q2 2026`, or `latest`). |
| `--input-root` | `...\Quarterly Battle Book Inputs` | Root holding the quarter folders. |
| `--output-root` | `...\Quarterly Battle Book Data` | Root the JSON is written to. |
| `--no-patch` | – | Build attribution files but leave the asset-class JSONs untouched. |
| `--verbose` | – | Debug logging. |

Exit codes: `0` success · `1` one or more workbooks failed · `2` bad arguments,
missing quarter folder, or Phase 1 output not found.

> **Run order matters.** `build_quarterly_json.py` rewrites the asset-class
> files from scratch, which removes the attribution pointers. **Always run
> Phase 2 after any Phase 1 rebuild for the same quarter.** Phase 2 on its own is
> safe to re-run any number of times — it is idempotent.
>
> `build_battle_book_data.py` exists so this cannot go wrong by accident: it runs
> both phases in order, resolves `latest` once so both phases target the same
> quarter, and refuses to run Phase 2 if Phase 1 fails. Prefer it for routine
> quarterly builds. Its exit codes are `0` both phases succeeded · `1` a phase
> failed · `2` bad arguments or a phase script missing from the folder.

---

## 2. Folders

**Input**

```
Quarterly Battle Book Inputs\
  2026 Q2\
    US Large Growth.xlsx                       ← Phase 1
    US Mid Growth.xlsx                         ← Phase 1
    US Small Growth.xlsx                       ← Phase 1
    US Growth Sector Industry Factor.xlsx      ← Phase 1
    Attribution\                               ← Phase 2
      T. Rowe Price Blue Chip Growth Attribution.xlsx
      Fidelity Blue Chip Growth Attribution.xlsx
      Edgewood Growth Instl Attribution.xlsx
```

Every `.xlsx` in `Attribution\` is processed; filenames are irrelevant because
the manager is read from inside the workbook. Excel lock files (`~$…`) are
ignored. A missing `Attribution\` folder is not an error — the build simply
records that no attribution is available.

**Output**

```
Quarterly Battle Book Data\
  2026 Q2\
    large_growth.json                  ← patched with attribution pointers
    mid_growth.json                    ← patched
    small_growth.json                  ← patched
    manifest.json                      ← Phase 1, untouched
    debug_layout_report.json           ← Phase 1, untouched
    attribution\
      t_rowe_price_blue_chip_growth.json
      fidelity_blue_chip_growth_k.json
      edgewood_growth_instl.json
    debug_attribution_report.json
```

---

## 3. Scope: commentary, not audit

This is the most important design decision in Phase 2.

The battle-book-facing JSON contains **no** residual effects, attribution gaps,
non-attributed effects, expense-ratio differences, coverage caveats, or
benchmark-reconstruction differences. Those numbers are still calculated on every
run — they live in `debug_attribution_report.json` under each workbook's
`reconciliation` block, for internal QA only.

The reason is behavioural: fields that exist in the JSON get written about. A
`coverage_percent` field on the manager record invites a sentence about
attribution coverage in a document meant for consultants. Keeping those numbers
out of the commentary-facing file is what keeps them out of the commentary.

**Industry attribution does not exist here at all** — no object, no placeholder,
no `available: false` flag. The workbooks classify by GICS Sector only. Industry
commentary comes from the Phase 1 market-data layer
(`market_data.industries`, `summaries.top_10_industries`), which is index-level
market movement rather than manager attribution.

---

## 4. How the agent uses the output

1. Agent is asked for *"T. Rowe Price Blue Chip Growth, Q2 2026"*.
2. It finds the manager in `large_growth.json` via `manager_lookup` (Phase 1).
3. The manager record now carries `attribution_available` and `attribution_path`.
4. If available, it opens `attribution/t_rowe_price_blue_chip_growth.json`.

```jsonc
// on the manager record
"attribution_available": true,
"attribution_path": "attribution/t_rowe_price_blue_chip_growth.json"

// at the asset-class level
"attribution_index": {
  "available": ["edgewood growth instl", "fidelity blue chip growth k",
                "t rowe price blue chip growth"],
  "count": 3,
  "folder": "attribution",
  "path_pattern": "attribution/{manager_slug}.json",
  "manager_slugs": { "t rowe price blue chip growth": "t_rowe_price_blue_chip_growth" }
}
```

Managers without attribution get `attribution_available: false` and
`attribution_path: null`, so the agent never has to guess or probe for a file.

---

## 5. Attribution JSON structure

```jsonc
{
  "manager": "T. Rowe Price Blue Chip Growth",
  "manager_lookup_key": "t rowe price blue chip growth",
  "asset_class": "US Large Growth",
  "asset_class_key": "large_growth",
  "benchmark": "Russell 1000 Growth TR USD",
  "period": "2026 Q2",
  "period_start_date": "2026-04-01",
  "period_end_date": "2026-06-30",
  "period_band_label": "4-1-2026 - 6-30-2026",
  "model": "three_factor_brinson",
  "classification": "GICS Sector",
  "lineage": { "source_file": "…", "worksheet": "Attribution",
               "exported_at": "…", "generated_at": "…" },

  "attribution_summary":       { … },   // §5.1
  "sector_attribution":        [ … ],   // §5.2
  "cash_attribution":          { … },   // §5.2a
  "security_attribution":      [ … ],   // §5.3
  "security_attribution_meta": { … },
  "top_contributors":          [ … ],   // §5.4
  "top_detractors":            [ … ],   // §5.4
  "sector_rankings":           { … },   // §5.5
  "attribution_periods":       { … },   // §5.6
  "attribution_trends":        { … },   // §5.7
  "concentration":             { … }    // §5.8
}
```

Every effect appears twice — as a percentage and in basis points. Battle books
quote basis points, and deriving `_bps` at build time removes an arithmetic step
the agent gets wrong at exactly the moment precision matters.

### 5.1 `attribution_summary`

```jsonc
{
  "allocation_effect": -0.2872,   "allocation_effect_bps": -28.7,
  "selection_effect": -2.4691,    "selection_effect_bps": -246.9,
  "interaction_effect": -0.3436,  "interaction_effect_bps": -34.4,
  "total_active_return": -3.1,    "total_active_return_bps": -310.0,
  "primary_driver": "selection",
  "driver_confidence": "high"
}
```

**`primary_driver`** — `selection` · `allocation` · `interaction` · `mixed`.
Decided on **absolute magnitude**: the largest |effect| wins, provided it clearly
dominates the runner-up.

| Ratio of largest to second-largest \|effect\| | `primary_driver` | `driver_confidence` |
| --- | --- | --- |
| ≥ 2.00 | the largest effect | `high` |
| ≥ 1.25 | the largest effect | `medium` |
| < 1.25 | `mixed` | `low` |
| no effects present | `mixed` | `insufficient_data` |

Thresholds are `DRIVER_HIGH_CONFIDENCE_RATIO` and `DRIVER_MIN_DOMINANCE_RATIO`.
The `mixed` outcome exists so the agent never claims a driver the numbers do not
support.

### 5.2 `sector_attribution`

All 11 GICS sectors, always — a zero weight is itself the story (Edgewood holds
no Consumer Staples, worth +42 bps of allocation).

```jsonc
{
  "sector": "Information Technology",
  "portfolio_weight": 47.6961, "benchmark_weight": 52.5058, "active_weight": -4.8096,
  "portfolio_return": 14.1584, "benchmark_return": 19.6832, "return_differential": -5.5248,
  "allocation_effect": -0.1558,  "allocation_effect_bps": -15.6,
  "selection_effect": -2.8737,   "selection_effect_bps": -287.4,
  "interaction_effect": 0.2276,  "interaction_effect_bps": 22.8,
  "total_effect": -2.8019,       "total_effect_bps": -280.2,
  "position": "underweight",
  "driver": "selection",
  "effect_rank": 1
}
```

* **`position`** — `overweight` · `underweight` · `neutral` · `not_held`.
  `not_held` when portfolio weight is zero; `neutral` when |active weight| is
  under `NEUTRAL_ACTIVE_WEIGHT_PP` (0.10 pp).
* **`driver`** — same rule as `primary_driver`, applied per sector.
* **`effect_rank`** — 1 = largest absolute total effect, so rank 1 is the sector
  that mattered most in either direction.

### 5.2a `cash_attribution`

Cash is **not** a GICS sector and never appears in `sector_attribution`. But
holding cash in a rising market is a genuine reason a manager lagged, so its
allocation effect is exposed separately:

```jsonc
"cash_attribution": {
  "available": true,
  "allocation_effect": -0.3102,
  "allocation_effect_bps": -31.0,
  "material": true
}
```

* **`available`** — `false` when the workbook has no `Cash` row; the effect
  fields are then `null` and `material` is `false`.
* **`material`** — `true` when |allocation effect| reaches the same
  `MATERIALITY_THRESHOLD_PP` used for securities (0.05 pp = 5 bps). Use it to
  decide whether cash is worth a sentence; do not judge the size yourself.

Q2 2026 across the three managers: Edgewood −31.0 bps (material), TRP −8.0 bps
(material), Fidelity −1.0 bps (not material).

Cash carries an allocation effect only — there is no cash selection or
interaction effect to report.

### 5.3 `security_attribution`

Not every security. Morningstar lists the full benchmark — 493 rows for TRP,
805 for Fidelity — most of which the manager never owned. Stored are:

* every security whose |total effect| reaches `MATERIALITY_THRESHOLD_PP`
  (**0.05 pp = 5 bps**, a constant at the top of the script), held or not; plus
* every security appearing in `top_contributors` / `top_detractors`, so those
  lists can never reference a security absent from the file.

That is 46–67 securities per manager instead of 493–805.

```jsonc
{
  "security": "Advanced Micro Devices Inc", "ticker": "AMD",
  "sector": "Information Technology",
  "held": true,
  "portfolio_weight": 0.3752, "benchmark_weight": 1.012, "active_weight": -0.6368,
  "portfolio_return": 63.872, "benchmark_return": 185.5577,
  "contribution_portfolio": 0.3462, "contribution_benchmark": 1.2073,
  "contribution_active": -0.8611,
  "selection_effect": -0.72, "selection_effect_bps": -72.0,
  "total_effect": -0.72,     "total_effect_bps": -72.0,
  "reason": "underweight_outperformer"
}
```

Securities carry a **selection effect only** — allocation and interaction exist
at sector level and are not distributed to holdings. Morningstar reports the same
number twice (as `Selection %` and `Active Ret%`); both fields are emitted.

### 5.4 `top_contributors` / `top_detractors`

Top 10 each, by `total_effect` — contributors descending, detractors ascending.

```jsonc
{
  "rank": 1,
  "security": "Lam Research Corp", "ticker": "LRCX",
  "sector": "Information Technology",
  "held": false,
  "total_effect": -0.7562, "total_effect_bps": -75.6,
  "active_weight": -1.0059,
  "portfolio_return": null, "benchmark_return": 102.9538,
  "reason": "not_held_outperformer"
}
```

### The `reason` enum — read this before writing commentary

| Value | Plain meaning | Effect on relative return |
| --- | --- | --- |
| `overweight_outperformer` | Owned more than the benchmark; it beat the benchmark | Positive |
| `overweight_underperformer` | Owned more than the benchmark; it lagged | Negative |
| `underweight_outperformer` | Owned less than the benchmark; it beat the benchmark | Negative |
| `underweight_underperformer` | Owned less than the benchmark; it lagged | Positive |
| `not_held_outperformer` | **Never owned**; it beat the benchmark | Negative |
| `not_held_underperformer` | **Never owned**; it lagged | Positive |
| `held_positive_selection` | Held at roughly benchmark weight; helped | Positive |
| `held_negative_selection` | Held at roughly benchmark weight; hurt | Negative |
| `mixed` | Direction could not be established from the data | — |

**`held` is the field that matters most.** A `not_held_*` security is a benchmark
constituent the manager never owned. Writing *"the fund's position in Lam Research
detracted 76 bps"* would be factually wrong — the correct sentence is *"not owning
Lam Research, which returned 103%, cost 76 bps"*. Both `held` and `reason` are
provided precisely so this cannot go wrong.

"Outperformer" / "underperformer" is measured against the benchmark's total
return for the period, not against the security's own benchmark return.

### 5.5 `sector_rankings`

Pre-sorted so the agent never sorts:

```jsonc
{
  "top_contributing_sectors": [ { "sector": "Health Care", "total_effect_bps": 36.2,
                                  "driver": "selection", "position": "underweight" } ],
  "top_detracting_sectors":   [ { "sector": "Information Technology", "total_effect_bps": -280.2,
                                  "driver": "selection", "position": "underweight" } ],
  "strongest_sector_selection":  { "sector": "Industrials",            "selection_effect_bps": 53.4,   "position": "underweight" },
  "weakest_sector_selection":    { "sector": "Information Technology", "selection_effect_bps": -287.4, "position": "underweight" },
  "strongest_sector_allocation": { "sector": "Consumer Staples",       "allocation_effect_bps": 30.5,  "position": "underweight" },
  "weakest_sector_allocation":   { "sector": "Information Technology", "allocation_effect_bps": -15.6, "position": "underweight" }
}
```

Top/bottom sector lists are capped at `TOP_N_SECTORS` (5) and only include
sectors whose effect is genuinely positive / negative.

### 5.6 `attribution_periods`

Four quarters, resolved by date against the selected quarter — the same approach
Phase 1 uses, so the keys line up with `performance_periods`:

| Key | Window for a Q2 2026 build |
| --- | --- |
| `selected_quarter` | 2026-04-01 → 06-30 |
| `two_quarters_ago` | 2026-01-01 → 03-31 |
| `three_quarters_ago` | 2025-10-01 → 12-31 |
| `four_quarters_ago` | 2025-07-01 → 09-30 |

As in Phase 1, `two_quarters_ago` is the quarter **immediately before** the
selected quarter, following Morningstar's own labelling. Explicit start and end
dates are on every record. Unavailable periods are `null` with
`available: false`.

### 5.7 `attribution_trends`

```jsonc
{
  "consecutive_quarters_selection_negative": 2,
  "consecutive_quarters_selection_positive": 0,
  "consecutive_quarters_allocation_negative": 2,
  "consecutive_quarters_allocation_positive": 0,
  "dominant_driver_selected_quarter": "selection",
  "dominant_driver_trailing_4_quarters": "selection",
  "driver_stability": "consistent",
  "quarters_available": 4
}
```

Streaks walk newest → oldest and stop at the first quarter that breaks the run or
has no data — the same convention as `performance_trends`.
`dominant_driver_trailing_4_quarters` applies the driver rule to the summed
absolute effects across available quarters. `driver_stability` is `consistent`
when every quarter shares one driver, `variable` when they differ, and
`insufficient_data` below two quarters.

### 5.8 `concentration`

```jsonc
{
  "top_5_contributors_share_of_positive_effect": 0.328,
  "top_5_detractors_share_of_negative_effect": 0.404,
  "top_5_absolute_effect_share": 0.247,
  "number_of_positive_securities": 213,
  "number_of_negative_securities": 183,
  "number_of_sectors_positive": 5,
  "number_of_sectors_negative": 6
}
```

Computed over the **full** security universe inside GICS sectors, before
materiality filtering, so the denominators are honest. High shares support
"concentrated"; low shares support "broad-based".

---

## 6. Manager linking

The `Template` sheet's `Portfolio:` value is matched against Phase 1 using
`normalize_lookup_name` — the same lowercase / trim / collapse-spaces /
remove-periods rules that build `manager_lookup`.

Matching is **strict**. There is no fuzzy matching, no substring matching, no
best-guess. Anything other than exactly one match is an error: the workbook is
skipped, the failure is recorded in `debug_attribution_report.json` under
`errors` and `unmatched_attribution_files`, and the run exits `1`.

A silent mis-link — attaching one manager's attribution to another — is the worst
failure this pipeline can produce, which is why it fails loudly instead.

Manager-name matches take priority. Phase 1 also indexes tickers and strategy
names as secondary keys; those are used only as a fallback and the debug report
records `match_type` so an alias match is always visible.

The output filename is the slug of the normalised name:
`T. Rowe Price Blue Chip Growth` → `t_rowe_price_blue_chip_growth.json`.

---

## 7. Workbook layout handling

Nothing is addressed by fixed cell reference:

* **Header rows** are found by locating the row containing `Allocation %`,
  `Selection %` and `Interaction %`; the band and group rows are the two above it.
* **Period bands** are discovered from the populated cells of the band row, and
  each band's label (`4-1-2026 - 6-30-2026`) is parsed into a date range. The band
  for a quarter is chosen by **date match**, with a quarter-shaped label
  (`Q2 2026`) as a fallback. Position is never used.
* **Columns within a band** are mapped by combining the metric group (row 9,
  carried forward across its merged span) with the leaf header (row 10) — so
  `("Weights %", "+/-")` becomes `active_weight` regardless of column order or
  band width.
* **Rows** are classified from column A: the 11 GICS sectors, effect-bearing
  buckets (`Cash`, `Unclassified`), non-attributed buckets (`Bond`, `Other`,
  `Missing Performance`), and control rows (`Attribution Total`, `Total`,
  `Reported Total`, `Expense Ratio`, `Residual…`). Securities are rows with
  column B populated, and only those inside GICS sectors are used.

Optional sections are tolerated: Edgewood has no `Bond`, `Other` or
`Unclassified` section at all, and parses without a warning.

`Cash` carries a real allocation effect but is not a GICS sector, so it stays out
of `sector_attribution` and is surfaced separately as `cash_attribution` (§5.2a).
`Unclassified` remains debug-only, in `attributed_bucket_rows`.

---

## 8. `debug_attribution_report.json`

Written every run. Contains, per workbook:

* worksheets found and which was parsed, plus the `Template` manifest
* detected header rows and every period band with its dates, width and mapped fields
* the selected band and the `logical_period_map_summary`
  (`selected_quarter -> 4-1-2026 - 6-30-2026`)
* row classification counts, sector rows found, sectors missing
* attributed bucket rows (`Cash`, `Unclassified`) with their effects
* security rows scanned, stored and filtered out
* **manager linkage**: portfolio name, normalised key, match type, target file
  and index, and whether the benchmarks agree
* **`reconciliation`** — internal QA only: sector-sum vs attribution total,
  security-sum vs selection total, coverage percent, reported vs attributed
  excess and the gap, expense ratio, residuals, and the attribution benchmark
  return

Plus, at the top level: files processed, files written, unmatched files, errors,
the asset-class patch summary, and every warning.

---

## 9. Constants worth knowing

All at the top of `build_attribution_json.py`:

| Constant | Default | Effect |
| --- | --- | --- |
| `MATERIALITY_THRESHOLD_PP` | `0.05` | Minimum \|effect\| in pp for a security to be stored (5 bps) |
| `TOP_N_SECURITIES` | `10` | Length of the contributor / detractor lists |
| `TOP_N_SECTORS` | `5` | Length of the sector ranking lists |
| `CONCENTRATION_TOP_N` | `5` | The "top 5" in `concentration` |
| `NEUTRAL_ACTIVE_WEIGHT_PP` | `0.10` | Active weight band treated as `neutral` |
| `DRIVER_HIGH_CONFIDENCE_RATIO` | `2.00` | Dominance needed for `high` confidence |
| `DRIVER_MIN_DOMINANCE_RATIO` | `1.25` | Below this, driver becomes `mixed` |
| `RECONCILE_TOLERANCE_PP` | `0.05` | Debug-only reconciliation tolerance |

Raising `MATERIALITY_THRESHOLD_PP` shrinks the files; lowering it stores more
marginal names. The top/bottom 10 lists are unaffected either way.

---

## 10. What is deliberately not here

* **Industry attribution** — GICS Sector is the only classification in these
  exports. Use the Phase 1 market-data layer for industry context and word it as
  market movement, not manager attribution.
* **Residual, gap, coverage, expense, benchmark-reconstruction fields** — computed
  every run, kept in the debug report only (§3).
* **A `manager_index.json`** — the asset-class files remain the source of truth,
  as in Phase 1.
* **Full security lists** — see §5.3.
