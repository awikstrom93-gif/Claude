# US Small Cap Growth — Asset-Class & Benchmark Review
## Project Charter & Phased Build Plan (v1.0)

### 1. Background & mandate

Active US Small Cap Growth (SCG) managers have underperformed the **Russell 2000 Growth
(R2000G)** over roughly the trailing 2.5 years; managers are benchmarked to R2000G and clients
evaluate them against it. This project is an **asset-class review**: understand the benchmark,
characterize how it has evolved (2015–2026), dissect its composition, and **connect that
evolution to the active-management headwinds** that plausibly explain the underperformance —
in a form an Investment Committee (IC) can act on.

The governing question is not "what is the index" but **"how has the index changed in ways
that made it hard for a quality-disciplined active manager to keep up — and is R2000G even the
right yardstick?"**

### 2. Questions the deliverable must answer

1. **Composition & quality drift.** How have profitability, margins, ROE/ROIC, leverage,
   growth, sector mix, and concentration of R2000G evolved 2015–2026?
2. **Underperformance decomposition.** How much of the manager shortfall is explained by
   benchmark dynamics — the profitless-growth rally, narrow breadth, the graduation of
   winners, biotech idiosyncrasy, rate/duration regime — versus manager skill?
3. **Benchmark fit.** Is R2000G the appropriate benchmark for a quality-oriented SCG manager,
   given that **S&P SmallCap 600 Growth applies a positive-earnings screen and R2000G does
   not**? How much of the "shortfall" is a benchmark-mismatch artifact?
4. **IC conclusion.** What should the committee take away about manager evaluation, benchmark
   choice, and the asset class?

### 3. Deliverables

- **D1 — Fundamentals data engine:** an as-originally-filed constituent fundamentals dataset,
  2010–2026, accuracy-validated.
- **D2 — Analytics workbooks:** benchmark evolution, cohort decomposition, concentration/
  breadth, turnover/graduation, and (data permitting) cohort performance attribution.
- **D3 — Benchmark comparison:** R2000G vs S&P SmallCap 600 Growth composition & quality.
- **D4 — IC report:** a written narrative tying benchmark evolution to active headwinds, with
  charts and an explicit benchmark-fit recommendation.

### 4. Architecture & accuracy guarantees (the from-scratch decision)

**Decision: rebuild value derivation from scratch; reuse the verified identity layer; legacy
pipeline is an oracle only.** Starting over is *necessary but not sufficient* for accuracy —
the guarantee comes from the spine and the validation, not from the act of starting over.

- **Contract-first.** The definitions policy (v1.0) and a frozen ground-truth set are the spec
  that drives extraction.
- **Identity layer reused (not rebuilt).** Ticker→CIK with survivorship recovery and the
  temporal point-in-time map carry *no tagging risk* and were independently verified against
  SEC submissions. Rebuilding them only re-incurs solved problems.
- **As-filed-by-accession spine.** Each constituent-year's value is read from that period's
  **original 10-K accession** (submissions API gives the first-filed accession + filing date;
  companyfacts facts are filtered to that `accn`). This eliminates cross-vintage blending and
  the entire heuristic reconstruction engine, and is fully reachable via `data.sec.gov` JSON.
- **No legacy value is ever an input.** `edgar_annual_fundamentals.csv` is used solely as an
  **oracle to diff against**, so no prior tagging mistake can propagate.
- **Validation is first-class.** Ground-truth fixtures (every known failure: DAWN scale, NANO
  comparative, AMRX shell, Soleno sign, Kodak undercount, NN Inc equity), accounting-identity
  invariants, and dual-source agreement (accession-companyfacts vs DERA face-of-statement)
  with the definitions policy as adjudicator.

### 5. Data sources & dependencies

| Source | Use | Status / risk |
|---|---|---|
| SEC EDGAR (submissions + companyfacts) | As-filed fundamentals | Reachable via data.sec.gov JSON on the corporate laptop (truststore) |
| Holdings snapshots (annual, weighted, CUSIP/ISIN, GICS/Morningstar) | Point-in-time membership & weights | In hand |
| Morningstar Direct | Constituent total returns | **Survivorship gap** on delisted/acquired micro-caps — test coverage first |
| Bloomberg / FactSet / CRSP (by CUSIP/ISIN) | Backfill delisted-name returns | Required only if Morningstar gap is material |
| Published R2000G total return | Control total for attribution | Public/index provider |
| S&P SmallCap 600 Growth constituents & characteristics | Benchmark comparison (D3) | Source TBD (S&P / Morningstar / provider) |

**Returns survivorship is the key data risk.** Delisting is non-random (bankruptcies skew
cohort returns down; acquisitions skew up), so any cohort attribution must be survivorship-free
or transparently coverage-bounded. Mitigation: CUSIP/ISIN-keyed backfill + published index
return as control + per-year coverage disclosure.

### 6. Analytical design (the dissection)

Built in layers, each consuming the validated dataset:

**Layer 1 — Composition & quality over time.**
Profitability (NI>0 and OI>0, count & weight), operating/net margins, ROE & ROIC (average
denominators), leverage (D/E, D/Capital ex-operating-leases), revenue & earnings growth.
*Plus the standards-grounded quality lenses suited to a non-earner-heavy universe:*
**gross-profits-to-assets (Novy-Marx)**, **accruals (Sloan; surfaces the SBC-inflates-CFO
distortion)**, a **Piotroski-style health score**, and a **DuPont decomposition** of ROE
(margin × turnover × leverage) to explain *why* index ROE moved.

**Layer 2 — Concentration & breadth.**
Top-N weight, HHI, **effective number of names**, mega-cap-creep at the top of the index.
Narrow leadership is a structural reason a cap-weighted benchmark is hard to beat.

**Layer 3 — Turnover & the graduation drag.**
Entry/exit each reconstitution; quantify the "successful-leaver" effect — winners acquired or
graduated to mid-cap, so the index banks the run-up then loses the compounder.

**Layer 4 — Cohort performance attribution (returns-dependent).**
Decompose the benchmark's return into contribution from profitable vs unprofitable, biotech vs
non-biotech, top-10 vs rest. Translate into active terms (e.g., "unprofitable cohort returned
X vs profitable Y in 2023 → an N-bp headwind to a profitability tilt"). **This is the bridge
from 'the index changed' to 'why managers lagged.'**

**Layer 5 — Benchmark fit: R2000G vs S&P SmallCap 600 Growth.**
The non-obvious centerpiece. S&P 600 requires positive earnings to enter; R2000G does not, so
R2000G *includes* the profitless cohort a quality manager avoids. Compare the two indices'
quality profiles and (with returns) performance over the window to test how much "shortfall"
is **benchmark mismatch** versus skill.

**Layer 6 — Active-management implications.**
Synthesize Layers 1–5 into the IC narrative: where the benchmark created structural headwinds
for quality discipline, and a defensible view on benchmark choice and manager evaluation.

### 7. Phased build plan & acceptance gates

| Phase | Work | Acceptance gate |
|---|---|---|
| **0 — Contract** | Definitions policy + frozen ground-truth set (15–25 hand-verified cells across sectors/eras/edge cases) | Policy signed off (done); ground-truth set built & verified vs actual 10-Ks |
| **1 — Identity** | Reuse + re-verify point-in-time membership & temporal CIK | Coverage ≥ prior (97–99.8% weight); submissions re-verification clean |
| **2 — Extraction** | As-filed-by-accession engine; legacy run in parallel as oracle | 100% of ground-truth fixtures pass; cell-level diff vs oracle triaged (vintage→adjudicated, definitional→policy, defect→queue) |
| **3 — Quality metrics** | CFA-grounded metrics + quality lenses + DuPont | Identity checks pass; aggregates within sanity bounds |
| **4 — Returns** | Morningstar pull + survivorship backfill; cohort attribution | Per-year return coverage disclosed; reconciles to published index return within tolerance |
| **5 — Benchmark comparison** | S&P 600 Growth composition & performance contrast | Comparison reconciles to each index's published characteristics |
| **6 — IC report** | Narrative, charts, benchmark-fit recommendation | IC-ready review |

Legacy pipeline runs as oracle through Phases 2–3; cutover only after ground-truth + diff
gates pass — making the rebuild graded and reversible, not a leap.

### 8. Risks & mitigations

- **Return survivorship bias** → CUSIP/ISIN backfill + index-return control + coverage
  disclosure (§5).
- **Original-filing XBRL errors** (filer scale/sign typos) → identity checks + ground-truth +
  manual queue; as-filed faithfully reproduces them, so they must be caught, not assumed away.
- **Financial & biotech special cases** → sector-specific revenue, blanked margins for
  financials, marketable-securities runway for biotech (carried from policy).
- **Benchmark data licensing** (R2000G/S&P 600 constituents & returns) → confirm entitlements
  early; index providers can supply constituent-level data.
- **Scope creep into manager-level attribution** → out of scope; this review characterizes the
  *benchmark*, not individual manager portfolios (a possible follow-on).

### 9. Out of scope (this phase)

Individual manager holdings-based attribution; forward-looking return forecasts; portfolio
construction recommendations. The review equips the IC to interpret those, but does not produce
them here.
