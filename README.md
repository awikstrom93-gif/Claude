# Quarterly Battle Book JSON Builder (V1)

Converts reusable quarterly **Morningstar Direct** Excel exports into clean,
self-contained JSON files that a **Microsoft Copilot Studio** commentary agent
can retrieve reliably.

Copilot Studio cannot parse the raw Morningstar exports — they use wide period
blocks, merged header bands, repeated group sections, and (importantly) *the
three asset-class workbooks do not share the same column layout*. This script
does the extraction once per quarter so the agent only ever reads JSON.

---

## 1. Quick start

```bash
pip install -r requirements.txt

python build_quarterly_json.py --quarter "2026 Q2"
python build_quarterly_json.py --quarter latest
python build_quarterly_json.py --list-quarters
```

> **Building a full quarter?** Use the wrapper instead — it runs this script and
> then the Phase 2 attribution builder in the correct order:
>
> ```bash
> python build_battle_book_data.py --quarter "2026 Q2"
> ```
>
> This matters because the script below rewrites the asset-class JSON files from
> scratch, which removes the attribution pointers Phase 2 adds. Running it on its
> own leaves the commentary agent unable to find attribution data even when the
> attribution files exist. See `README_attribution.md`.

The input and output roots default to the OneDrive paths below and can be
overridden for testing:

```bash
python build_quarterly_json.py --quarter "2026 Q2" ^
  --input-root  "D:\test\in" ^
  --output-root "D:\test\out"
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--quarter` | `latest` | Quarter folder to build (`"2026 Q2"`, `2026Q2`, `Q2 2026`, or `latest`). |
| `--input-root` | `...\Quarterly Battle Book Inputs` | Root holding the quarter folders. |
| `--output-root` | `...\Quarterly Battle Book Data` | Root the JSON is written to. |
| `--list-quarters` | – | Print the quarter folders found, then exit. |
| `--verbose` | – | Debug logging. |

Exit codes: `0` success · `1` ran but produced no asset-class JSON · `2` bad
arguments, missing folder, or unparsable layout.

---

## 2. Folders

**Input**

```
Quarterly Battle Book Inputs\
  2026 Q2\
    US Large Growth.xlsx
    US Mid Growth.xlsx
    US Small Growth.xlsx
    US Growth Sector Industry Factor.xlsx
```

Filename matching is tolerant — `US_Large_Growth.xlsx` resolves just as well as
`US Large Growth.xlsx`. Excel lock files (`~$…`) are ignored.

**Output**

```
Quarterly Battle Book Data\
  2026 Q2\
    large_growth.json
    mid_growth.json
    small_growth.json
    manifest.json
    debug_layout_report.json
```

`--quarter latest` picks the highest `(year, quarter)` folder present, so it is
safe to leave in a scheduled task.

---

## 3. How the agent uses the output

The asset-class files are the **source of truth**. There is no
`manager_index.json` and none is needed:

1. The agent is asked for *"T. Rowe Price Blue Chip Growth, Q2 2026"*.
2. It opens the three asset-class JSONs for `2026 Q2`.
3. It normalises the requested name and checks each file's `manager_lookup`.
4. The file it is found in **is** the asset class — `large_growth.json` means
   Large Growth. No separate mapping to maintain or drift.
5. `manager_lookup[key]` is the **integer index** into that file's `managers`
   array — a direct jump, no scanning.
6. `market_data` and `summaries` for that asset class are already embedded in
   the same file, so one retrieval covers performance *and* market backdrop.
7. `performance_periods`, `performance_trends` and `ranking_trend` on the same
   record carry the history, so trend claims need no extra retrieval either.

This supports observations such as *"has underperformed for four consecutive
quarters"*, *"trails the benchmark over ten years"*, *"underperformed in Q2 but
remains ahead YTD"* and *"recent weakness looks short-term rather than
long-term"* — all from one file.

```json
"manager_lookup": {
  "fidelity growth company k6": 17,
  "t rowe price blue chip growth": 19
}
```

Lookup keys are normalised by lowercasing, trimming, collapsing duplicate
spaces, and removing periods (**for lookup only** — the stored `manager` value
keeps its original punctuation).

Tickers and strategy names are added as **secondary** lookup keys when they do
not collide with a manager-name key, so `trbcx` also resolves. Set
`INCLUDE_TICKER_AND_STRATEGY_IN_LOOKUP = False` at the top of the script to
turn that off.

---

## 4. Asset-class JSON shape

```jsonc
{
  "asset_class": "US Large Growth",
  "asset_class_key": "large_growth",
  "period": "2026 Q2",
  "period_start_date": "2026-04-01",
  "period_end_date": "2026-06-30",
  "default_benchmark": "Russell 1000 Growth TR USD",
  "lineage": {
    "source_file": "US Large Growth.xlsx",
    "worksheet": "Sheet1",
    "period_header": "Last Quarter",
    "generated_at": "2026-07-30T16:41:45+00:00"
  },
  "manager_count": 27,
  "managers": [ /* see below */ ],
  "manager_lookup": { "t rowe price blue chip growth": 19 },
  "reference_indexes": [ /* e.g. S&P MidCap 400 Growth vs the primary index */ ],
  "peer_group_stats":  { "median": { "return_cumulative": 24.76 } },

  // selected-quarter aliases - unchanged from V1
  "market_data": { "factors": [], "sectors": [], "industries": [] },
  "summaries": {
    "top_10_factors": [], "bottom_10_factors": [],
    "top_10_sectors": [], "bottom_10_sectors": [],
    "top_10_industries": [], "bottom_10_industries": []
  },

  // multi-period market context
  "market_data_periods": { "selected_quarter": {}, "ytd": {}, "trailing_1_year": {} },
  "market_trends": { "best_sector_selected_quarter": "Technology" },
  "warnings": []
}
```

### Manager record

```jsonc
{
  "manager": "T. Rowe Price Blue Chip Growth",
  "strategy_name": "T. Rowe Price US Large Cap Core Growth Equity",
  "asset_class": "US Large Growth",
  "benchmark": "Russell 1000 Growth TR USD",
  "calculation_benchmark": "Russell 1000 Growth TR USD",
  "return_cumulative": 13.2733,
  "benchmark_return": 16.7412,
  "benchmark_return_calculated": false,
  "peer_percentile": 83,
  "excess_return_cumulative": -3.4679,
  "source_file": "US Large Growth.xlsx",

  // historical context - see section 6
  "performance_periods": { "selected_quarter": {}, "ytd": {}, "…": {} },
  "performance_trends":  { "consecutive_quarters_underperforming": 2 },
  "ranking_trend":       { "selected_quarter": 83, "trailing_10_year": 50 },

  // additive context - safe to ignore
  "ticker": "TRBCX",
  "portfolio_managers": ["Paul D. Greene"],
  "source_row": 33,
  "source_section": "US Large Growth",
  "benchmark_source": "calculation_benchmark_column"
}
```

Returns are percentages as Morningstar reports them (`13.2733` = 13.27%),
rounded to 4 decimals. Missing values are `null`, never `0`.

### Benchmark logic

1. Use the row's **Calculation Benchmark** column when the workbook has one.
2. Otherwise use the `Benchmark 1:` row of the manager's group section — this
   is what US Small Growth needs, as that export has no benchmark column.
3. Otherwise fall back to the asset-class default constant (and warn).

`benchmark_return` is taken from the matching benchmark row when one exists.
When it does not, it is derived as `return_cumulative − excess_return_cumulative`
and `benchmark_return_calculated` is set to `true`. `benchmark_source` records
which of the three rules applied.

---

## 5. Market data

Extracted from `US Growth Sector Industry Factor.xlsx` and embedded into each
asset-class file, so the agent never has to open a second document.

| Asset class | Factors | Sectors | Industries |
| --- | --- | --- | --- |
| Large Growth | Large Cap Factors | **Russell 1000 Growth Sectors** | S&P 500 Industry Group |
| Mid Growth | S&P MidCap 400 Factors | **Russell Midcap Growth Sectors** | S&P MidCap 400 Sub-Industries |
| Small Growth | Small Cap Factors | **Russell 2000 Growth Sectors** | `Sub/…` rows in the small-cap sections |

Preference order lives in `MARKET_SECTION_PREFERENCES`; if a preferred section
is missing, the next one is used and a warning is recorded.

Two honest limitations of the current export, both surfaced as warnings rather
than hidden:

* There is **no mid-cap-growth or small-cap-growth factor block** — the
  S&P MidCap 400 and small-cap factor blocks are the closest available proxies.
* There is **no dedicated small-cap industry section**. Only the handful of
  `Sub/…` series inside the small-cap sector sections are available, so
  `top_10_industries` for Small Growth is short by design.

Each market row carries both the raw Morningstar name and a `display_name`
cleaned for prose — `S&P MidCap 400 Sub/Electl Compnts&Eq PR` becomes
`Electrical Components & Equipment`.

---

## 6. Historical context (`performance_periods`)

The selected quarter remains the primary period — every top-level field still
describes it, unchanged. Alongside it, each manager carries nine periods that
already exist in the workbook:

| Key | Workbook block (Q2 2026 build) | Window | Basis |
| --- | --- | --- | --- |
| `selected_quarter` | Last Quarter | 2026-04-01 → 06-30 | cumulative |
| `ytd` | YTD thru Last Q End | 2026-01-01 → 06-30 | cumulative |
| `two_quarters_ago` | 2 Quarters Ago | 2026-01-01 → 03-31 | cumulative |
| `three_quarters_ago` | 3 Quarters Ago | 2025-10-01 → 12-31 | cumulative |
| `four_quarters_ago` | 4 Quarters Ago | 2025-07-01 → 09-30 | cumulative |
| `trailing_1_year` | 1 YEAR | 2025-07-01 → 2026-06-30 | cumulative |
| `trailing_3_year` | 3 YEARS | 2023-07-01 → 2026-06-30 | **annualized** |
| `trailing_5_year` | 5 YEARS | 2021-07-01 → 2026-06-30 | **annualized** |
| `trailing_10_year` | 10 YEARS | 2016-07-01 → 2026-06-30 | **annualized** |

```jsonc
"trailing_3_year": {
  "label": "3 YEARS",
  "period_start_date": "2023-07-01",
  "period_end_date": "2026-06-30",
  "basis": "annualized",
  "return_cumulative": 21.4637,
  "benchmark_return": 22.5755,
  "benchmark_return_calculated": false,
  "peer_percentile": 52,
  "excess_return_cumulative": -1.1119,
  "available": true
}
```

> **`basis` matters.** Morningstar reports the 3/5/10-year blocks
> **annualized**, not cumulative. `21.4637` on `trailing_3_year` means 21.46%
> *per year*, not 21.46% over three years. The field name
> `return_cumulative` is kept for schema compatibility, so commentary must read
> `basis` before describing a trailing figure. The value is detected from the
> column's own header text (`Return (Annualized)`), not assumed.

**Naming.** `two_quarters_ago` follows Morningstar's own labelling, which counts
back from the *current* quarter. Because the selected quarter is normally
"Last Quarter", `two_quarters_ago` holds the quarter **immediately before** the
selected quarter. Every record carries explicit start/end dates so this never
has to be inferred.

**Resolution.** Periods are resolved against the selected quarter **by date**
(`two_quarters_ago` = selected quarter minus one calendar quarter), falling back
to label text only when no date match exists. This keeps the chain correct even
when the selected quarter is not "Last Quarter" — building a partial Q3 2026
correctly maps `two_quarters_ago` to the *Last Quarter* block. A missing period
is `null` with `available: false`, never zero.

### `performance_trends`

```jsonc
"performance_trends": {
  "consecutive_quarters_underperforming": 2,
  "consecutive_quarters_outperforming": 0,
  "underperformed_selected_quarter": true,
  "underperformed_ytd": true,
  "underperformed_trailing_1_year": true,
  "underperformed_trailing_3_year": true,
  "underperformed_trailing_5_year": true,
  "underperformed_trailing_10_year": true,
  "trend_direction": "deteriorating",
  "periods_evaluated": ["selected_quarter", "ytd", "…"],
  "quarters_available": 4
}
```

**Streaks** walk the quarter chain newest → oldest
(`selected_quarter` → `two_quarters_ago` → `three_quarters_ago` →
`four_quarters_ago`) and stop at the first quarter that breaks the run or has no
data. Underperforming is `excess_return_cumulative < 0`, outperforming is `> 0`;
an excess of exactly `0` breaks both. Both streaks can never be non-zero at once.

**`underperformed_*`** is `true` only when excess return is present and negative.
It is `false` both when the manager beat the benchmark **and** when the period is
missing — so `periods_evaluated` lists which periods actually had data. Check it
before writing "has not underperformed over any period".

### `trend_direction` logic

Evaluated on excess return across the four-quarter chain, oldest → newest:

1. Fewer than **3** quarters with excess-return data → `insufficient_data`.
2. Compute the differences between consecutive quarters.
   * Every difference positive → `improving`.
   * Every difference negative → `deteriorating`.
3. Otherwise compare the mean excess of the most recent half against the
   earliest half:
   * recent − earlier > **+0.5 pp** → `improving`
   * recent − earlier < **−0.5 pp** → `deteriorating`
   * within ±0.5 pp → `mixed`

Step 2 catches clean monotonic runs; step 3 gives a defensible read on choppy
ones instead of collapsing everything to `mixed`. The threshold is
`TREND_DIRECTION_THRESHOLD` and the minimum quarter count is
`TREND_MIN_QUARTERS`.

Worked example — T. Rowe Price Blue Chip Growth, Q2 2026. Excess by quarter
(oldest → newest): `−2.54, +0.93, −1.46, −3.47`. Differences are
`+3.47, −2.39, −2.01` — mixed signs, so step 3 applies: earlier mean `−0.80`,
recent mean `−2.46`, shift `−1.66` → **deteriorating**.

### `ranking_trend`

Peer percentile across the six headline periods, so percentile drift is a
single lookup:

```jsonc
"ranking_trend": {
  "selected_quarter": 83, "ytd": 89, "trailing_1_year": 74,
  "trailing_3_year": 52, "trailing_5_year": 70, "trailing_10_year": 50
}
```

Morningstar percentiles run 1 (best) to 100 (worst) — a *falling* number is an
*improving* rank.

---

## 7. Multi-period market data

`market_data_periods` holds `selected_quarter`, `ytd` and `trailing_1_year`,
each with its own `factors`, `sectors`, `industries` and `summaries`.
`market_trends` pre-computes the headline movers:

```jsonc
"market_trends": {
  "best_sector_selected_quarter":  "Technology",
  "worst_sector_selected_quarter": "Energy",
  "best_sector_ytd":               "Energy",
  "worst_sector_ytd":              "Consumer Discretionary",
  "best_factor_selected_quarter":  "S&P 500 Momentum",
  "worst_factor_selected_quarter": "S&P 500 Low Volatility",
  "best_industry_selected_quarter":  "Semiconductors & Equipment",
  "worst_industry_selected_quarter": "Telecom Services",
  "best_sector_trailing_1_year":   "Energy",
  "worst_sector_trailing_1_year":  "Financials"
}
```

> **Market YTD is not the same window as manager YTD.** The market workbook has
> no "YTD thru Last Q End" block — only "YTD", which runs to the **export date**
> (2026-07-24), while manager YTD ends at quarter end (2026-06-30). Each market
> period therefore carries `aligned_with_selected_quarter_end` and, when false, a
> `note` spelling out the mismatch. Do not present the two as covering an
> identical window. If you need an aligned market YTD, add a
> "YTD thru Last Q End" column set to the Morningstar export.

**Backward compatibility:** top-level `market_data` and `summaries` remain and
are exact aliases of `market_data_periods.selected_quarter`. Nothing that read
V1 output needs to change.

---

## 8. Why period selection is date-based

Row 7 of each export labels period blocks (`MTD`, `Last Quarter`,
`Current Quarter`, …) and rows 8/9 hold each block's start and end dates. Which
*label* holds the completed quarter depends on when the export was run, so the
script matches the block whose **start and end dates equal the requested
quarter** instead of trusting the label text.

For a 2026 Q2 build from a 27 Jul 2026 export, that resolves to the
`Last Quarter` block (1 Apr – 30 Jun 2026) — recorded in
`lineage.period_header` for auditability.

The same date-first rule drives the whole history chain, which is what keeps it
correct when the selected quarter is *not* "Last Quarter". Building a partial
Q3 2026 from the same export maps `selected_quarter` → `Current Quarter` and
`two_quarters_ago` → `Last Quarter`; a label-based mapping would have silently
returned the wrong quarters.

If only a partial block matches (you built the quarter you are still in), the
script uses it, marks it quarter-to-date, and warns loudly.

---

## 9. Handling the layout differences

Nothing is addressed by fixed cell reference. Header rows, identity columns,
period blocks and metric columns are all discovered at run time — which is what
makes one parser work across three genuinely different exports:

| | US Large / US Mid Growth | US Small Growth |
| --- | --- | --- |
| Period block width | 3 columns | 4 columns |
| `Calculation Benchmark` column | Yes (column B) | **Absent** |
| Group sections | One | Two (`Recommended`, `US Small Growth`) |
| Peer group statistic rows | None | Five per section |
| Q2 2026 return column | `AC` | `AI` |

Row classification distinguishes manager rows from section headers,
`Benchmark N:` rows, `Peer Group …` statistic rows, and bare index rows such as
`Russell 1000 Growth TR USD`. Index rows go to `reference_indexes` and peer
statistics to `peer_group_stats` rather than being silently dropped.

Duplicate managers across group sections keep the first occurrence and warn.
Managers with no return for the period are kept with `null` values and warn —
they are never silently deleted.

---

## 10. debug_layout_report.json

Written every run. Use it whenever a build looks wrong before touching the
script:

* worksheets found in each workbook, and which one was parsed
* detected period blocks — label, column range, start/end date, basis, metric
  columns
* which period block was selected, and its metric column letters
* `logical_period_map_summary` — the logical-period → workbook-block mapping in
  one glance:

  ```
  selected_quarter   -> Last Quarter
  ytd                -> YTD thru Last Q End
  two_quarters_ago   -> 2 Quarters Ago
  three_quarters_ago -> 3 Quarters Ago
  four_quarters_ago  -> 4 Quarters Ago
  trailing_1_year    -> 1 YEAR
  trailing_3_year    -> 3 YEARS
  trailing_5_year    -> 5 YEARS
  trailing_10_year   -> 10 YEARS
  ```

* `logical_period_map` — the same mapping in full, with each period's column
  range, resolved dates, basis, the dates that were *targeted*, and
  `resolved_by` (`selected_quarter_block`, `matched_by_date`,
  `matched_by_label`, or `not_found`)
* detected manager header row and identity column letters
* detected market section ranges with row counts and categories
* row counts: managers parsed, reference indexes, benchmark rows, peer stat
  rows, section headers, blanks
* every warning, and every skipped row with the reason it was skipped

---

## 11. Scope of V1

Deliberately **not** in V1:

* Manager-specific attribution workbooks are not parsed.
* No `manager_index.json` is produced.

Attribution has a prepared extension point (Section 10 of the script):
implement `parse_manager_attribution()` to return
`{normalised_manager_name: {...}}` and call `attach_attribution()` from
`build_quarter()`. The payload merges onto the existing manager records in
place, so `manager_lookup` indexes stay valid and nothing else changes.

---

## 12. Adapting to a changed export

Section 1 of `build_quarterly_json.py` holds every layout assumption — file
names, header aliases, metric aliases, market section preferences, name
expansions, summary size, rounding. If Morningstar changes a template, edit
that section only. Run with `--verbose` and read
`debug_layout_report.json` first to see what the script actually detected.
