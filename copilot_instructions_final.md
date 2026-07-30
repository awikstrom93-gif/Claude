# Final Copilot Studio Instructions (8,000-char limit) + Knowledge Document Updates

Reading the five knowledge documents changed the answer. Roughly 40% of the
instruction block was duplicating them. The instructions now carry **only what no
document can know** — the data mechanics — and the documents keep the analytical
and stylistic guidance they already do well.

**Final instruction block: 7,910 characters (90 to spare).** Full text in
`copilot_instructions_8k.txt`; paste it as the complete instructions, replacing
everything currently there.

---

## 1. What I cut, and which document now carries it

| Cut from instructions | Already covered by |
| --- | --- |
| Q1–Q4 scope, annual review, trend evaluation, length adjustment | **Period-Specific Commentary Rules** — far more thorough than my summary was (examples A/B/C, annual attribution, historical trend analysis) |
| Call to Action field list; market source priority; Market Mosaic; sector counts (3 detract / 2 contribute); security counts (top 3 / top 3, 4th–5th) | **Commentary Construction Rules** |
| Top-down hierarchy; 2–4 market themes; state-support-explain; not-owned securities criteria; concentrated vs broad-based; dominant security exception | **Alex Commentary Playbook** |
| Tone; claim-first evidence-second; avoid marketing/hyperbole/em dash; attribution discussion style | **Alex Writing Style Guide** |
| Length; paragraphs only; no bullets/numbered/hyperlinks; no research-memo or academic style; excessive macro; heading levels | **Battle Book Output Standards** |

What stayed is retrieval, the period gate, data conventions, field names, the
`held`/`reason` enum, the no-attribution path, and the anti-preamble rules —
none of which appear in any document.

---

## 2. Two rules that would otherwise be lost

I cut these from the instructions on the basis that documents cover them. **They
don't.** Add them or they disappear:

**Battle Book Output Standards v1** — add under `Length:`

```
Approximately one page
Generally one Call to Action paragraph and three to five Context & Analysis
paragraphs
Match the gold-standard battle books in length, density and detail, and do not
exceed their level of detail
```

**Battle Book Output Standards v1** — add a new section

```
Final Paragraph
The final paragraph should summarise performance drivers and contextualise the
result.
Do not introduce a new investment conclusion in the final paragraph.
```

Optionally, **Alex Writing Style Guide v1** — add under `Attribution Discussion`:

```
Use compact attribution language.
Do not restate the same conclusion in more than one way.
```

---

## 3. Documents that need updating

### Critical — same broken references I flagged in the instructions

**Alex Commentary Playbook v1** is priority 1, so its stale file references will
outrank a corrected instruction set. Two places:

*`Market Backdrop Process`* currently reads:

```
Use:
US Growth Sector, Industry, Factor
as the primary source.
```

Replace with:

```
Use the market_data, summaries, market_data_periods and market_trends objects
in the asset-class JSON as the primary source.
```

*`Data Source Hierarchy`* currently lists four sources by file name. Replace the
whole section with:

```
Data Source Hierarchy

First Source
The asset-class JSON for the quarter: large_growth.json, mid_growth.json or
small_growth.json.
Used for: return analysis, relative return, peer ranking, performance
significance, and multi-period history.

Second Source
The market objects inside that same JSON: market_data, summaries,
market_data_periods, market_trends.
Used for: factor leadership, sector leadership, industry leadership, market
themes.

Third Source
External market research and trusted web sources.
Used to explain why factor rotations occurred, why sectors moved, major macro
events and market narratives. Only include explanations supported by observed
market performance, and ensure they are current for the commentary period.

Fourth Source
The per-manager attribution JSON, when attribution_available is true, at the
location given by attribution_path.
Used for: sector attribution, security attribution, contributors, detractors,
not-owned benchmark impacts.
```

**Commentary Construction Rules v1** — `Market Backdrop` currently reads:

```
Primary source:
US Growth Sector, Industry, Factor
```

Replace with:

```
Primary source:
Market objects in the asset-class JSON (market_data, summaries,
market_data_periods, market_trends)
```

### Worth doing — makes existing rules executable

**Period-Specific Commentary Rules v1**, `Annual Attribution Analysis`, currently
opens *"When annual attribution is available"*. Make the condition concrete:

```
Annual attribution is available when attribution_available is true for the
manager. For a Q4 commentary, the four quarters in attribution_periods are the
four quarters of that calendar year.
```

**Period-Specific Commentary Rules v1**, `Historical Trend Analysis`, asks for
"consecutive periods of outperformance / underperformance". Those are computed:

```
Use performance_trends.consecutive_quarters_underperforming and
consecutive_quarters_outperforming rather than counting manually, and
trend_direction for whether results have improved or deteriorated.
```

### A conflict worth resolving

Three documents give three different placements for attribution in the narrative:

| Document | Stated flow |
| --- | --- |
| Battle Book Output Standards | Market → **Attribution** → Sector → Security |
| Alex Writing Style Guide | Market → Sector → Security *(attribution not placed)* |
| Alex Commentary Playbook, `Analytical Hierarchy` | Market → Factor → Sector → **Attribution** → Security |

Your original instructions used the Output Standards ordering. The Playbook's
section order (Part B Attribution, then Part C Sector) agrees with Output
Standards, but its own `Analytical Hierarchy` diagram does not. Worth picking one
and making all three match — the agent currently gets three answers to "where
does the attribution paragraph go".

---

## 4. Structural note

Nothing safety-critical was moved into a document. Instructions are always in
context; knowledge documents are *retrieved*, and retrieval can miss. So the
rules whose failure produces a wrong or misleading battle book — the period gate,
`basis`, `held`/`reason`, the no-attribution path, `effect_rank` — all stayed in
the instructions even where a document mentions something adjacent. Only
analytical and stylistic guidance was devolved.

The cheap safety-critical items (the two headings, no recommendation section)
also stayed, despite being double-covered, because they cost almost nothing.

---

## 5. Acceptance tests (unchanged)

| # | Prompt | Must be true | Must not happen |
| --- | --- | --- | --- |
| 1 | T. Rowe Price Blue Chip Growth, Q2 2026 | Selection-driven (−247 bps vs −29 bps allocation); Info Tech the weakest sector; **Lam Research described as not owned** | Lam Research called a holding; any residual/coverage/expense language |
| 2 | Fidelity Blue Chip Growth K, Q2 2026 | Selection-driven outperformance (+921 bps); Info Tech top contributor | Comm Services (−35 bps) framed as comparable in size to the contributors |
| 3 | Any Mid Growth manager, Q2 2026 | Commentary from performance, peer rank, history, market context only | Any allocation/selection language; any named contributor or detractor; any mention that attribution was missing |
| 4 | Any Small Growth manager, Q2 2026 | As #3 | Conclusions drawn from the near-empty industries list |
| 5 | **T. Rowe Price Blue Chip Growth, Q1 2026** (only Q2 loaded) | States data unavailable and stops | **Any commentary at all** |
