# Russell 2000 Growth — Index Quality Study
## Blank-Slate Design Memo

**Thesis.** Build the *contract* — identity over time, and the definitions standard —
**first**, treat extraction as a thin, swappable adapter behind it, and bake validation in
from line one. The existing build inverted this order: it grew a sophisticated extraction
engine first and bolted definitions, verification, and audits on afterward. That inversion is
the root cause of the 2,000-line `_annual_records` reconstruction — it is heroic compensation
for not having pinned the contract up front. With the contract fixed, the engine becomes a few
hundred deterministic lines.

---

### 1. Start from the question; let it impose non-negotiable invariants

The question — *how has the fundamental quality of the R2KG constituents evolved 2015–2026* —
is a **longitudinal composition study**, and that dictates three architectural invariants that
must be designed in, never patched in later:

1. **Point-in-time (no look-ahead).** Each snapshot sees only what was filed by that date —
   FY0 chosen on the actual 10-K acceptance date, not a fixed lag.
2. **Survivorship-free.** Delisted/acquired/renamed constituents are first-class; the universe
   is reconstructed *as it was*, not as it survives today.
3. **As-originally-reported.** Each constituent-year is the value in that period's original
   10-K; restatements are not back-applied (they cluster in the low-quality cohort the study
   measures, so hindsight would bias index quality upward).

These are exactly the constraints that justify the existence of point-in-time / as-first-
reported databases in academic and backtest work. Treat them as invariants the test suite
enforces.

"Quality" imposes a fourth: **CFA ratio discipline** (average denominators, DuPont
consistency, NOPAT-based ROIC, sector carve-outs) decided before any number is computed.

"How the *makeup* changed" imposes a fifth, and it's the one the current build lacks: a
**decomposition** of index-level change into its drivers, not just a level time-series (§6).

---

### 2. Architecture: five contract-driven layers + a validation spine

In dependency order, each talking to the next through a stable schema (the seam the current
project already has and should keep):

- **A. Identity & membership (point-in-time).** Ticker→CIK with survivorship recovery and a
  temporal map. *Keep the existing work almost verbatim — it is genuinely good and hard-won.*
- **B. Definitions contract.** The policy already written: one standardized economic concept
  per field, with the parent-level / restricted-cash / leverage / FCF / sector rules. This is
  the **spec that drives extraction**, replacing discovered-by-trial tag lists.
- **C. Extraction adapter (thin, swappable, validated).** §3.
- **D. Quality metrics.** CFA-grounded. §5.
- **E. Composition analytics.** Decomposition + cohorts. §6.
- **Validation harness** spanning C–E as a first-class citizen, not an afterthought. §7.

---

### 3. The extraction insight that dissolves the 2,000-line engine

Two problems drove all the heuristics. Both dissolve cleanly:

**(a) Vintage — solved by selecting facts by the *original accession*, not by inference.**
companyfacts already carries `accn` on every fact; the submissions API gives each fiscal
year's **first-filed 10-K** accession and filing date (authoritative vintage). So:

> as-filed value = companyfacts facts **filtered to the original 10-K accession** for that
> period.

This is exactly what your own `validate_accession_selection.py` and `accession_adjudicator.py`
already prove works. It means: **no bulk FSDS download, no cross-vintage blending, fully
reachable via `data.sec.gov` JSON on the locked-down laptop.** The entire native/comparative/
latest-filed/magnitude-anchor/shell-year apparatus — DAWN, NANO, AMRX, the 52/53-week re-key —
largely *evaporates*, because a single original accession has no comparatives and no
restatements to disentangle.

**(b) Concept selection — solved by the definitions contract, not global tag priority.**
Each field's acceptable tags come from layer B in priority order; the residual segment-fragment
cases (Kodak/Federal Signal: a smaller generic `Revenues` masking the true `SalesRevenueNet`
top line) are caught by an optional **presentation-linkbase tiebreaker** (face-of-statement
order) — used only where the policy priority is ambiguous, not as the spine.

Net result: extraction is *"enumerate original accessions → pull as-filed facts → apply the
definitions contract"* — deterministic, auditable, ~a few hundred lines, and the source
(companyfacts-by-accession today, per-accession R-files or FSDS later) becomes a swappable
adapter behind a fixed schema.

---

### 4. Definitions decided up front (done) — plus the quality lenses I'd add

Beyond the policy already written (parent-level NI & equity for ROE consistency, cash excl.
restricted, total CFO, interest-bearing debt ex-operating-leases, simple levered FCF,
bank/insurer revenue carve-outs), a blank slate is the moment to add the **academic/CFA
quality lenses** that suit a no-earnings-heavy small-cap growth universe far better than ROE
alone:

- **Gross-profits-to-assets** (Novy-Marx): (Revenue − COGS) / Total Assets. The most robust
  academic quality measure, and it *works for growth names with no net income* — exactly this
  universe.
- **Accruals** (Sloan): (NI − CFO) / avg assets. A direct read on earnings quality — and
  pointed here, because the SBC add-back inflates CFO and thus flatters accruals; tracking it
  surfaces that distortion rather than hiding it.
- **Piotroski-style profitability/quality flags** (profitability, leverage/liquidity,
  efficiency) as a composite "fundamental health" score for the unprofitable-cohort work.
- **DuPont decomposition** of ROE (margin × turnover × leverage) so the study can explain
  *why* index ROE moved, not just that it did.

---

### 5. Quality metrics — CFA discipline, decided not discovered

- Return denominators use **average** opening/closing balances (matters: growth names issue
  equity mid-year, so ending equity understates ROE).
- **ROIC** = NOPAT / average invested capital, with sector exclusions for financials.
- Every index aggregate reported three ways — **weight-weighted, equal-weight median+IQR,
  dollar-aggregate** — plus **winsorization** on per-name ratios, never on dollar levels.
- **Negative / absent denominators are handled explicitly** (reported as their own series,
  not silently dropped) — critical when a large share of the index has negative equity or no
  revenue.

---

### 6. The deliverable the current build is missing: composition-change attribution

"How has the makeup changed" is best answered by **decomposing the change in any index metric
into its sources** — a Brinson-style attribution applied to fundamentals:

> Δ(index metric) = **within-name drift** (same constituents, year over year)
>                 + **turnover effect** (entrants vs leavers)
>                 + **reweighting effect** (surviving names' weight shifts)

This separates "the index changed because its *members* changed" from "because the *same
companies* changed" — the genuinely interesting question, and the one a level time-series
cannot answer. The existing Step 3 already gestures at this with same-constituent vs full-index
growth; I'd generalize it to *every* headline metric (margins, ROE, % unprofitable, leverage)
as the centerpiece output, with the cohort views (unprofitable decomposition, biotech,
concentration/HHI) layered on top.

---

### 7. Validation as a first-class citizen (test-driven extraction)

- **A frozen ground-truth set** — 15–25 (company, year) cells hand-verified against the actual
  10-K *face of statement*, spanning sectors, eras, and every known edge case (DAWN scale,
  NANO comparative, AMRX shell, Soleno sign, Kodak undercount, NN Inc equity). Each becomes a
  **regression fixture** the extractor must pass. The current project's battle scars become the
  test suite.
- **Dual-source agreement** (companyfacts-by-accession vs DERA face-of-statement) as a
  continuous confidence signal, with the **definitions policy as the adjudicator** — the recon
  harness already scoped: vintage diffs → accession-adjudicated; definitional diffs → policy
  lookup; only genuine defects reach a human.
- **Accounting-identity invariants** (A = L + E; NI ≈ pretax − tax; GP ≤ rev; cash ≤ assets)
  run on every build as cheap structural checks.

---

### 8. Keep / drop / add, explicitly

**Keep** (already correct): identity + survivorship mapping, temporal CIK, three-method
aggregation, the cohort framing, and the provenance/override audit discipline.
**Drop** (obviated): the vintage-reconstruction heuristics (replaced by accession selection)
and the global tag-priority guesswork (replaced by the definitions contract).
**Add**: composition-change attribution (§6), DuPont, the academic quality lenses (§4), the
as-filed-by-accession spine (§3), and TDD validation (§7).

---

### 9. Sequencing

- **Phase 0 — Contract:** definitions policy + frozen ground-truth set. *(Largely done.)*
- **Phase 1 — Identity:** point-in-time membership + temporal CIK. *(Have it.)*
- **Phase 2 — Extraction adapter:** as-filed-by-accession, validated against Phase 0, with the
  legacy companyfacts pipeline run **in parallel as an oracle** (diff every cell; agreement =
  confidence, disagreement = review queue).
- **Phase 3 — Quality metrics:** CFA-grounded.
- **Phase 4 — Composition analytics + cohorts.**

---

### Honest closing note

The existing build is not a mistake to regret — its scars *are* the specification, and a blank
slate is faster *now* precisely because those scars exist to point at. The one thing I would
change is **order**: pin identity and the definitions contract first, make validation a
first-class citizen, and write extraction last and thin. Do that, and the data source becomes a
detail rather than the architecture.
