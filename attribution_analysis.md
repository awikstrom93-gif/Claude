# Attribution Data Layer — Structure Analysis & Phase 2 Design

Analysis of three Morningstar Direct attribution exports, performed against the
actual files. No extraction code written. Purpose: decide how attribution should
be extracted and structured before any parser exists.

Workbooks analysed:

| Workbook | Portfolio (as stated in file) | Sheet rows | Size |
| --- | --- | --- | --- |
| T. Rowe Price Blue Chip Growth Attribution | T. Rowe Price Blue Chip Growth | 602 | 333 KB |
| Fidelity Blue Chip Growth Attribution | Fidelity Blue Chip Growth K | 1,913 | 814 KB |
| Edgewood Growth Instl Attribution | Edgewood Growth Instl | 513 | 295 KB |

**Headline conclusion:** these three files are structurally *identical* — same
sheets, same 94-column geometry, same header rows, same row taxonomy, same
attribution model. One generic parser covers all three with no manager-specific
handling. The genuine difficulties are not layout; they are **reconciliation,
coverage, and noise**, covered in §3.4 and §4.

---

## 1. Workbook structure analysis

### 1.1 Worksheets

Every workbook contains exactly two sheets:

| Sheet | Purpose | Relevance |
| --- | --- | --- |
| `Template` | Export manifest — portfolio, benchmark, currency, export timestamp, attribution model, classification scheme, date range, frequency | **Relevant** — read for identity, model and period validation |
| `Attribution` | The data grid; also repeats the five metadata lines in rows 1–5 | **Relevant** — the entire payload |

There are no irrelevant sheets to skip. Note that the TRP `Template` lists eight
enabled views (`Highlights`, `Highest/Lowest`, `Portfolio Statistics`,
`Trailing Performance`, `Valuation by Data Point`, `Weights`, `Attribution`,
`Holdings`) whereas Fidelity and Edgewood list two. **Those extra views did not
produce extra sheets or extra columns** — the workbook is still 2 sheets / 94
columns. The view list is a settings echo, not a content promise, and must not
be used to infer structure.

### 1.2 `Template` sheet — usable as a manifest

Fixed key–value layout in column A:

```
Name: T. Rowe Price Blue Chip Growth Russell 1000 Growth
Portfolio: T. Rowe Price Blue Chip Growth
Benchmark: Russell 1000 Growth TR USD
Currency: US Dollar
Date Exported: Wednesday, July 29, 2026 3:50:37 PM
...
Security Classification → "1. GICS Sector"
Dates & Calculation     → Start Date:01-01-2025
                          End Date:Last Quarter End
                          Display Frequency:Quarterly
                          Use Holdings by:Inferring Weights Forward in Time
```

Four facts matter downstream:

1. **`Portfolio:` matches the performance-JSON `manager` value exactly** for all
   three files. This is the join key — no fuzzy matching needed (§5.3).
2. **`Benchmark:` is `Russell 1000 Growth TR USD`** in all three, matching the
   benchmark already in `large_growth.json`.
3. **Classification is `1. GICS Sector` — a single level.** There is no industry
   or sub-industry breakdown anywhere in these files (§3.2).
4. **`Three-Factor: Allocation, Selection & Interaction Effects`** confirms the
   Brinson-Fachler three-factor model in all three.

### 1.3 `Attribution` sheet — grid geometry

Consistent across all three workbooks:

| Rows | Content |
| --- | --- |
| 1–5 | Metadata (duplicate of `Template` header) |
| 6–7 | Blank |
| **8** | Period band labels, merged 13 columns each |
| **9** | Metric group labels, merged (`Weights %`, `Return %`, `Contribution to Return %`, `Attribution Effect`) |
| **10** | Leaf headers (`Portfolio`, `Benchmark`, `+/-`, …, `Allocation %`, `Selection %`, `Interaction %`, `Active Ret%`) |
| 11+ | Data rows |

Columns A–C are identity (`GICS Sector`, `Name`, `Ticker`). Columns D–CP are
**seven period bands of exactly 13 columns each** (3 + 7×13 = 94 = column CP).

### 1.4 Period bands — attribution history already exists

Row 8 band labels, identical in all three files:

| Band | Columns | Window |
| --- | --- | --- |
| Cumulative | D:P | 1-1-2025 – 6-30-2026 |
| Q1 2025 | Q:AC | 1-1-2025 – 3-31-2025 |
| Q2 2025 | AD:AP | 4-1-2025 – 6-30-2025 |
| Q3 2025 | AQ:BC | 7-1-2025 – 9-30-2025 |
| Q4 2025 | BD:BP | 10-1-2025 – 12-31-2025 |
| Q1 2026 | BQ:CC | 1-1-2026 – 3-31-2026 |
| **Q2 2026** | **CD:CP** | **4-1-2026 – 6-30-2026** ← selected quarter |

This is a significant finding: **attribution is already multi-period and aligns
exactly with the `performance_periods` work in Phase 1.** Q2 2026 → `selected_quarter`,
Q1 2026 → `two_quarters_ago`, Q4 2025 → `three_quarters_ago`, Q3 2025 → `four_quarters_ago`.
The band labels are explicit date ranges, so they can be matched by date exactly
as the performance parser already does — no reliance on label text or position.

The 13 columns within each band are always, in order:

```
Weights %:                Portfolio | Benchmark | +/-
Return %:                 Portfolio | Benchmark | +/-
Contribution to Return %: Portfolio | Benchmark | +/-
Attribution Effect:       Allocation % | Selection % | Interaction % | Active Ret%
```

### 1.5 Row taxonomy — a two-level hierarchy plus control rows

Column A holds a section label; column B/C hold security name/ticker. A row with
column A populated opens a section; rows beneath it with column B populated are
its securities.

| Row type | Count | Attribution effects present |
| --- | --- | --- |
| GICS sector (11: Communication Services … Utilities) | 11 in all three | Allocation, Selection, Interaction, Active Ret |
| `Unclassified` | TRP, Fidelity only | Allocation only (nominal) |
| `Cash` | all three | Allocation only |
| **`Attribution Total`** | all three | **All four — the model total** |
| `Bond` | TRP, Fidelity only | None |
| `Missing Performance` | all three | None |
| `Other` | TRP, Fidelity only | None |
| `Total` | all three | Weights only |
| `Reported Total` | all three | Returns only |
| `Expense Ratio` | all three | Returns only |
| `Residual(Reported - Attribution + Expense)` | all three | Returns only |

**Security-level rows carry only `Selection %` and `Active Ret%`** — never
`Allocation %` or `Interaction %`, which exist solely at sector level. At
security level the two are numerically identical, so only one need be stored.

Verified relationship (exact, all three workbooks):

```
Σ security Active Ret%  ==  sector-level total Selection %
Fidelity  9.212 == 9.212      TRP −2.469 == −2.469      Edgewood −6.209 == −6.209
```

Allocation and Interaction are **not** distributed to securities. This is the
natural parser self-check.

### 1.6 Manager-specific vs quarter-specific sections

* **Quarter-specific:** only the seven period bands. Every row exists in every
  band; there are no quarter-specific rows.
* **Manager-specific:** only *which optional sections are present* (`Bond`,
  `Other`, `Unclassified` — absent from Edgewood) and *how many securities* each
  section holds. No manager introduces a new column, header row, or metric.

---

## 2. Cross-workbook comparison

### 2.1 Common to all three (safe to generalise)

* Two sheets: `Template`, `Attribution`
* 94 columns; 3 identity + 7 bands × 13
* Header rows at 8 / 9 / 10; data from row 11
* Identical band labels and windows
* Identical 13-column metric order
* Identical three-factor model and GICS Sector classification
* Same benchmark (`Russell 1000 Growth TR USD`)
* All 11 GICS sectors present as rows even at zero weight
* Same control rows (`Attribution Total`, `Total`, `Reported Total`,
  `Expense Ratio`, `Residual…`)
* `Portfolio:` value matches the performance-JSON manager name exactly

### 2.2 What varies by manager

| Dimension | TRP | Fidelity | Edgewood |
| --- | --- | --- | --- |
| Sheet rows | 602 | 1,913 | 513 |
| Securities inside GICS sectors | 493 | 805 | 480 |
| **Securities actually held** | **60** | **284** | **23** |
| Benchmark-only names with an effect | 420 | 502 | 445 |
| `Unclassified` section | yes | yes | **no** |
| `Bond` section | yes (45 rows) | yes (98 rows) | **no** |
| `Other` section | yes (22 rows) | yes (613 rows) | **no** |
| `Missing Performance` rows | 7 | 348 | 2 |
| **Attribution coverage (Q2 2026)** | **98.2%** | **94.0%** | **100.0%** |

### 2.3 What can be generalised vs what needs special handling

**Fully generalisable** — everything structural. One parser, no per-manager
configuration, no layout branching. Detection should be by header text and band
date range (exactly the approach already proven in Phase 1), not by fixed cell
addresses, so that a future manager with more or fewer sections still parses.

**Requires handling, but as data-driven logic rather than manager-specific code:**

1. **Optional sections.** `Bond`/`Other`/`Unclassified` must be treated as
   *may be absent*, never assumed. Edgewood proves the case.
2. **Coverage normalisation.** `Attribution Total` portfolio weight is 94.0% for
   Fidelity but 100.0% for Edgewood. Effects are not comparable across managers
   without publishing coverage alongside them.
3. **Security-row noise.** 62% (Fidelity) to 93% (Edgewood) of security rows are
   benchmark constituents the manager does not hold. They are meaningful (not
   owning a big winner hurts) but must be labelled, not silently mixed with
   holdings.

There is **no case for manager-specific parser code.**

---

## 3. Attribution components — verified, not assumed

### 3.1 Present

| Component | Level | Column within band | Notes |
| --- | --- | --- | --- |
| Allocation effect | Sector only | 10th | |
| Selection effect | Sector + security | 11th | |
| Interaction effect | Sector only | 12th | Three-factor model, kept separate |
| Active return (total effect) | Sector + security | 13th | Security-level == Selection |
| Portfolio weight | Sector + security | 1st | |
| Benchmark weight | Sector + security | 2nd | |
| Active weight (+/-) | Sector + security | 3rd | |
| Portfolio return | Sector + security | 4th | |
| Benchmark return | Sector + security | 5th | |
| Return differential | Sector + security | 6th | |
| Contribution to return (portfolio / benchmark / +/-) | Sector + security | 7th–9th | |
| Attribution total | Total row | — | Sum of sector effects |
| Reported total return | Total row | — | Portfolio and benchmark |
| Expense ratio | Total row | — | |
| Residual | Total row | — | Reconciliation line |
| Cash effect | `Cash` row | — | Allocation only |

### 3.2 Absent — do not design for these

* **Industry or sub-industry attribution.** Classification is `1. GICS Sector`
  only. Any industry-level narrative must come from the *market* data already in
  the asset-class JSON, not from these files. **This directly affects the
  requested "Industry Attribution" schema — see §6.3.**
* **Pre-built top contributor / detractor lists.** Despite TRP's template listing
  a `Highest/Lowest` view, no such section exists in the file. These must be
  derived by ranking security rows.
* **Currency attribution** — single-currency (USD) exports.
* **Factor or style attribution** — not present; already covered by the market
  data layer.
* **Sector-level totals per security** beyond Selection — allocation and
  interaction are not decomposed to holdings.

### 3.3 Multi-period attribution totals (Active Ret%, by quarter)

| Quarter | TRP | Fidelity | Edgewood |
| --- | --- | --- | --- |
| Q1 2025 | +0.841 | −3.494 | +2.994 |
| Q2 2025 | +1.141 | +2.014 | −3.315 |
| Q3 2025 | −1.939 | +1.189 | −11.739 |
| Q4 2025 | +0.809 | +0.767 | +1.787 |
| Q1 2026 | −1.516 | +1.349 | −3.577 |
| **Q2 2026** | **−3.100** | **+9.265** | **−6.111** |
| Cumulative | −4.237 | +11.293 | −20.854 |

### 3.4 Three reconciliation issues that must be surfaced, not hidden

**(a) Attribution total ≠ reported excess return.**

| Manager | Reported excess (perf JSON) | Attribution Active Ret% | Gap |
| --- | --- | --- | --- |
| Fidelity Blue Chip Growth K | +9.6715 | +9.265 | **+0.406** |
| T. Rowe Price Blue Chip Growth | −3.4679 | −3.100 | **−0.368** |
| Edgewood Growth Instl | −7.5423 | −6.111 | **−1.431** |

The workbook explains the gap itself via `Reported Total`, `Expense Ratio` and
`Residual(Reported - Attribution + Expense)`. For Edgewood the gap is 1.43 pp on
a −7.54 pp excess — **19% of the underperformance is outside the attribution
model.** If the agent writes "sector effects explain the −7.54% shortfall" it is
wrong. The schema must carry the reported excess, the attribution total, and the
gap explicitly, with guidance to attribute only what the model covers.

**(b) The attribution benchmark return is not the index return.**
Attribution shows the benchmark at **15.266%** for Q2 2026; the performance
workbook shows `Russell 1000 Growth TR USD` at **16.741%**. The difference
(1.475 pp) is the benchmark's own holdings-reconstruction residual. Attribution
figures and performance figures must never be mixed in one sentence.

**(c) Coverage varies materially.** Fidelity's attribution covers 94.0% of the
portfolio; 5.5% sits in `Other` and 0.5% in `Missing Performance`, neither
carrying effects. Effect sizes are therefore not strictly comparable between
Fidelity and Edgewood without stating coverage.

---

## 4. Minimum data set for battle book generation

### Critical — without these no attribution commentary is possible

| Item | Why |
| --- | --- |
| Manager identity + benchmark + period window | Join key and provenance; prevents wrong-quarter or wrong-benchmark claims |
| `Attribution Total`: allocation, selection, interaction, active return | The single most important sentence in any attribution paragraph is whether results were selection- or allocation-driven |
| Sector-level allocation / selection / interaction / active return (11 sectors) | Produces "stock selection in Information Technology detracted 120 bps" — the core requested output |
| Sector portfolio weight, benchmark weight, active weight | Required to say *why* allocation helped or hurt (over/underweight) |
| Reported excess return + attribution total + **gap** | Prevents overclaiming; see §3.4(a) |
| Attribution coverage % | Prevents comparing a 94%-covered book with a 100%-covered one |

### High Value — materially better commentary

| Item | Why |
| --- | --- |
| Top N contributors / detractors by active return (security) | "Weakness was concentrated in a small number of holdings" needs names and sizes |
| Held vs not-held flag on each security | "Not owning AMD cost 94 bps" is a different sentence from "owning AMD cost 94 bps" |
| Security portfolio/benchmark/active weight and return | Lets the agent explain magnitude, not just direction |
| Sector portfolio and benchmark returns | Distinguishes "the sector fell" from "our stocks in it fell" |
| Prior-quarter attribution totals (5 extra quarters, already present) | Supports "weakness is recent rather than structural" — pairs directly with the Phase 1 trend layer |
| Concentration metric (share of total effect from top 5) | Directly answers concentrated vs broad-based |

### Nice to Have

| Item | Why |
| --- | --- |
| Full sector history (per-sector effects for all 6 quarters) | Enables "IT selection has detracted for three straight quarters" — genuinely useful but multiplies payload |
| `Cash` drag | Small but occasionally the story (Edgewood: −0.310 in Q2 2026) |
| Contribution-to-return columns | Largely redundant with weights × returns |
| Expense ratio and residual detail | Needed for audit, rarely quoted |

### Ignore

| Item | Why |
| --- | --- |
| `Bond`, `Other`, `Missing Performance` security rows | 613 rows for Fidelity alone, no attribution effects, pure payload bloat. Keep only their aggregate weight as a coverage note |
| Securities with zero weight in both portfolio and benchmark and zero effect | Structural padding |
| `Unclassified` beyond its weight | Nominal |
| The `1-1-2025 – 6-30-2026` cumulative band | Spans an arbitrary 18-month window that matches no battle-book period; misleading if quoted. Retain only if a use case appears |
| Full 1,880-row security list | Ranking to top/bottom N delivers the entire narrative value at ~2% of the size |

---

## 5. Storage architecture — Option A vs Option B

### Recommendation: **Option A — one JSON file per manager per quarter**, plus a thin availability pointer inside the asset-class JSON.

```
Quarterly Battle Book Data/
  2026 Q2/
    large_growth.json          ← gains a small attribution_index
    mid_growth.json
    small_growth.json
    manifest.json
    debug_layout_report.json
    attribution/
      t_rowe_price_blue_chip_growth.json
      fidelity_blue_chip_growth_k.json
      edgewood_growth_instl.json
```

### Why not Option B (embedding)

1. **Coverage is sparse and will stay sparse.** Three of 27 Large Growth managers
   have attribution. Embedding forces every consumer of `large_growth.json` to
   carry data relevant to 11% of its records.
2. **Size.** `large_growth.json` is already 288 KB after the Phase 1 history work.
   Even trimmed attribution (sector table + top/bottom 10 + totals) adds roughly
   8–15 KB per manager; a fully-covered universe would roughly double the file.
   Full security lists would be catastrophic — Fidelity alone is 1,913 rows.
3. **Retrieval precision.** The agent's flow is already: find manager → identify
   asset class → pull record. A per-manager attribution file is one extra
   deterministic fetch keyed on data it already holds. Embedding instead dilutes
   the asset-class file with content that is irrelevant to most queries — the
   opposite of optimising for retrieval quality.
4. **Different refresh cadence and provenance.** These exports were generated on
   two different days (29 and 30 July) by a separate manual process, and each has
   its own export timestamp and its own reconciliation gap. Separate files keep
   lineage honest and let one manager's attribution be regenerated without
   rewriting a file that 27 managers depend on.
5. **Blast radius.** A malformed attribution workbook should fail one small file,
   not corrupt the asset-class file the whole battle book depends on.

### The pointer that keeps one-hop retrieval

Add to each asset-class JSON — small, and preserving the "one lookup" property:

```
"attribution_index": {
  "available": ["t rowe price blue chip growth", "fidelity blue chip growth k",
                "edgewood growth instl"],
  "path_pattern": "attribution/{manager_slug}.json",
  "manager_slugs": { "t rowe price blue chip growth": "t_rowe_price_blue_chip_growth" }
}
```

and a boolean `attribution_available` on each manager record. The agent then
knows, without a second lookup, whether attribution exists and exactly where.

### 5.3 Linking

`Template!A2` (`Portfolio: …`) matches the performance-JSON `manager` value
**exactly** for all three files. Applying the existing `normalize_lookup_name`
rules gives the same key already used by `manager_lookup`. No fuzzy matching is
needed; a strict match that *fails loudly* when it does not resolve is correct,
because a silent mis-link would attach one manager's attribution to another.

---

## 6. Proposed attribution JSON schema

One file per manager per quarter. Field names deliberately mirror the existing
performance JSON conventions (`period`, `lineage`, `*_effect`, explicit dates).

### 6.1 Attribution Summary

```
{
  "manager": "T. Rowe Price Blue Chip Growth",
  "manager_lookup_key": "t rowe price blue chip growth",
  "asset_class": "US Large Growth",
  "benchmark": "Russell 1000 Growth TR USD",
  "period": "2026 Q2",
  "period_start_date": "2026-04-01",
  "period_end_date": "2026-06-30",
  "period_band_label": "4-1-2026 - 6-30-2026",

  "model": "three_factor_brinson",
  "classification": "GICS Sector",

  "attribution_summary": {
    "allocation_effect": -0.287,
    "selection_effect": -2.469,
    "interaction_effect": -0.344,
    "total_active_return": -3.100,

    "portfolio_return_attribution": 12.166,
    "benchmark_return_attribution": 15.266,

    "reported_return": 13.273,
    "reported_benchmark_return": 16.741,
    "reported_excess_return": -3.468,

    "attribution_vs_reported_gap": -0.368,
    "expense_ratio": 0.198,
    "residual_portfolio": 1.305,
    "residual_benchmark": 1.475,

    "coverage_percent": 98.212,
    "unattributed_weight_percent": 1.816,
    "reconciles": true,

    "primary_driver": "selection",
    "driver_confidence": "high",
    "interpretation_note": "Attribution explains -3.100 of the -3.468 reported excess return. The remaining -0.368 is expense ratio and pricing residual and must not be attributed to sector or security effects."
  },

  "lineage": {
    "source_file": "T. Rowe Price Blue Chip Growth Attribution.xlsx",
    "worksheet": "Attribution",
    "exported_at": "2026-07-29T15:50:37",
    "generated_at": "2026-07-30T17:00:00-00:00"
  }
}
```

### 6.2 Sector Attribution

```
"sector_attribution": [
  {
    "sector": "Information Technology",
    "portfolio_weight": 47.696,
    "benchmark_weight": 52.506,
    "active_weight": -4.810,
    "portfolio_return": 14.158,
    "benchmark_return": 19.683,
    "return_differential": -5.525,
    "allocation_effect": -0.156,
    "selection_effect": -2.874,
    "interaction_effect": 0.228,
    "total_effect": -2.802,
    "effect_rank": 11,
    "share_of_total_active_return": 0.904,
    "position": "underweight",
    "driver": "selection"
  }
]
```

`share_of_total_active_return` is the share of the **same-signed** effect total,
so the agent can say "IT selection accounted for most of the shortfall" without
computing it. `position` is derived from `active_weight` against a small
threshold. All 11 GICS sectors are emitted even at zero weight, because a zero
weight *is* the story for Edgewood (0% Consumer Staples, +0.424 allocation).

### 6.3 Industry Attribution — **not available; do not build**

```
"industry_attribution": {
  "available": false,
  "reason": "Morningstar export classification is '1. GICS Sector' (single level). No industry or sub-industry breakdown exists in these workbooks.",
  "alternative": "Use market_data.industries / summaries.top_10_industries in the asset-class JSON for industry context. That is index-level market movement, NOT manager attribution, and must be worded accordingly."
}
```

This is the one requested object the data cannot support. Rather than omit the
key, carry it with an explicit `available: false` so the agent has a definitive
answer instead of inferring absence. If industry attribution is genuinely wanted,
it requires re-running the Morningstar export with a second classification level
(e.g. `2. GICS Industry Group`) — a source-side change, not a parser change.
That change would add a second block of sector-shaped rows and the schema above
would extend to it unchanged.

### 6.4 Security Attribution

Store only the meaningful subset (see §4 Ignore): securities held, plus
not-held benchmark names whose effect exceeds a materiality threshold.

```
"security_attribution": [
  {
    "security": "Advanced Micro Devices Inc",
    "ticker": "AMD",
    "sector": "Information Technology",
    "held": true,
    "portfolio_weight": 0.375,
    "benchmark_weight": 1.011,
    "active_weight": -0.637,
    "portfolio_return": 63.872,
    "benchmark_return": 185.558,
    "contribution_portfolio": 0.346,
    "contribution_benchmark": 1.207,
    "contribution_active": -0.861,
    "selection_effect": -0.720,
    "total_effect": -0.720
  }
],
"security_attribution_meta": {
  "securities_in_gics_sectors": 493,
  "securities_held": 60,
  "securities_stored": 40,
  "materiality_threshold": 0.05,
  "note": "Security-level rows carry selection effect only; allocation and interaction exist at sector level. Sum of security total_effect equals the sector-level selection total exactly."
}
```

### 6.5 Top Contributors

```
"top_contributors": [
  {
    "rank": 1,
    "security": "ASML Holding NV ADR",
    "ticker": "ASML",
    "sector": "Information Technology",
    "held": true,
    "total_effect": 0.401,
    "active_weight": 0.612,
    "portfolio_return": 39.769,
    "benchmark_return": 39.769,
    "reason": "overweight_outperformer"
  }
]
```

### 6.6 Top Detractors

```
"top_detractors": [
  {
    "rank": 1,
    "security": "Lam Research Corp",
    "ticker": "LRCX",
    "sector": "Information Technology",
    "held": true,
    "total_effect": -0.756,
    "active_weight": -0.884,
    "portfolio_return": 44.061,
    "benchmark_return": 92.144,
    "reason": "underweight_outperformer"
  }
]
```

`reason` is a derived enum — `overweight_outperformer`, `overweight_underperformer`,
`underweight_outperformer`, `underweight_underperformer`, `not_held_outperformer`,
`not_held_underperformer` — which converts a number into a sentence the agent can
use directly, and prevents the common error of describing a not-held name as a
holding.

### 6.7 Attribution history

Six quarters already exist in every file at near-zero extra parsing cost:

```
"attribution_periods": {
  "selected_quarter":   { "label": "4-1-2026 - 6-30-2026", "allocation_effect": -0.287, "selection_effect": -2.469, "interaction_effect": -0.344, "total_active_return": -3.100, "coverage_percent": 98.212 },
  "two_quarters_ago":   { "…": "…" },
  "three_quarters_ago": { "…": "…" },
  "four_quarters_ago":  { "…": "…" }
},
"attribution_trends": {
  "consecutive_quarters_selection_negative": 2,
  "consecutive_quarters_allocation_negative": 2,
  "dominant_driver_selected_quarter": "selection",
  "dominant_driver_trailing_4_quarters": "selection",
  "driver_stability": "consistent"
}
```

Totals-only for history (not the full sector grid) keeps the file small while
supporting "selection has been the drag for three straight quarters".

---

## 7. What to precompute

Every item below is cheap to compute at build time and expensive or error-prone
for the agent to derive from raw numbers.

| Precomputed field | Replaces this agent reasoning |
| --- | --- |
| `primary_driver` (`selection` / `allocation` / `interaction` / `mixed`) | Comparing three effects and judging materiality |
| `largest_positive_contributor` / `largest_detractor` (security) | Sorting hundreds of rows |
| `strongest_sector_selection` / `weakest_sector_selection` | Sorting 11 sectors on one column |
| `strongest_sector_allocation` / `weakest_sector_allocation` | As above |
| `share_of_total_active_return` per sector | Division the agent should never do in prose |
| `concentration_top5_share` | "Concentrated vs broad-based" — see §8 |
| `breadth`: count of sectors with positive vs negative effect | Same |
| `position` (`overweight` / `underweight` / `not_held`) | Comparing two weights |
| `reason` enum per contributor/detractor | The single most error-prone sentence in attribution writing |
| `held` flag | Prevents describing a benchmark name as a holding |
| `attribution_vs_reported_gap` + `reconciles` | Stops overclaiming (§3.4a) |
| `coverage_percent` | Contextualises effect sizes |
| `basis_points` mirrors of each effect | Battle books quote bps; avoids the agent multiplying by 100 |

A note on the last one: commentary is written in basis points ("detracted 120
bps") while the workbook is in percent (`-1.20`). Emitting both `*_effect` and
`*_effect_bps` removes an arithmetic step that models get wrong at exactly the
moment precision matters most.

---

## 8. Narrative opportunities, grounded in the actual data

### T. Rowe Price Blue Chip Growth — *broad-based, selection-driven shortfall*

Q2 2026 active −3.100 (allocation −0.287, selection −2.469, interaction −0.344);
IT selection −2.874 on a 47.7% weight; top 5 names = 12.9% of total absolute effect.

* "Underperformance was driven by stock selection rather than sector positioning —
  selection detracted 247 bps against 29 bps from allocation."
* "Security selection within Information Technology was the largest single source
  of underperformance, detracting 287 bps."
* "Weakness was broad rather than concentrated: the five largest detractors
  accounted for only 13% of total active effect."
* "Not owning Lam Research, which returned 92% in the quarter, cost 76 bps."
* Trend: selection has been negative in two consecutive quarters (Q1 −0.809,
  Q2 −2.469) — pairs with the Phase 1 `deteriorating` trend direction.

### Fidelity Blue Chip Growth K — *concentrated, selection-driven outperformance*

Q2 2026 active +9.265 (allocation +0.338, selection +9.212); IT selection +7.709;
top 5 = 34.5% of total absolute effect; coverage 94.0%.

* "Outperformance was overwhelmingly selection-driven — stock selection added
  921 bps versus 34 bps from sector allocation."
* "Security selection within Information Technology alone contributed 771 bps."
* "Results were concentrated: Marvell Technology (+278 bps), Micron (+151 bps)
  and SanDisk (+130 bps) accounted for the bulk of the excess return."
* Caveat the agent must honour: attribution covers 94.0% of the portfolio and
  explains +9.265 of the +9.672 reported excess.

### Edgewood Growth Instl — *single-stock concentration, structural*

Q2 2026 active −6.111 (allocation −0.067, selection −6.209); only 23 holdings;
Netflix alone −2.950; top 5 = 25.9% of absolute effect; coverage 100.0%.

* "With only 23 holdings, results are highly stock-specific: Netflix alone
  detracted 295 bps, roughly half the quarter's shortfall."
* "Sector allocation was essentially neutral (−7 bps); the entire shortfall came
  from security selection (−621 bps)."
* "Zero weight in Consumer Staples, Energy, Real Estate and Utilities contributed
  modestly positively via allocation."
* Trend and honesty: attribution explains −6.111 of the −7.542 reported excess;
  the remaining 143 bps is expense and residual and must not be attributed to
  stock picking. Q3 2025 (−11.739) was far worse — useful for "recent weakness is
  an improvement on last year".

### Narrative types the data supports

Concentrated vs broad-based weakness · allocation-driven vs selection-driven ·
sector-specific contribution · not-held opportunity cost · zero-weight allocation
effects · driver persistence across quarters · cash drag · coverage caveats.

### Narrative types the data does **not** support

Industry-level attribution · factor/style attribution · currency effects ·
intra-quarter timing or trading effects · any claim that sector effects fully
explain the reported excess return.

---

## 9. Parser complexity estimate

**Overall: Medium** — but the difficulty is concentrated in a small number of
semantic decisions, not in layout handling.

| Component | Complexity | Reasoning |
| --- | --- | --- |
| Worksheet + metadata discovery | **Low** | Two sheets, fixed key–value lines, already-proven patterns |
| Period band detection | **Low** | Row 8 carries explicit date ranges; reuses the Phase 1 date-matching resolver almost unchanged |
| Column mapping within a band | **Low** | Fixed, verified 13-column order; still map by row 9/10 header text rather than offset |
| Row classification (sector vs security vs control) | **Low–Medium** | Clean rule (column A vs column B), but the control-row vocabulary must be enumerated and optional sections tolerated |
| Sector table extraction | **Low** | 11 well-behaved rows |
| Security extraction + filtering | **Medium** | 500–1,900 rows per file; needs held/not-held logic and a materiality threshold to avoid bloat |
| Contributor/detractor ranking + `reason` enum | **Medium** | Straightforward ranking, but the enum must be exactly right — this is where wrong sentences originate |
| Reconciliation + coverage | **Medium–High** | Requires cross-referencing `Attribution Total`, `Reported Total`, `Expense Ratio`, `Residual` **and** the performance JSON, then deciding tolerance for `reconciles` |
| Multi-period history | **Low** | Same rows, different band — nearly free once bands are resolved |
| Manager linkage to performance JSON | **Medium** | Mechanically trivial (exact match) but must fail loudly; a silent mis-link is the worst failure mode in the system |

**Not High overall** because there is no manager-specific layout handling, no
merged-cell ambiguity in the data region, no repeated-section scanning, and no
label-order dependence — genuinely simpler than the Small Growth performance
workbook already handled in Phase 1.

**Risks that would raise it to High** if they materialise: a manager exported with
a second classification level (adds a nested row tier); a non-USD portfolio
(adds currency columns); a different attribution model (two-factor would remove
the interaction column and shift the band width from 13).

---

## 10. Recommended Phase 2 architecture

### 10.1 Pipeline shape

A separate entry point (`build_attribution_json.py`) rather than an extension of
the quarterly builder, invoked over the same quarter folder:

```
Quarterly Battle Book Inputs/
  2026 Q2/
    US Large Growth.xlsx                     ← Phase 1
    US Mid Growth.xlsx                       ← Phase 1
    US Small Growth.xlsx                     ← Phase 1
    US Growth Sector Industry Factor.xlsx    ← Phase 1
    Attribution/
      T. Rowe Price Blue Chip Growth Attribution.xlsx
      Fidelity Blue Chip Growth Attribution.xlsx
      Edgewood Growth Instl Attribution.xlsx
```

Separate because attribution coverage is partial and manual, its inputs arrive on
a different schedule, and a failure there must not block the quarterly build.

### 10.2 Run order and coupling

1. Run the Phase 1 quarterly build (unchanged).
2. Run the attribution build, which **reads the just-written asset-class JSONs**
   to resolve each manager, confirm the asset class, and pull the reported excess
   return for reconciliation.
3. The attribution build writes `attribution/*.json` and **patches** the
   asset-class files with `attribution_index` plus per-manager
   `attribution_available`.

Reading Phase 1 output rather than re-parsing the performance workbooks keeps a
single source of truth for manager identity and reported returns.

### 10.3 Reuse from Phase 1

Directly reusable with little change: quarter resolution and folder discovery,
`normalize_lookup_name`, the date-based period-block resolver, header-text column
mapping, the `lineage` convention, the warnings/`debug_layout_report` pattern, and
the constants-at-the-top layout-assumption block. The attribution parser should
be recognisably the same codebase, not a parallel dialect.

### 10.4 Output contract

* One file per manager per quarter under `attribution/`.
* Sections as specified in §6: summary, sector table, security subset,
  top contributors, top detractors, attribution history, trends, lineage.
* `industry_attribution` present with `available: false` and the reason.
* Every effect emitted in both percent and basis points.
* A `debug_attribution_report.json` mirroring the Phase 1 debug file: bands
  detected, row classification counts, sections found/missing, securities kept vs
  filtered, reconciliation results per manager, and every warning.

### 10.5 Validation gates before the agent is pointed at the data

1. Σ security `total_effect` == sector-level selection total (exact identity
   verified in all three files).
2. Σ sector `total_effect` == `Attribution Total` active return.
3. `attribution_total + gap == reported_excess`, with `gap` reported, never
   silently absorbed.
4. Coverage percent present and within 90–101%; warn outside.
5. Manager linkage resolved for every attribution file, or hard failure.
6. Benchmark in the attribution file matches the benchmark in the asset-class
   JSON for that manager.

### 10.6 Guidance the commentary agent needs alongside the data

Three rules, best encoded in the agent's construction rules rather than left to
inference:

1. Attribution explains `attribution_total`, not `reported_excess_return`. Quote
   the gap when it is material (Edgewood: 143 bps of 754).
2. Never mix attribution returns with performance-JSON returns in one sentence —
   the benchmark differs (15.27% vs 16.74% for Q2 2026).
3. Check `held` before describing any security; use the `reason` enum wording.

### 10.7 Suggested sequencing

1. Parse `Template` + metadata + `Attribution Total` for one quarter; prove
   linkage and reconciliation across all three files. *Smallest slice that
   de-risks the hardest part.*
2. Add the sector table and precomputed sector rankings — this alone unlocks most
   of the target commentary.
3. Add security filtering, contributors/detractors and the `reason` enum.
4. Add the six-quarter history and driver trends.
5. Patch the asset-class files with the availability index.
6. Extend `validation_checklist.md` with the §10.5 gates.

### 10.8 Open question for you

Only one decision genuinely needs your input before Phase 2 starts: **is
industry-level attribution required?** It is unavailable in the current exports
and cannot be parsed into existence. If it is required for the battle book
format, the Morningstar export template needs a second classification level added
before the next quarterly pull — a source change with a lead time, which is why
it is worth deciding now rather than at build time.
