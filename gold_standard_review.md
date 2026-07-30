# Review — Actual Battle Book Examples vs Instructions and Data Layer

Read: T. Rowe Price Blue Chip Growth 1Q26, T. Rowe Price Growth Stock 1Q26.

These change four things in the instructions and expose three genuine data gaps.
**Do not apply the knowledge-document edits yet** — two of them need revising
first.

---

## 1. What the examples validate

The data layer supports the style closely. The Growth Stock Call to Action is
almost a field-by-field read of `performance_periods` and `ranking_trend`:

> "...trailing the benchmark by -148 bps and ranking in the 83rd percentile...
> lags the benchmark over the 1-year (-538 bps, 68th pct), 3-year (-269 bps, 60th
> pct), 5-year (-652 bps, 83rd pct), and 10-year (-341 bps, 70th pct) periods"

That maps exactly onto `excess_return_cumulative` + `peer_percentile` across
`trailing_1_year` / `trailing_3_year` / `trailing_5_year` / `trailing_10_year`.

Equally well supported:

| Gold-standard phrasing | Field |
| --- | --- |
| "did not own GE Vernova (+33.74%, -16 bps)" | `held: false`, `reason: not_held_outperformer`, `benchmark_return`, `total_effect_bps` |
| "Consumer Staples (-46 bps, entirely an allocation effect from a 0% weight in a sector that rallied)" | `position: not_held`, `driver: allocation`, `allocation_effect_bps` |
| "GE Aerospace, an overweight position, returned -7.74% and cost -21 bps" | `position: overweight`, `portfolio_return`, `total_effect_bps` |
| "The shortfall was overwhelmingly stock selection" | `primary_driver: selection`, `driver_confidence: high` |

The `held` / `reason` work was the right call — the examples lean on the
owned-versus-not-owned distinction constantly.

---

## 2. Four instruction changes required

### 2.1 The examples end with a Recommendation section

Both do:

> **Recommendation:** ...We remain convicted in the investment strategy and team.
> No change in rating.

> **Recommendation:** ...T. Rowe Price Growth Stock will remain on Watch status.

The instructions say "Never write a recommendation section", and Output Standards
and the Style Guide both repeat it. I read that as deliberate — the Recommendation
carries rating and Watch decisions that are yours and the committee's, not the
agent's. **But the gold standards are a knowledge source**, so the agent will see
that structure and imitate it. A prohibition without an explanation is weak
against a visible example.

Replace the current wording with an explicit instruction:

```
The gold-standard battle books end with a Recommendation section containing
rating and Watch decisions. That section is written by the analyst, not by you.
Do not reproduce it, do not imitate it, and do not state or imply a rating,
Watch status, conviction or approval decision anywhere in the commentary. End
after Context & Analysis.
```

Confirm this is right. If you actually want the agent to draft a Recommendation,
the instructions and two documents all need reversing.

### 2.2 The section labels are inline, not headings

The real format is a run-in label:

```
Call to Action: T. Rowe Price Blue Chip Growth returned -11.24%...

Context & Analysis: The start of 2026 saw a rotation into value...
```

Title case, colon, text continuing on the same line. My instruction block says to
use two headings in capitals, which would produce the wrong shape. Corrected:

```
Open with "Call to Action:" followed by the text on the same line. Begin the
analysis with "Context & Analysis:" followed by the text on the same line. Use
title case with a colon, not capitalised headings, and create no other labels or
sub-sections.
```

### 2.3 Length is 6-7 paragraphs, not 3-5

| Example | Call to Action | Context & Analysis |
| --- | --- | --- |
| Blue Chip Growth | 1 | 6 |
| Growth Stock | 1 | 7 |

Output Standards says "approximately one page"; both examples run closer to two
pages of body text. The "1 + 3-5" figure I was about to add to Output Standards
is wrong — it would systematically produce short commentary. Use:

```
Length:
Approximately one to two pages
Generally one Call to Action paragraph and five to seven Context & Analysis
paragraphs
```

### 2.4 A "what worked" paragraph is a consistent element

Both examples devote a late paragraph specifically to contributors:

> "What worked in the quarter was selection within Financials and Communication
> Services..."

> "What worked was concentrated in semiconductor manufacturing and a handful of
> avoidances."

No document mentions this. Worth adding to the Playbook's Part C/D guidance —
after the detracting sectors are covered, a dedicated contributors paragraph
before any read-across or closing.

---

## 3. Three data gaps the examples expose

### 3.1 Calendar-year performance — required, and missing

Both Calls to Action lean on calendar years:

> "...missed by -291 bps in 2025 and -376 bps in 2024 (after a strong +260 bps
> calendar 2023)"

> "...a year in 2025 that the strategy outperformed the benchmark by 22bps and
> ranked in the 27th percentile... the third straight calendar year of
> outperformance"

`performance_periods` has none of it. But the workbooks carry **eleven
calendar-year blocks (2015-2025)** which the parser already detects and then
discards:

```
2025  2025-01-01 -> 2025-12-31  cols DN:DU
2024  2024-01-01 -> 2024-12-31  cols DV:EC
2023  2023-01-01 -> 2023-12-31  cols ED:EK
...
```

This is the single most valuable addition available, and it is small: add
calendar years as logical periods, plus a `consecutive_calendar_years_*` streak
to support "third straight calendar year of outperformance". Roughly 30 lines in
Phase 1, no schema break — a new `calendar_year_periods` object alongside
`performance_periods`.

Without it, the agent cannot write the Call to Action in your house style.

### 3.2 Calendar-year sector attribution — harder

The Growth Stock example has a read-across paragraph:

> "Consumer Discretionary selection cost the strategy -118 bps in 2025,
> Information Technology selection cost -78 bps, and Healthcare another -36 bps."

That is **sector-level attribution for calendar 2025**. The attribution export's
bands are the cumulative window plus individual quarters — there is no
calendar-year band, and `attribution_periods` stores only totals per quarter, not
a sector breakdown. Summing four quarters of effects is also not strictly valid.

Three options, in order of preference:

1. **Add a calendar-year window to the Morningstar attribution export** (a second
   saved view, or change Display Frequency to Yearly). Cleanest and correct.
2. Store per-quarter sector effects and let the agent describe individual
   quarters rather than the calendar year.
3. Leave it out and accept that read-across paragraphs stay manual.

This matters most for Q4, where Period-Specific Rules require annual attribution.

### 3.3 Two smaller gaps

**Active weight in basis points.** The Blue Chip example writes "the portfolio's
362bp overweight to Carvana". `active_weight` is stored in percentage points
(3.62) with no `_bps` twin, while the instructions say not to convert percentages
yourself. Either add `active_weight_bps` to sector and security records, or
exempt weights from that rule.

**Manager tenure and history.** The Growth Stock Call to Action references a PM
departure date and a co-PM start date. The source workbooks carry
`Manager History`, `Manager Tenure (Longest)`, `Longest Tenured Manager Name` and
`Longest Tenured Manager Start Date`, none of which are extracted. Cheap to add.

**Watch status and ratings are in no data source at all.** They are internal. The
agent cannot produce that sentence and should not try — supply it in the prompt,
or add it by hand.

---

## 4. Two conflicts to resolve before the document edits

### 4.1 Em dashes

The Playbook lists under Avoid: *"Common AI output, such as the em dash."*

The Growth Stock example uses them throughout:

> "defensive and value factors led — Yield (+5.74%), Value (+4.35%)..."
> "the damage was concentrated in software — Application Software (-25.86%)..."
> "extends a period of softer relative performance — the strategy has lagged..."

The Blue Chip example uses none. Since gold standards govern style and the
Playbook governs the ban, the agent gets contradictory guidance. My
recommendation is to keep the ban and treat Growth Stock as non-exemplary on this
point — but that needs to be your call, and whichever way it goes, the two
sources should agree.

### 4.2 The two examples are written in different voices

| | Blue Chip Growth | Growth Stock |
| --- | --- | --- |
| Numbers | moderate, in prose | dense, bracketed `(+33.74%, -16 bps)` |
| Em dashes | none | frequent |
| Structure | narrative | labelled segments, "Calendar 2025 read-across." |
| Trailing-period detail in CTA | 2025 only | 1, 3, 5, 10-year with percentiles |

Same author, eight days apart. These would calibrate the agent very differently:
the Growth Stock style is more systematic and more data-dense, the Blue Chip
style more conversational.

Worth asking directly: **was the Growth Stock note drafted with AI assistance?**
Its density, bracketing convention and em-dash usage are the markers the Playbook
warns about. If it was, using it as a gold standard risks entrenching the style
the Playbook is trying to avoid. If it was not, it is the better calibration
target and the em-dash ban should probably go.

### 4.3 Sector count

Commentary Construction Rules says "Top 3 detracting sectors". Both examples list
**five**, with basis points for each. `sector_rankings.top_detracting_sectors`
already returns up to five, so the data matches practice rather than the document.
Update the document to five.

---

## 5. Recommended order

1. Answer the two questions in §4.1 and §4.2 — they change what "style" means.
2. Apply the instruction changes in §2 (Recommendation, inline labels, length,
   what-worked paragraph).
3. Add calendar-year performance to Phase 1 (§3.1). Small, and the Call to Action
   depends on it.
4. Decide on calendar-year attribution (§3.2) before the Q4 build, not during it.
5. Then apply the knowledge-document edits, with the §2.3 length correction and
   §4.3 sector count folded in.
