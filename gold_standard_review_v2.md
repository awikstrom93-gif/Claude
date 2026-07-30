# Gold-Standard Review v2 — Three Human-Written Q4 Battle Books

Read: JP Morgan Large Cap Growth 4Q25, Loomis Sayles Small Cap Growth 4Q25,
Geneva Small Cap Growth 4Q25. All authored by you, all Q4 — the period I had the
least evidence for.

This supersedes the length and structure findings in the previous review.

---

## 1. Questions resolved

**Em dashes: keep the ban.** None of your four hand-written notes uses one. The
Growth Stock note used them throughout. The Playbook is right and the gold
standards agree with it once the AI-written example is set aside.

**Remove T. Rowe Price Growth Stock from the gold-standard knowledge sources.**
It is AI output, and as a calibration target it would teach the agent the exact
habits the Playbook bans — em dashes, dense bracketed figures, labelled segments
like "Calendar 2025 read-across." Keeping it in the knowledge base means the
agent is being calibrated partly on its own predecessor's output. Blue Chip
Growth plus these three are the real target.

---

## 2. The most important structural finding

**Loomis Sayles and Geneva share a word-for-word identical market backdrop** —
both opening paragraphs and both "Wrapping up 2025" paragraphs are verbatim
matches. Same asset class, same quarter, different managers.

The market backdrop is written **once per asset class per quarter and reused**.
That is a feature, not duplication to be varied away. It confirms the decision to
embed `market_data` at asset-class level, and it means:

```
The market backdrop describes the asset class and quarter, not the manager. Two
managers in the same asset class and quarter should receive the same market
backdrop. Do not vary the market narrative to suit a particular manager's
results.
```

Worth adding to the instructions — otherwise the agent will reword it each time
and consultants comparing two notes will see a discrepancy where there should be
none.

---

## 3. Q4 structure, from the three examples

```
Call to Action:        quarter return vs benchmark, percentile, then the calendar
                       year (return, excess in bps, percentile)
Context & Analysis:    quarter market backdrop
                       "Wrapping up <year>," annual market backdrop
"4th Quarter" /        quarter attribution: allocation vs selection totals in bps,
"In Q4,"               then material detracting sectors with bps
                       3-4 sector deep-dive paragraphs
                       what worked (contributors)
"<year>" /             annual attribution: allocation vs selection, sector
"Looking at the        detractors with bps
year as a whole,"      2-3 annual sector deep-dive paragraphs
Recommendation:        written by you, not the agent
```

**Length.** My previous estimate was badly low:

| Note | Period | Context & Analysis paragraphs |
| --- | --- | --- |
| Blue Chip Growth | Q1 | 6 |
| JP Morgan LCG | Q4 | 9 |
| Loomis Sayles SCG | Q4 | 10 |
| Geneva SCG | Q4 | 11 |

So roughly **5-7 for Q1-Q3 and 9-12 for Q4**, not the "3-5" I was about to write
into Output Standards. Output Standards' "approximately one page" is wrong for
Q4 — these run two to three pages of body text. Period-Specific Rules already
allows the extra length; Output Standards just needs to stop contradicting it.

**Q4 uses inline segment labels.** Two of three introduce the split with a short
label on its own line — `4th Quarter` and `2025` (JPM adds colons). Loomis uses
prose transitions instead. My instruction to create no sub-labels is wrong for
Q4; both forms are acceptable and the agent should use one.

**Sector coverage matches what you described.** Named detractors run three to
five; detailed paragraphs cover the top three, occasionally four:

| Note | Sectors named | Sectors detailed |
| --- | --- | --- |
| Geneva | 4 (HC, Industrials, Cons Disc, Real Estate) | 3 detractors + IT as contributor |
| JPM | 4 (Comm Svcs, Cons Disc, HC, IT) | 4 |
| Loomis | 4 (HC, Cons Disc, Real Estate, Financials) | 2 detractors + Industrials as contributor |

Encode it as: *name the material detracting sectors, typically three to five;
give a detailed paragraph to the top three, and to a fourth only where it carries
the story.* Commentary Construction Rules' flat "Top 3" should be updated.

---

## 4. Data gaps, in priority order

### P0 — Q4 commentary is impossible without these

**4.1 Calendar-year performance.** Confirmed again by all three Calls to Action:

> "The strategy closed a difficult year in 2025 with strong absolute returns of
> +14.40% but trailed the benchmark and most peers... trailed their benchmark by
> -416bps and ranked 66th against peers."

Return, excess in bps, and percentile for the calendar year. The parser already
detects eleven calendar-year blocks and discards them. ~30 lines in Phase 1.

**4.2 Calendar-year attribution — and there is an elegant fix.**

All three notes carry full annual attribution with sector-level basis points:

> "Three sectors were the primary detractors in Industrials (-653bps), Health Care
> (-600bps), and IT (-586bps)."

> "detraction mainly came from allocation with -247bps detracted as well as -95bps
> detracted from selection."

The attribution workbook's `Template` currently sets **Start Date: 01-01-2025**
with End Date "Last Quarter End", so its cumulative band spans 1/1/2025-6/30/2026
— eighteen months, useless.

**Set the export Start Date to 1 January of the reporting year.** Then the
cumulative band becomes exactly year-to-date, and for a Q4 export it becomes
exactly the calendar year. One settings change, no code, and it solves both the
YTD attribution gap and the Q4 annual attribution gap at once. The parser would
then map that band to a `ytd` / `calendar_year` logical period alongside the four
quarters.

This is the single highest-leverage change available. Make it before the next
quarterly pull.

### P1 — materially degrades the output

**4.3 Sector-level contribution to return.** The market backdrop leans on it
constantly:

> "Health Care was up +18.13%, contributing +4% of the +1.22% returned by the
> benchmark."

> "Comm Services and Health Care contributed +0.59% and +1.07% of the 1.12%
> returned by the index."

The attribution workbook has `Contribution to Return` (Portfolio / Benchmark /
+/-) at sector level, and the parser already maps those columns — but
`sector_attribution` only emits weights, returns and effects. Security records do
carry them. Adding the three fields at sector level is a few lines.

Caveat: this only helps where attribution exists. For the 76 managers without it,
index sector contribution would need adding to the market export as a data point.

**4.4 `reference_indexes` and index percentiles are used in every Q4 note.**

> "Russell 2000 Growth returned +13.01% ranking in the 23rd percentile. In
> contrast, the S&P SmallCap 600 Growth returned +5.37% ranking in the 67th
> percentile."

> "Russell 1000 Equal Weighted returned +9.93% and ranked in the 92nd percentile
> and the Russell 1000 ex Mag 7 returned +14.90% and ranked in the 42nd."

Both are already in the data — `reference_indexes` for the alternate benchmarks,
and `market_data.factors[].peer_percentile` for the equal-weighted and ex-Mag-7
series. I left both out of the instructions to save characters. That was a
mistake: this benchmark-versus-benchmark framing appears in all three notes and
is how you explain *why* the environment was hard for active managers. Must go
back in.

**4.5 Small and mid-cap industry data is too thin.** Geneva and Loomis discuss
Biotech, Pharmaceuticals, Health Care Equipment, Health Care Providers,
Specialized REITs and Real Estate Management & Development — all within the
Russell 2000 Growth. Small Growth currently has **two** industry entries. The
export has no small-cap or mid-cap industry section. This needs adding to the
Morningstar market export; no parser change will conjure it.

### P2 — polish

**4.6 "Out of benchmark holding"** appears throughout your notes and is a
distinct concept from overweight: `held: true` with `benchmark_weight: 0`. Both
fields exist; the agent just needs telling to use that phrase.

**4.7 Active weight in basis points — downgrade.** Your hand-written notes quote
weights as percentages ("a +1.34% weighting", "1.52% active weight", "-4.59%
underweight"). Only the AI-written note used "362bp overweight". No
`active_weight_bps` field needed; instruct the agent to quote weights as
percentages and effects as basis points.

**4.8 Tables.** The JPM note includes a Mag 7 weight/return/contribution table,
while Output Standards says "paragraphs only". One example in five, and it is
supplementary data rather than commentary. I would keep the no-tables rule for
the agent and let you add such a table by hand.

---

## 5. Revised recommendations

**Do first, no code:**

1. Remove the Growth Stock note from the gold-standard knowledge sources.
2. Change the attribution export Start Date to 1 January of the reporting year.
3. Add small-cap and mid-cap industry sections to the market export.

**Instruction changes:**

4. Market backdrop is per asset class and quarter, and should be identical across
   managers in the same asset class.
5. Use `reference_indexes` and factor percentiles for benchmark-versus-benchmark
   context.
6. Q4 length and segment labels; Q1-Q3 five to seven paragraphs, Q4 nine to twelve.
7. Name three to five detracting sectors, detail the top three.
8. "Out of benchmark holding" phrasing; weights in percentages, effects in bps.

**Code changes, in order:**

9. Calendar-year periods in Phase 1 (P0).
10. Map the cumulative attribution band to a YTD / calendar-year period once the
    export date is fixed (P0 for Q4).
11. Sector-level contribution fields in Phase 2 (P1).

**Document edits** — hold until 5-8 are settled, then apply with the length
figures from §3 and the sector count from §3 rather than the numbers in my
earlier review.
