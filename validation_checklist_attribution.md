# Validation Checklist — Attribution JSON (Phase 2)

Run after every attribution build, **before** pointing the commentary agent at
the new folder. Assumes the Phase 1 checklist has already passed for the same
quarter.

Quarter built: `____________`  Run by: `____________`  Date: `____________`

---

## 1. Run order and prerequisites

- [ ] Phase 1 (`build_quarterly_json.py`) ran for this quarter and passed its own
      checklist.
- [ ] **Phase 2 ran *after* Phase 1.** Phase 1 rewrites the asset-class files
      from scratch and removes the attribution pointers, so a Phase 1 rerun always
      needs a Phase 2 rerun behind it.
- [ ] `build_attribution_json.py` completed with **exit code 0**
      (`echo %ERRORLEVEL%` on Windows).
- [ ] Console reports `N of N attribution workbooks written`.
- [ ] Error count in the final `Done.` line is **0**. Any error means at least
      one workbook did not link to a manager — see §3.

## 2. Files present

In `Quarterly Battle Book Data\<quarter>\`:

- [ ] `attribution\` exists with one `.json` per attribution workbook.
- [ ] Filenames are manager slugs (`t_rowe_price_blue_chip_growth.json`), not
      workbook names.
- [ ] `debug_attribution_report.json` exists and is from today's run.
- [ ] `large_growth.json`, `mid_growth.json`, `small_growth.json` have today's
      timestamp (they were patched).
- [ ] `manifest.json` and `debug_layout_report.json` are **unchanged** from the
      Phase 1 run — Phase 2 must not touch them.

## 3. Manager linkage — the highest-risk gate

A silent mis-link attaches one manager's attribution to another. Check every file.

- [ ] `debug_attribution_report.json → unmatched_attribution_files` is empty.
- [ ] `errors` is empty.
- [ ] For each workbook, `manager_linkage.linked` is `true`.
- [ ] `manager_linkage.match_type` is `manager_name` for every workbook. A value
      of `alias` means the portfolio label matched a ticker or strategy name
      rather than the fund name — verify that manager by hand before shipping.
- [ ] `manager_linkage.portfolio` (from the workbook) and `manager_linkage.manager`
      (from Phase 1) describe the same fund.
- [ ] `manager_linkage.benchmark_matches` is `true`. If `false`, the attribution
      export was run against a different benchmark than Phase 1 records — stop
      and resolve at source.
- [ ] No two attribution files resolve to the same manager.

## 4. Period selection

- [ ] `logical_period_map_summary` shows `selected_quarter -> ` the band whose
      dates equal the quarter you built (e.g. `4-1-2026 - 6-30-2026` for Q2 2026).
- [ ] `selected_period_band.resolved_by` is `matched_by_date`.
- [ ] `two_quarters_ago`, `three_quarters_ago`, `four_quarters_ago` map to
      consecutive earlier quarters.
- [ ] In each attribution JSON, `period_start_date` / `period_end_date` are the
      calendar quarter bounds and `period_band_label` matches the workbook band.

## 5. Structure detection

- [ ] `sector_rows_found` is **11** for every workbook.
- [ ] `sectors_missing` is empty.
- [ ] `security_rows_in_gics_sectors` is plausible (roughly 480–810 on the
      current exports) and `security_rows_outside_gics_sectors` accounts for the
      `Bond` / `Other` / `Missing Performance` rows.
- [ ] `row_classification_counts` shows no unexpected category.
- [ ] `securities_stored` is far smaller than `security_rows_in_gics_sectors`
      (tens, not hundreds) — the materiality filter is working.
- [ ] `securities_filtered_out` + `securities_stored` equals
      `securities_in_gics_sectors`.

## 6. Reconciliation (debug file only)

These numbers must **never** appear in the attribution JSON — confirm they are
present in the debug file and absent from the manager files.

- [ ] `reconciliation.sector_sum_matches_attribution_total` is `true` for every
      manager.
- [ ] `reconciliation.security_sum_matches_selection_total` is `true` for every
      manager.
- [ ] `reconciliation.attribution_coverage_percent` is between 90 and 101.
- [ ] `reconciliation.phase1_excess_return` matches the manager's
      `excess_return_cumulative` in the asset-class JSON.
- [ ] `reconciliation.attribution_vs_reported_gap` is recorded. A large gap is
      not a build failure, but note it — it caps how much of the excess return
      attribution can explain.

## 7. Battle-book JSON must stay commentary-focused

- [ ] Search each attribution JSON for `residual`, `gap`, `coverage`, `expense`,
      `reconcil`, `unattributed`, `caveat`. **All must return zero hits.**
- [ ] Search for `industry_attribution`. Must return zero hits — no object and no
      placeholder.
- [ ] Top-level keys are exactly: `manager`, `manager_lookup_key`, `asset_class`,
      `asset_class_key`, `benchmark`, `period`, `period_start_date`,
      `period_end_date`, `period_band_label`, `model`, `classification`,
      `lineage`, `attribution_summary`, `sector_attribution`,
      `security_attribution`, `security_attribution_meta`, `top_contributors`,
      `top_detractors`, `sector_rankings`, `attribution_periods`,
      `attribution_trends`, `concentration`.

## 8. `attribution_summary`

- [ ] All four effects present with matching `_bps` fields.
- [ ] `allocation + selection + interaction ≈ total_active_return` (within
      0.05 pp).
- [ ] `primary_driver` is one of `selection`, `allocation`, `interaction`, `mixed`.
- [ ] `driver_confidence` is one of `high`, `medium`, `low`, `insufficient_data`.
- [ ] Sanity: the driver is the effect with the largest absolute value, unless
      the value is `mixed`.

## 9. `sector_attribution`

- [ ] Exactly 11 records, one per GICS sector, including sectors at zero weight.
- [ ] `effect_rank` runs 1..11 with no duplicates, ranked by **absolute** total
      effect.
- [ ] `position` is one of `overweight`, `underweight`, `neutral`, `not_held`,
      and agrees with `active_weight` (`not_held` only when
      `portfolio_weight` is 0).
- [ ] `driver` is one of `allocation`, `selection`, `interaction`, `mixed`.
- [ ] For one sector, verify against Excel:
      `portfolio_weight`, `benchmark_weight`, `allocation_effect`,
      `selection_effect`, `interaction_effect`, `total_effect` — using the band
      columns listed in `debug_attribution_report.json →
      selected_period_band.columns`.

## 10. `security_attribution`, contributors and detractors

- [ ] `held` is `true` exactly when `portfolio_weight > 0`, and `false` when
      `portfolio_weight` is 0 and `benchmark_weight > 0`.
- [ ] `top_contributors` is sorted descending and contains only positive
      `total_effect` values; `rank` runs 1..N.
- [ ] `top_detractors` is sorted ascending and contains only negative
      `total_effect` values; `rank` runs 1..N.
- [ ] Every security named in `top_contributors` / `top_detractors` also appears
      in `security_attribution`.
- [ ] `reason` is always one of the nine enum values.
- [ ] **Spot-check the enum on one not-held name.** A security with
      `held: false` must carry a `not_held_*` reason. This is the check that
      stops the agent describing a benchmark constituent as a holding.
- [ ] Spot-check one security's weights, returns and effect against Excel.

## 11. `attribution_periods` and `attribution_trends`

- [ ] Four period keys present; unavailable ones are `null` with
      `available: false`, never zero.
- [ ] Each period's four effects have matching `_bps` fields.
- [ ] `consecutive_quarters_selection_negative` and `..._positive` are never both
      non-zero; same for the allocation pair.
- [ ] Manually verify one streak by reading `selection_effect` across
      `selected_quarter` → `two_quarters_ago` → `three_quarters_ago` →
      `four_quarters_ago` and confirming the count stops where the sign flips.
- [ ] `driver_stability` is `consistent`, `variable` or `insufficient_data`.
- [ ] `dominant_driver_selected_quarter` agrees with
      `attribution_summary.primary_driver` unless that value is `mixed`.

## 12. `concentration`

- [ ] All three share values are between 0 and 1, or `null`.
- [ ] `number_of_positive_securities` + `number_of_negative_securities` is
      plausible against `security_attribution_meta.securities_in_gics_sectors`
      (the remainder are securities with a zero or missing effect).
- [ ] `number_of_sectors_positive` + `number_of_sectors_negative` ≤ 11.
- [ ] Sanity: a high `top_5_absolute_effect_share` matches a genuinely
      concentrated result.

## 13. Basis-point fields

- [ ] Every `*_bps` field equals its percent field × 100 (see §16).
      Percentages are rounded to 4 decimals and bps to 1, so the identity is
      exact by construction — any mismatch means a builder was edited.

## 14. Asset-class patching

- [ ] Every manager in all three asset-class files has `attribution_available`
      and `attribution_path` (managers without attribution get `false` / `null`).
- [ ] `attribution_index.count` equals the number of attribution files written
      for that asset class.
- [ ] Every `attribution_path` points to a file that exists on disk.
- [ ] Phase 1 content is intact: `managers`, `manager_lookup`,
      `performance_periods`, `performance_trends`, `ranking_trend`,
      `market_data`, `summaries`, `market_data_periods`, `market_trends` all
      still present with the same manager counts as the Phase 1 manifest.

## 15. Agent readiness

- [ ] Files synced to the folder the agent reads; OneDrive shows sync complete.
- [ ] Ask: *"Create a battle book for T. Rowe Price Blue Chip Growth for
      \<quarter\>."*
- [ ] The draft attributes the result to the correct driver (selection vs
      allocation) and the numbers match `attribution_summary`.
- [ ] Sector claims match `sector_attribution` / `sector_rankings`, in basis
      points.
- [ ] **Any not-held security is described as not owned**, never as a holding.
- [ ] The draft does **not** discuss residuals, attribution gaps, coverage,
      expense ratios or benchmark reconstruction. If it does, the language came
      from the agent's own guidance documents, not from this JSON — fix it there.
- [ ] Repeat for a manager **without** attribution and confirm the agent handles
      `attribution_available: false` gracefully.

---

## 16. Automated spot-check

Run from the output quarter folder.

```python
import json, pathlib

REASONS = {"overweight_outperformer", "overweight_underperformer",
           "underweight_outperformer", "underweight_underperformer",
           "not_held_outperformer", "not_held_underperformer",
           "held_positive_selection", "held_negative_selection", "mixed"}
POSITIONS = {"overweight", "underweight", "neutral", "not_held"}
DRIVERS = {"allocation", "selection", "interaction", "mixed"}
BANNED = ("residual", "gap", "coverage", "expense", "reconcil",
          "unattributed", "caveat", "industry_attribution")

for path in sorted(pathlib.Path("attribution").glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    problems = []

    # bps fields must equal percent x 100
    def check_bps(node):
        if isinstance(node, dict):
            for key, value in node.items():
                base = key[:-4]
                if key.endswith("_bps") and base in node:
                    pct, bps = node[base], value
                    if pct is not None and bps is not None:
                        if abs(bps - round(pct * 100, 1)) > 1e-9:
                            problems.append(f"{key}: {bps} != {pct} x 100")
                check_bps(value)
        elif isinstance(node, list):
            for item in node:
                check_bps(item)
    check_bps(d)

    # summary
    s = d["attribution_summary"]
    parts = [s["allocation_effect"], s["selection_effect"], s["interaction_effect"]]
    if all(p is not None for p in parts) and s["total_active_return"] is not None:
        if abs(sum(parts) - s["total_active_return"]) > 0.05:
            problems.append("effects do not sum to total_active_return")
    if s["primary_driver"] not in DRIVERS:
        problems.append(f"primary_driver {s['primary_driver']}")

    # sectors
    sectors = d["sector_attribution"]
    if len(sectors) != 11:
        problems.append(f"{len(sectors)} sectors, expected 11")
    if sorted(x["effect_rank"] for x in sectors) != list(range(1, len(sectors) + 1)):
        problems.append("effect_rank not 1..n")
    for sec in sectors:
        if sec["position"] not in POSITIONS:
            problems.append(f"position {sec['position']}")
        if sec["driver"] not in DRIVERS:
            problems.append(f"driver {sec['driver']}")
        pw = sec["portfolio_weight"] or 0
        if (sec["position"] == "not_held") != (pw <= 0):
            problems.append(f"{sec['sector']}: not_held disagrees with weight")

    # securities
    for sec in d["security_attribution"]:
        if sec["held"] != ((sec["portfolio_weight"] or 0) > 0):
            problems.append(f"{sec['security']}: held flag wrong")
        if sec["reason"] not in REASONS:
            problems.append(f"{sec['security']}: reason {sec['reason']}")
        if not sec["held"] and not sec["reason"].startswith(("not_held", "mixed")):
            problems.append(f"{sec['security']}: not held but reason {sec['reason']}")

    # movers
    contrib = [r["total_effect"] for r in d["top_contributors"]]
    detract = [r["total_effect"] for r in d["top_detractors"]]
    if contrib != sorted(contrib, reverse=True) or any(v <= 0 for v in contrib):
        problems.append("top_contributors bad")
    if detract != sorted(detract) or any(v >= 0 for v in detract):
        problems.append("top_detractors bad")
    stored = {(x["security"], x["ticker"]) for x in d["security_attribution"]}
    for r in d["top_contributors"] + d["top_detractors"]:
        if (r["security"], r["ticker"]) not in stored:
            problems.append(f"{r['security']} missing from security_attribution")

    # streaks
    t = d["attribution_trends"]
    for a, b in [("consecutive_quarters_selection_negative",
                  "consecutive_quarters_selection_positive"),
                 ("consecutive_quarters_allocation_negative",
                  "consecutive_quarters_allocation_positive")]:
        if t[a] and t[b]:
            problems.append(f"both streaks non-zero: {a}/{b}")

    # commentary scope
    blob = json.dumps(d).lower()
    hits = [b for b in BANNED if b in blob]
    if hits:
        problems.append(f"audit language present: {hits}")

    status = "OK" if not problems else f"{len(problems)} PROBLEM(S)"
    print(f"{path.name}: {len(sectors)} sectors, "
          f"{len(d['security_attribution'])} securities - {status}")
    for problem in problems:
        print("   ", problem)
```

Then confirm linkage, period mapping and reconciliation from the debug file:

```python
import json
report = json.load(open("debug_attribution_report.json", encoding="utf-8"))
print("unmatched:", report["unmatched_attribution_files"])
print("errors:   ", len(report["errors"]))
for wb in report["workbooks"]:
    link = wb.get("manager_linkage", {})
    rec = wb.get("reconciliation", {})
    print(f"\n{wb['file']}")
    print(f"   linked={link.get('linked')} match_type={link.get('match_type')} "
          f"benchmark_matches={link.get('benchmark_matches')}")
    for line in wb.get("logical_period_map_summary", []):
        print("   ", line)
    if rec:
        print(f"   sector sum matches total:    {rec['sector_sum_matches_attribution_total']}")
        print(f"   security sum matches select: {rec['security_sum_matches_selection_total']}")
        print(f"   coverage {rec['attribution_coverage_percent']}%  gap {rec['attribution_vs_reported_gap']}")
```

And confirm the asset-class patch:

```python
import json, pathlib
for name in ["large_growth.json", "mid_growth.json", "small_growth.json"]:
    d = json.loads(pathlib.Path(name).read_text(encoding="utf-8"))
    idx = d.get("attribution_index", {})
    flagged = [m for m in d["managers"] if m.get("attribution_available")]
    unpatched = [m["manager"] for m in d["managers"]
                 if "attribution_available" not in m]
    broken = [m["manager"] for m in flagged
              if not pathlib.Path(m["attribution_path"]).is_file()]
    print(f"{name}: index={idx.get('count')} flagged={len(flagged)} "
          f"unpatched={len(unpatched)} broken_paths={len(broken)}")
```

---

## 17. Sign-off

- [ ] All sections above complete.
- [ ] Zero linkage errors.
- [ ] Zero audit-language hits in the attribution JSONs.
- [ ] Agent produced a correct attribution paragraph for at least one manager,
      with the not-held distinction handled correctly.

Signed: `____________`  Date: `____________`
