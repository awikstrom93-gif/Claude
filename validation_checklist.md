# Validation Checklist — Quarterly Battle Book JSON

Run this after every quarterly build, **before** pointing the Copilot Studio
commentary agent at the new folder. It should take about ten minutes.

Quarter built: `____________`  Run by: `____________`  Date: `____________`

---

## 1. The run itself

- [ ] Command completed with **exit code 0**
      (`echo %ERRORLEVEL%` on Windows, `echo $?` on bash).
- [ ] Console shows `Parsing ...` for **all four** workbooks.
- [ ] Console shows a `matched period block` line for each workbook, and it
      names the block you expect (normally `Last Quarter`).
- [ ] No `WARNING ... only partially complete` lines — that means you built a
      quarter that has not finished yet.
- [ ] Warning count in the final `Done.` line is understood, not just seen.

## 2. Files present

In `Quarterly Battle Book Data\<quarter>\`:

- [ ] `large_growth.json`
- [ ] `mid_growth.json`
- [ ] `small_growth.json`
- [ ] `manifest.json`
- [ ] `debug_layout_report.json`
- [ ] File timestamps are from today's run, not a previous quarter.

## 3. manifest.json

- [ ] `period` matches the quarter you intended to build.
- [ ] `generated_at` is today.
- [ ] `files_written` lists all three asset-class files.
- [ ] `manager_counts` are non-zero and within about ±3 of last quarter.
      A drop of five or more managers means a section failed to parse —
      investigate before publishing.

Reference counts from the 2026 Q2 exports: large 27 · mid 22 · small 30.

## 4. Period correctness — the highest-risk check

For **each** asset-class file:

- [ ] `period` is the requested quarter.
- [ ] `period_start_date` / `period_end_date` are the true quarter bounds
      (e.g. `2026-04-01` / `2026-06-30`).
- [ ] `lineage.period_header` is the same across all three files.
- [ ] `lineage.source_file` and `worksheet` are populated.

Then confirm against Excel — this is the one check that cannot be skipped:

- [ ] Open the workbook, find the period block whose row 8 / row 9 dates equal
      the quarter, and spot-check **one** manager's return against the JSON.
      Column letters differ per workbook, so use
      `debug_layout_report.json → workbooks.<key>.metric_columns_selected`
      to find the right column rather than assuming.

## 5. Manager records

Pick three managers per file, including one you write about often:

- [ ] `manager` matches column A of the workbook exactly.
- [ ] `strategy_name` is populated (blank is acceptable only where Excel is blank).
- [ ] `asset_class` matches the file (`large_growth.json` → `US Large Growth`).
- [ ] `return_cumulative`, `peer_percentile`, `excess_return_cumulative`
      match the workbook.
- [ ] `peer_percentile` is between 1 and 100.
- [ ] Sign sanity: negative `excess_return_cumulative` pairs with a **higher**
      (worse) percentile than a peer with positive excess.
- [ ] `source_file` names the workbook the record came from.

## 6. Benchmark integrity

- [ ] Every manager has a non-empty `benchmark`.
- [ ] Large Growth managers → `Russell 1000 Growth TR USD`.
- [ ] Mid Growth managers → `Russell Mid Cap Growth TR USD`.
- [ ] Small Growth managers → `Russell 2000 Growth TR USD`.
- [ ] Small Growth `calculation_benchmark` is `""` and `benchmark_source` is
      `section_benchmark_row` — that export has no benchmark column, and this
      is expected, not a defect.
- [ ] No manager has `benchmark_source: "asset_class_default"`. If one does,
      the workbook lost its benchmark row — check the export.
- [ ] **Arithmetic:** for every manager,
      `return_cumulative − excess_return_cumulative ≈ benchmark_return`
      (within 0.02). Run the snippet in §11.
- [ ] All managers with the same benchmark share the same `benchmark_return`.

## 7. manager_lookup

- [ ] Contains one key per manager, at minimum.
- [ ] Keys are lowercase, single-spaced, no periods
      (`"t rowe price blue chip growth"`, not `"T. Rowe Price..."`).
- [ ] Every value is a valid index into `managers` (see §11).
- [ ] A manager you name often resolves to the right record.
- [ ] The same manager does **not** appear in two asset-class files. If one
      does, the Morningstar universes overlap and the agent's asset-class
      inference is ambiguous — resolve at the export.

## 7b. Historical periods (`performance_periods`)

Pick one manager you know well and check all nine periods:

- [ ] All nine keys present: `selected_quarter`, `ytd`, `two_quarters_ago`,
      `three_quarters_ago`, `four_quarters_ago`, `trailing_1_year`,
      `trailing_3_year`, `trailing_5_year`, `trailing_10_year`.
- [ ] `selected_quarter` values are **identical** to the manager's top-level
      `return_cumulative` / `benchmark_return` / `peer_percentile` /
      `excess_return_cumulative`. This is the backward-compatibility guarantee.
- [ ] `period_start_date` / `period_end_date` on each period are the windows you
      expect — in particular `two_quarters_ago` is the quarter **immediately
      before** the selected quarter (Morningstar's own naming counts from the
      current quarter, not the selected one).
- [ ] `basis` is `annualized` on `trailing_3_year`, `trailing_5_year` and
      `trailing_10_year`, and `cumulative` on everything else.
      **If this is wrong the agent will describe annualized returns as total
      returns.** Verify against the Excel header text (`Return (Annualized)`).
- [ ] Arithmetic holds on every period, not just the quarter (see §11).
- [ ] Spot-check two historical periods against Excel using the column letters
      in `debug_layout_report.json → logical_period_map`.

## 7c. Trends and rankings

- [ ] `performance_trends.consecutive_quarters_underperforming` and
      `..._outperforming` are never both non-zero on the same manager.
- [ ] Both streaks are between 0 and 4.
- [ ] Manually verify one streak: read `excess_return_cumulative` for
      `selected_quarter` → `two_quarters_ago` → `three_quarters_ago` →
      `four_quarters_ago` and confirm the count stops where the sign flips.
- [ ] `trend_direction` is one of `improving`, `deteriorating`, `mixed`,
      `insufficient_data`.
- [ ] Managers with no return data show `insufficient_data` and an empty
      `periods_evaluated` — not `improving`.
- [ ] `underperformed_*` flags agree with the sign of each period's excess return.
- [ ] Remember a `false` flag can mean *"beat the benchmark"* **or**
      *"no data"* — `periods_evaluated` distinguishes them. Confirm it is
      populated for the managers you write about.
- [ ] `ranking_trend` percentiles match the `peer_percentile` on the
      corresponding `performance_periods` entries.
- [ ] Sanity: a manager with a strongly negative excess in the selected quarter
      should not show `improving` unless earlier quarters were worse.

## 8. Market data and summaries

For each asset-class file:

- [ ] `market_data.sections_used` names the sections you expect
      (Large → Russell 1000 Growth Sectors, Mid → Russell Midcap Growth
      Sectors, Small → Russell 2000 Growth Sectors).
- [ ] `factors`, `sectors` and `industries` are non-empty — with the two known
      exceptions below.
- [ ] `top_10_*` is sorted **descending**; `bottom_10_*` **ascending**.
- [ ] The best and worst sector are plausible for the quarter and agree with
      what you know happened in the market.
- [ ] `display_name` values read as prose (`Semiconductors & Equipment`),
      not raw index strings.
- [ ] The same sector does not appear in both the top and bottom list —
      that only happens when a category has fewer than 20 members, which is
      normal for 9-sector lists. Confirm it is the expected overlap.

Multi-period market context:

- [ ] `market_data_periods` has `selected_quarter`, `ytd`, `trailing_1_year`.
- [ ] Top-level `market_data` is **identical** to
      `market_data_periods.selected_quarter` factors/sectors/industries, and
      top-level `summaries` is identical to that period's `summaries`
      (the §11 script asserts both).
- [ ] `market_data_periods.ytd.aligned_with_selected_quarter_end` is `false`
      and carries a `note` — the market workbook's YTD runs to the **export
      date**, not quarter end, so it is a different window from manager YTD.
      Confirm the note is present before letting the agent pair the two.
- [ ] `market_trends` names sectors/factors that match the top and bottom of the
      corresponding `summaries` lists.
- [ ] `best_sector_ytd` is derived from that wider market YTD window — do not
      let commentary present it as the same period as manager YTD.

Known and accepted for the current export template:

- [ ] Small Growth `top_10_industries` has only a couple of entries — there is
      no small-cap industry section in the source workbook.
- [ ] Mid Growth factors come from the S&P MidCap 400 block; there is no
      mid-cap *growth* factor block.

## 9. debug_layout_report.json

- [ ] Every workbook key is present with no `error` field.
- [ ] `row_counts.managers_parsed` matches `manifest.manager_counts`.
- [ ] `skipped_rows` reasons are all expected: section header, benchmark row,
      peer group statistic, reference index, blank. Any other reason —
      especially `duplicate manager name` — needs a look.
- [ ] `market_section_ranges` lists roughly 15 sections.
- [ ] Read the `warnings` array end to end and sign off on each one.

## 10. Agent readiness

- [ ] JSON files copied/synced to the folder the agent reads.
- [ ] OneDrive shows sync complete (green check), not "syncing".
- [ ] Ask the agent: *"Create a battle book for T. Rowe Price Blue Chip Growth
      for <quarter>."*
- [ ] It identifies the correct asset class from the file it found the manager in.
- [ ] Fund return, benchmark, excess return and percentile in the draft match
      the JSON exactly.
- [ ] Market backdrop it cites matches `summaries` for that asset class.
- [ ] Repeat for one mid-cap and one small-cap manager — small cap is the file
      most likely to expose a layout regression.

---

## 11. Automated spot-check

Run from the output quarter folder. Prints nothing but `OK` lines when clean.

```python
import json, pathlib

for path in sorted(pathlib.Path(".").glob("*_growth.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    managers = d["managers"]
    problems = []

    # benchmark arithmetic
    for m in managers:
        r, b, e = (m["return_cumulative"], m["benchmark_return"],
                   m["excess_return_cumulative"])
        if None not in (r, b, e) and abs((r - e) - b) > 0.02:
            problems.append(f"benchmark mismatch: {m['manager']}")

    # lookup indexes resolve
    for key, idx in d["manager_lookup"].items():
        if not isinstance(idx, int) or not 0 <= idx < len(managers):
            problems.append(f"bad lookup index: {key} -> {idx}")

    # required fields present
    required = ["manager", "strategy_name", "asset_class", "benchmark",
                "calculation_benchmark", "return_cumulative", "benchmark_return",
                "benchmark_return_calculated", "peer_percentile",
                "excess_return_cumulative", "source_file"]
    for m in managers:
        missing = [f for f in required if f not in m]
        if missing:
            problems.append(f"{m['manager']}: missing {missing}")

    # percentile range
    for m in managers:
        p = m["peer_percentile"]
        if p is not None and not 1 <= p <= 100:
            problems.append(f"{m['manager']}: percentile {p}")

    # summary ordering
    for name, rows in d["summaries"].items():
        vals = [r["return_cumulative"] for r in rows]
        ordered = sorted(vals, reverse=name.startswith("top"))
        if vals != ordered:
            problems.append(f"{name} is not sorted correctly")

    # ---- historical periods -------------------------------------------------
    expected_periods = ["selected_quarter", "ytd", "two_quarters_ago",
                        "three_quarters_ago", "four_quarters_ago",
                        "trailing_1_year", "trailing_3_year",
                        "trailing_5_year", "trailing_10_year"]
    annualized = {"trailing_3_year", "trailing_5_year", "trailing_10_year"}

    for m in managers:
        periods = m["performance_periods"]
        if list(periods) != expected_periods:
            problems.append(f"{m['manager']}: period keys {list(periods)}")

        for key, p in periods.items():
            r, b, e = (p["return_cumulative"], p["benchmark_return"],
                       p["excess_return_cumulative"])
            if None not in (r, b, e) and abs((r - e) - b) > 0.02:
                problems.append(f"{m['manager']}/{key}: benchmark arithmetic")
            if p["available"]:
                want = "annualized" if key in annualized else "cumulative"
                if p["basis"] != want:
                    problems.append(
                        f"{m['manager']}/{key}: basis {p['basis']}, expected {want}")

        # selected_quarter must mirror the top-level fields exactly
        sel = periods["selected_quarter"]
        for fname in ["return_cumulative", "benchmark_return", "peer_percentile",
                      "excess_return_cumulative", "benchmark_return_calculated"]:
            if m[fname] != sel[fname]:
                problems.append(f"{m['manager']}: top-level {fname} != selected_quarter")

        t = m["performance_trends"]
        if t["consecutive_quarters_underperforming"] and t["consecutive_quarters_outperforming"]:
            problems.append(f"{m['manager']}: both streaks non-zero")
        for skey in ["consecutive_quarters_underperforming",
                     "consecutive_quarters_outperforming"]:
            if not 0 <= t[skey] <= 4:
                problems.append(f"{m['manager']}: {skey} = {t[skey]}")
        if t["trend_direction"] not in {"improving", "deteriorating",
                                        "mixed", "insufficient_data"}:
            problems.append(f"{m['manager']}: trend_direction {t['trend_direction']}")

        for pkey, pct in m["ranking_trend"].items():
            if pct != periods[pkey]["peer_percentile"]:
                problems.append(f"{m['manager']}: ranking_trend {pkey} mismatch")

    # ---- market aliases must stay byte-identical ----------------------------
    selected_view = d["market_data_periods"]["selected_quarter"]
    for cat in ["factors", "sectors", "industries"]:
        if d["market_data"][cat] != selected_view[cat]:
            problems.append(f"market_data.{cat} != market_data_periods.selected_quarter")
    if d["summaries"] != selected_view["summaries"]:
        problems.append("summaries alias != selected_quarter summaries")

    status = "OK" if not problems else f"{len(problems)} PROBLEM(S)"
    print(f"{path.name}: {len(managers)} managers, "
          f"{len(d['manager_lookup'])} lookup keys - {status}")
    for problem in problems:
        print("   ", problem)
```

Then confirm the period mapping is what you expect:

```python
import json
report = json.load(open("debug_layout_report.json", encoding="utf-8"))
for name, wb in report["workbooks"].items():
    print(name)
    for line in wb.get("logical_period_map_summary", ["<none>"]):
        print("   ", line)
```

---

## 12. Sign-off

- [ ] All sections above complete.
- [ ] Warnings reviewed and accepted.
- [ ] Agent produced a correct draft for at least one manager per asset class.

Signed: `____________`  Date: `____________`
