# Review — Copilot Studio Commentary Agent Instructions

Assessment of the current instruction set plus the proposed attribution addition,
checked against what the Phase 1 / Phase 2 JSON actually contains.

**Verdict:** the addition is directionally right and its two most important rules
(`held` / `reason`, and the audit-language ban) are exactly correct. But it is
incomplete, it is placed where it will be read last, and it now contradicts an
existing line in the instructions. It should be replaced rather than appended.

---

## 1. What the addition gets right

Keep all of these verbatim:

* Gating on `attribution_available` and routing via `attribution_path`.
* `attribution_summary` for the allocation / selection / interaction / mixed call.
* Checking `held` and `reason` **before describing any security**. This is the
  single highest-value rule in the block.
* `cash_attribution.material` as the gate, and not treating Cash as a GICS sector.
* Banning residual / gap / coverage / expense / reconciliation language.
* Refusing to build industry attribution from the attribution JSON.

---

## 2. The one thing that is now broken

The existing instructions say:

> 5. Analyze attribution using the attribution workbook.

There is no workbook any more. Attribution is JSON, and the addition tells the
agent to use JSON — so the instruction set now contains two conflicting
directives about the same task. The same problem exists here:

> Use the Sector, Industry, and Factor file as the primary source.

That file is no longer read by the agent either; its contents are embedded in
each asset-class JSON as `market_data`, `summaries` and `market_data_periods`.

Both lines must be edited, not merely supplemented. An agent that resolves the
conflict the wrong way will go looking for a spreadsheet it cannot open.

---

## 3. Gaps, ranked by risk

| # | Gap | Consequence | Severity |
| --- | --- | --- | --- |
| 1 | No `effect_rank` warning | Reports contributors as detractors | **Critical** |
| 2 | No `attribution_available = false` path | Undefined behaviour in 96% of cases | **Critical** |
| 3 | `basis` never mentioned | Annualized returns described as total returns | **Critical** |
| 4 | Market YTD window misalignment not flagged | Two different periods presented as one | High |
| 5 | Peer percentile direction never stated | Inverted "top quartile" claims | High |
| 6 | `held` not enforced on *contributors* | Not-held name described as a holding | High |
| 7 | History fields never referenced | Trend claims invented instead of sourced | High |
| 8 | `periods_evaluated` not mentioned | "Never underperformed" when data is missing | Medium |
| 9 | No retrieval algorithm | Wrong manager or wrong quarter | Medium |
| 10 | Data-vs-document precedence unstated | A style doc could override a number | Medium |

### 1. `effect_rank` is ranked by **absolute** effect — verified trap

`sector_attribution[].effect_rank` sorts by |total effect|, so rank 1 is the
sector that mattered most **in either direction**. For an outperforming fund the
top ranks are contributors:

```
Fidelity Blue Chip Growth K   (total +926.5 bps)
  effect_rank 1-3        : Information Technology +774.1, Consumer Discretionary +51.5, Financials +45.7
  top_detracting_sectors : Communication Services -35.1, Energy -5.2, Utilities -1.6
```

An agent told "discuss the top 3 detracting sectors" that reaches for
`effect_rank` 1–3 would report **+774 bps of contribution as detraction**. The
instructions must name `sector_rankings.top_detracting_sectors` and
`top_contributing_sectors` explicitly and forbid using `effect_rank` for
direction.

### 2. No path for `attribution_available = false`

This is the majority case by a wide margin:

```
large_growth.json     3/27 managers have attribution
mid_growth.json       0/22
small_growth.json     0/30
```

For 76 of 79 managers there is no attribution file at all. The instructions
currently say "Analyze attribution using the attribution workbook" with no
conditional, so the agent's most likely failure is inventing attribution from
sector market returns — fluent, plausible, and wrong. An explicit degraded mode
is required.

### 3. `basis` — annualized vs cumulative

```
trailing_1_year    ret=10.7933   basis=cumulative
trailing_3_year    ret=21.4637   basis=annualized
trailing_10_year   ret=16.4067   basis=annualized
```

`return_cumulative` on `trailing_3_year` is **21.46% per year**, not 21.46% over
three years. The field name is misleading for schema-compatibility reasons and
`basis` is the disambiguator. Nothing in the instructions mentions it.

### 4. Market YTD is a different window from manager YTD

```
manager ytd : 2026-01-01 -> 2026-06-30
market  ytd : 2026-01-01 -> 2026-07-24   aligned_with_selected_quarter_end: false
```

The market workbook has no quarter-end YTD block, so its YTD runs to the export
date. Pairing "the fund trails YTD" with "Energy led YTD" silently compares two
different periods. Each market period carries `aligned_with_selected_quarter_end`
and an explanatory `note` for exactly this reason.

### 5. Peer percentile runs 1 (best) to 100 (worst)

```
Vanguard PRIMECAP Inv: return=34.19  percentile=3
```

Highest return, lowest number. A *falling* percentile is an *improving* rank. The
instructions reference peer percentile seven times and never state the direction.

### 6. `held` matters on contributors, not just detractors

Not-held names appear in `top_contributors` too — not owning a loser helps:

```
contributor: Intuit Inc  held=false  +23.4 bps  reason=not_held_underperformer
contributor: Adobe Inc   held=false  +11.8 bps  reason=not_held_underperformer
```

The addition's phrasing implies the check matters mainly for detractors. It
applies to every security in both lists.

### 7. The history layer is never referenced

The instructions repeatedly ask for historical context — "whether the quarter was
meaningful relative to peers and historical results", "a continuation of an
existing trend", "a change in trend" — but never say where that lives. All of it
is precomputed:

* `performance_periods` — 9 periods per manager
* `performance_trends` — streaks, `trend_direction`, per-period underperformance flags
* `ranking_trend` — percentile across six periods
* `attribution_periods` / `attribution_trends` — 4 quarters of attribution, driver persistence
* `concentration` — concentrated vs broad-based

Meanwhile the instructions say *"Avoid making evaluative conclusions about
improvement, deterioration, conviction, trend reversal unless explicitly supported
by source data."* That support exists — the agent just has not been told about it.
As written, the rule suppresses trend commentary the data fully justifies.

### 8. `periods_evaluated`

`underperformed_trailing_10_year: false` means either "beat the benchmark" **or**
"no data". `performance_trends.periods_evaluated` lists what was actually
evaluable. Without it, "has not underperformed over any trailing period" can be
asserted from missing data.

### 9. Retrieval is undefined

Nothing tells the agent how to find a manager. The mechanism exists and is
deterministic: normalise the name, check `manager_lookup` in each asset-class
file, and the file it is found in *is* the asset class. Left unstated, the agent
will semantic-search and may land on the wrong manager or the wrong quarter.

### 10. Precedence does not cover data

The conflict hierarchy ranks six documents but omits the JSON. Worth stating
plainly: **documents govern how to write; data files govern what is true.** No
style or playbook document may override a number.

---

## 4. Drop-in replacement for the added block

Replace the appended section entirely. Also edit the two lines named in §2.

> **Placement:** put the "Data sources and retrieval" part immediately after the
> knowledge-source priority list near the top — not at the end. The agent needs
> to know where facts come from before it is told how to write. Everything below
> the retrieval section can stay where the current block sits.

```
DATA SOURCES AND RETRIEVAL

Knowledge documents govern how to think, what to include, and how to write.
The JSON data files govern what is true. No document may override a number in
the data. Every figure in the commentary must be traceable to a field in these
files. Never state a number that is not in the data.

To locate a manager:
1. Normalise the requested name: lowercase, trim, collapse repeated spaces,
   remove periods.
2. Look the name up in manager_lookup in large_growth.json, mid_growth.json and
   small_growth.json for the requested quarter.
3. The file containing the manager determines the asset class. The value is the
   index into that file's managers array.
4. Confirm the file's period field matches the requested quarter before using it.
   If it does not, stop and say the data for that quarter is unavailable.

Use these files for facts:
- Performance, peer ranking, history and market context: the asset-class JSON.
- Attribution: the per-manager attribution JSON, when available.

CORE DATA CONVENTIONS

Peer percentile runs from 1 (best) to 100 (worst). A lower number is a better
rank. A falling percentile over time is an improving rank. Never describe a
rising percentile as improvement.

Before quoting any trailing return, read the basis field on that period.
basis = "annualized" (trailing 3, 5 and 10 year) means the figure is per year.
basis = "cumulative" means it is a total for the period. The field is named
return_cumulative in both cases; basis is what disambiguates it. Never describe
an annualized return as a total return.

Missing values are null. Null means the data is unavailable, not zero and not
neutral. Never interpret a null as a result.

Quote attribution effects in basis points using the _bps fields. Do not convert
percentages yourself.

PERFORMANCE AND HISTORY

For the Call to Action, use the manager record: return_cumulative,
benchmark_return, excess_return_cumulative and peer_percentile.

For historical context, use the precomputed fields rather than inferring:
- performance_periods for prior quarters, year to date and trailing periods.
- performance_trends for consecutive_quarters_underperforming,
  consecutive_quarters_outperforming and trend_direction
  (improving, deteriorating, mixed, insufficient_data).
- ranking_trend for peer percentile across periods.

Statements about improvement, deterioration or trend change are supported when
they come from these fields. Use them; do not invent trend claims from a single
quarter.

An underperformed_* flag of false means either that the manager beat the
benchmark or that the period had no data. Check performance_trends.
periods_evaluated before making any claim covering multiple periods. If a period
is not listed there, do not assert anything about it.

MARKET CONTEXT

Use market_data and summaries in the asset-class JSON as the primary source for
factor, sector and industry leadership. These are index-level market movements,
not manager attribution, and must be worded as market context.

Use market_data_periods for multi-period market context and market_trends for
headline movers.

Before pairing a market period with a manager period, check
aligned_with_selected_quarter_end on that market period. When it is false the
market window ends on a different date than the manager figures, and the note
field explains how. Do not present the two as covering the same period.

An empty or short factor, sector or industry list means that breakdown is not
available in the export for that asset class. It does not mean nothing happened.
Do not draw conclusions from an absent list.

Use web research only to explain movements already visible in the data, and only
when the data alone does not support the explanation. Never introduce a market
theme that the data does not evidence.

ATTRIBUTION

Check attribution_available on the manager record first.

When attribution_available is true, open attribution_path and use that file as
the primary source for all attribution commentary.

Use attribution_summary.primary_driver to state whether the result was
allocation-driven, selection-driven, interaction-driven or mixed. When
driver_confidence is low or primary_driver is mixed, say the result reflected
both allocation and selection rather than naming a single driver.

For sector commentary use sector_rankings, which is already sorted:
- top_detracting_sectors for sectors that hurt.
- top_contributing_sectors for sectors that helped.
- strongest_sector_selection, weakest_sector_selection,
  strongest_sector_allocation and weakest_sector_allocation for the specific
  allocation-versus-selection story.

Do not use effect_rank to decide whether a sector helped or hurt. effect_rank is
ordered by absolute size, so rank 1 may be either the largest contributor or the
largest detractor. Determine direction from the sign of total_effect_bps or by
using the sector_rankings lists.

Use sector_attribution for the supporting detail on a named sector: position
(overweight, underweight, neutral, not_held), portfolio and benchmark weights,
returns, and the split between allocation_effect_bps and selection_effect_bps.

For security commentary use top_contributors and top_detractors.

Before describing any security, in either list, read held and reason.
- held = true: the manager owned it. Describe it as a position.
- held = false: the manager did not own it. Describe the impact as non-ownership.
  Never describe a not-held security as a holding, a position, a purchase or a
  sale.

Not owning a security can help or hurt, so not-held names appear among both
contributors and detractors. Let reason drive the wording:
- overweight_outperformer / overweight_underperformer: owned more than the
  benchmark.
- underweight_outperformer / underweight_underperformer: owned less than the
  benchmark.
- not_held_outperformer: did not own it and it rose; this cost relative return.
- not_held_underperformer: did not own it and it fell; this helped relative
  return.
- held_positive_selection / held_negative_selection: held at roughly benchmark
  weight.
- mixed: direction is not established; describe the effect only, without
  characterising the position.

Use concentration to decide whether to describe results as concentrated or
broad-based. A high top_5_absolute_effect_share supports concentrated; a low
share supports broad-based. Do not assert either without checking.

Use attribution_periods and attribution_trends for whether the pattern is
recurring: consecutive_quarters_selection_negative,
consecutive_quarters_allocation_negative, dominant_driver_trailing_4_quarters
and driver_stability.

Use cash_attribution only when cash_attribution.material is true. Cash is not a
GICS sector and must never be discussed as one; refer to it as a cash position
or cash drag.

Never mention residuals, attribution gaps, coverage, expense ratios, benchmark
reconstruction or reconciliation. Do not construct industry-level attribution
from the attribution data; industry context comes only from the market-data
layer and must be worded as market movement.

WHEN ATTRIBUTION IS NOT AVAILABLE

When attribution_available is false, no attribution data exists for that manager.
This is the normal case for most managers.

In that case:
- Do not state or imply allocation, selection or interaction effects.
- Do not name securities as contributors or detractors.
- Do not estimate attribution from sector market returns.
- Build Context & Analysis from performance, peer ranking, history and market
  context, explaining the environment the strategy faced and how its results
  compare with peers and its own history.
- Do not apologise for, or draw attention to, the absence of attribution data.
  Write the strongest commentary the available data supports.
```

---

## 5. Structural notes on the wider instruction set

Content aside, three things would improve adherence:

**Length and repetition.** Q4 guidance is stated three times in different words
(the Period-Specific section, the writing section, and the closing section), and
"do not fabricate / evidence-based / objective" recurs throughout. Long
instruction sets dilute attention and increase the chance a late rule is missed —
which is precisely why the appended attribution block is badly placed. Consolidate
the Q4 rules into one location and cut the restatements.

**Order by decision, not by document.** The agent makes decisions in this
sequence: find the data → judge significance → select what to discuss → write.
Instructions ordered that way are easier to follow than instructions grouped by
which source document they came from. The current set opens with a document
priority list, which matters less to the agent than knowing where the numbers are.

**Two lines to delete or edit** (see §2): "Analyze attribution using the
attribution workbook" and "Use the Sector, Industry, and Factor file as the
primary source". Both now point at artefacts the agent never sees.

---

## 6. One thing worth verifying by hand

The instructions ask for "top 3 detracting sectors and top 2 contributing
sectors" for underperformers. `sector_rankings` caps each list at five and only
includes sectors whose effect is genuinely negative or positive — so for a fund
with only two detracting sectors, the list is legitimately short. The agent
should discuss what is there rather than padding to three. Worth adding a line if
you see it inventing a third.
