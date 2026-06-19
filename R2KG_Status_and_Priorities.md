# US Small Cap Growth Review — Status & Priorities

*Recalibration checkpoint. Updated as the project moves.*

## Where we are

The **thinking/contract phase is essentially complete and sound.** What's locked:

- **Mandate (clear):** asset-class review explaining active SCG manager underperformance vs the
  **Russell 2000 Growth** benchmark of record; characterize how the benchmark evolved 2015–2026
  and connect that to active headwinds; IC-ready.
- **Definitions standard (v1.0):** one concept per field, every pipeline-vs-DERA fork resolved,
  grounded in vendor/CFA/index conventions.
- **Build decision:** rebuild *value derivation* from scratch on an **as-filed-by-accession**
  spine (submissions + companyfacts filtered to the original 10-K accession — reachable on the
  locked laptop, no bulk downloads); **reuse the verified identity layer** (no tagging risk);
  **legacy pipeline = oracle only** (never a value source).
- **Returns:** monthly total return is the right stream; coverage audited — **>99.5% of names,
  ~99.9% of weight**; the 18-name gap is resolved/dispositioned with a survivorship + point-in-time
  identity protocol (membership-window-bounded, CIK-anchored, verified vs rename dates).

## What is NOT yet built (the actual product)

- **Ground-truth set** (Phase 0) — the frozen, hand-verified fixtures. *Not started. This is the
  long pole and the gate for everything downstream.*
- **As-filed extraction engine** (Phase 2) — *not started.*
- **Quality metrics + analytics** (cohorts, DuPont, attribution, composition decomposition) —
  *not started.*
- **Returns join** — 53 CIKs resolved; matrix not yet re-keyed/membership-bounded.
- **S&P SmallCap 600 Growth data** (Layer 5 comparison) — *not sourced.*
- **Month-end weights/market cap** (point-in-time attribution) — *not pulled.*

The risk now is staying in planning. The contract is good enough to start building.

## Critical path

Accuracy of the **fundamentals dataset** is the foundation the entire review rests on (it is the
whole reason for the rebuild). Everything else — quality metrics, cohorts, attribution, the
benchmark comparison — consumes it. So the critical path is:

**Ground-truth set → pilot extraction engine (proven on a regression sample) → scale → metrics →
analytics → report.**

De-risk by building a **thin vertical slice first**: the new extractor on a ~30–50 name regression
set (including every known failure — DAWN, NANO, AMRX, Soleno, Kodak, NN Inc — plus a sector
spread), validated against ground truth *and* diffed against the legacy oracle. Prove accuracy on
the slice before committing to the full ~3,400.

## Priorities

| # | Priority | Track | Depends on |
|---|---|---|---|
| **P1** | **Pilot extraction engine** on the regression set; validate vs ground truth + oracle | Build (Claude writes, user runs) | companyfacts cache (have it), P2 |
| **P2** | **Ground-truth set** — hand-verify ~20–25 cells against actual 10-K faces | Human (user/analyst) | — (start now) |
| **P3** | **Source S&P 600 Growth** constituents/characteristics + **pull month-end weights** | Data sourcing (user) | entitlements |
| **P4** | **Scale extraction** to full universe; oracle-diff; triage residuals | Build | P1 proven |
| **P5** | **Returns join** — re-key by CIK, membership-window bounded | Build | P4, returns file |
| **P6** | **Quality metrics + analytics + IC report** | Build | P4, P5 |

P2 and P3 run **in parallel** with P1 (they're human/data-sourcing tracks, off the build critical
path but with lead time).

## Immediate next step

Start P1 + P2 together: Claude drafts the **pilot as-filed extraction engine** and a **ground-truth
template** for the user to fill against ~20 actual 10-Ks. First proof point: the pilot reproduces
the ground-truth cells exactly and its disagreements with the legacy oracle are all explained
(vintage / definitional / defect), with zero unexplained misses.
