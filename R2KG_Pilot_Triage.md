# Pilot Triage — As-Filed vs Ground Truth & Legacy

*Regression set: 22 companies. Sources: hand-verified ground truth (110 cells) +
`asfiled_vs_legacy_diff.csv`. Verdict basis: ground truth is the arbiter; legacy is context.*

## Headline

**The as-filed-by-accession thesis is validated.** Every marquee case where as-filed *should*
beat the legacy is confirmed by ground truth — and the diff cleanly sorts the legacy's
heuristics into two piles:

- **Vintage-reconstruction heuristics → correctly deleted.** The accession spine makes them
  unnecessary, and ground truth confirms the as-filed values: KODK 2010 revenue, PDCE 2016
  revenue, NNBR 2020 equity, FSS 2011 revenue, and the **AMRX 2017 shell year** (as-filed
  correctly blank — ground truth found "no line"; legacy was contaminated by the post-combination
  recast).
- **Concept-definition heuristics → must be ported.** These decide *which concept*, independent
  of vintage, so the accession spine doesn't address them. The pilot deliberately stripped them
  and the diff shows exactly where that hurts: revenue total-selection, cash fallbacks, debt
  assembly, and the pretax tag. This is the real fix list (below).

## Focus-cell scorecard (vs ground truth)

| Verdict | Cases |
|---|---|
| **WIN** (as-filed right, legacy wrong, GT-confirmed) | KODK 2010 rev (7,187M), PDCE 2016 rev (382,915M), NNBR 2020 eq (254,152M) + NNBR 2016–18 eq (legacy was 0), FSS 2011 rev (795,600M), AMRX 2017 shell (blank) |
| **MATCH** (as-filed = legacy = GT) | CMC 2018 NI, NANO 2019 NI, IMKTA 2014 rev, SNEX 2022 rev, DAWN 2021 NI, PLXS 2018 rev, + clean controls SFM/CRDO/FN/SMCI/TDOC |
| **LOSS** (as-filed wrong vs GT) | **TRC 2016 pretax**, **BE 2023/24 rev**, **AMRX 2024 NI** (definitional), and same-metric breaks **PLXS 2013/14 rev**, **CHDN 2013–15 rev** |
| **GAP** (blank, value needed) | cash (MGLN, MKSI, CMC, IMKTA), early-XBRL years (2010–11), total_debt (FN, TDOC, MGLN) |

The clean controls (Sprouts, Credo, Fabrinet revenue, SMCI/TDOC revenue & equity) pass untouched —
**the pilot does not introduce errors on normal names.** The failures are concentrated and
diagnosable.

## Fix list for the production engine (priority order)

**1. Revenue concept selection — the #1 issue.** The pilot picks the first priority tag; the
policy's "presented consolidated total" rule must actually be implemented, not just flagged:
  - **Prefer-positive** — PLXS 2013/14 returned *negative* revenue (the engine took a contra/
    elimination line tagged under `Revenues`; the real top line sits in `SalesRevenueNet`).
  - **Take-larger-total / ASC-606 guard** — BE undercounts (1,268,782 vs GT 1,333,470) because
    `RevenueFromContractWithCustomer…` carries only ASC-606 contract revenue and excludes BE's
    financing/electricity streams; the presented "Total revenue" is larger and is the answer.
  - **Reject zero / segment fragment** — CHDN 2013–15 returned 0 (a zero/segment line won the
    priority walk). Never accept a 0 revenue when a larger revenue total exists in the accession.

**2. Pretax tag priority.** TRC 2016: pilot −6,247K vs GT +851K. The priority-1 tag
(`…BeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments`) *excludes*
equity-method income; the income-statement "Income before income taxes" *includes* it.
Reorder so the full-pretax concept (`…BeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest`)
wins, or define pretax as the income-statement line that nets to NI + tax.

**3. Cash fallback chain (known-scope).** Cash is blank for CMC (all years), MGLN, MKSI, IMKTA —
the pilot's 3-tag list is too thin. Port the legacy's chain: combined cash+restricted **minus**
restricted, and bare `Cash`. *Policy note:* MGLN's balance-sheet "Cash" line is restricted-
inclusive (325,249 with 146,455 restricted) → the policy value is the **178,794 excl-restricted**,
which requires the derivation. (Ground-truth cell for MGLN recorded the gross 325,249 — see
GT-additions.)

**4. Total-debt assembly (known-scope).** Zeros for FN, TDOC, MGLN where debt exists — complete
the component assembly (incl. convertible notes / current-portion handling).

**5. Early-XBRL coverage hole.** 2010 (and some 2011) are blank across the board: the original
accession's facts often aren't in companyfacts for the 2009–11 era. *Largely outside the needed
window* (2015 snapshot − 3yr lookback = 2012). Mitigation if wanted: when the original accession
carries no facts for a year, fall back to the **earliest-filed** accession that does (closest to
original) — recovers 2010–11 without reintroducing vintage blending.

**6. Up-C / complex-NCI net income.** AMRX 2024: pilot −73,876K (NetIncomeLoss = parent slice)
vs GT −116,886K ("net loss before accretion of redeemable NCI"). Genuine definitional question
for Up-C structures — needs a policy sub-rule, not a silent pick. *(Notably the pilot's value may
be the policy-correct parent figure; GT chose a non-standard line. Decision required.)*

## Ground-truth additions / clarifications needed

- **NNBR 2019 & 2021 equity** — add them. As-filed reproduces `StockholdersEquity` = 2,839K (2019)
  and −31,902K (2021), which can't be real next to 254,152K (2020). This looks like a *filer XBRL
  mis-tag in the original* (DERA's as-reported was ~353M / ~224M). It's the one case proving
  as-filed faithfully carries original-filing defects — we need the 10-K face value to confirm and
  drive the repair layer.
- **MGLN 2019 cash** — confirm the policy value is 178,794K (excl restricted), not the 325,249K
  gross line recorded in the template.

## Conclusion

The pilot proves the spine and, just as importantly, **the diff is now a precise specification for
the production engine**: port the concept-definition heuristics (revenue total-selection, cash,
debt, pretax tag), add the early-XBRL earliest-filed fallback, and the vintage machinery stays
deleted. No surprises that aren't explained. This is "clean enough to proceed to the production
build" — not yet "clean enough to scale to 3,400," which comes after fixes 1–4 land and re-pass
the regression set.

---

## Revision v2 results — re-scored vs ground truth (112 cells)

Fixes implemented and validated live: pretax priority reorder; equity identity repair
(parent = inclNCI − NCI); cash combined-minus-restricted derivation; debt assembly; early-XBRL
earliest-filed fallback (time-bounded to 540 days so retro-tags can't contaminate);
**revenue = the `Revenues` total-revenues element when tagged** (it is the presented total —
larger than RFCwC for non-606-revenue filers like BE, smaller for filers netting a derivative
line like PDCE; either way it is the reported total); bank carve-out only when no `Revenues`
line exists (keeps StoneX-type financials off it); zero-asset shell years blanked.

**Score: 105 / 112 (93.8%). Zero clean pilot bugs.** The 7 residuals:

| Cell | Category | Disposition |
|---|---|---|
| CMC 2024 revenue | **GT data error** | GT dropped a digit (7,925,972 is correct) — pilot right |
| MGLN 2019 cash | **GT stale** | excl-restricted 178,794 confirmed correct — pilot right |
| MKSI 2024 cash | **GT recorded gross** | pilot 420,000 = excl-restricted (policy); GT = combined line |
| MKSI 2024 net income | **Re-verify** | XBRL `NetIncomeLoss`=`ProfitLoss`=190M at original accn vs GT 38M |
| AMRX 2024 net income | **Definitional (Up-C)** | pilot `NetIncomeLoss` (parent) is §2.2-correct; GT chose "before NCI accretion" |
| BE 2024 net income | **Definitional** | pilot parent NI is §2.2-correct; GT chose "attributable to common" |
| PLXS 2024 revenue | **Genuine override** | anomalous tagging (RFCwC-excl > RFCwC-incl, no `Revenues`); needs a value override |

Net: the engine is correct on ~110/112 cells once GT data errors are fixed and the two
policy-correct definitional choices are credited; **PLXS 2024 is the single genuine override**
and **MKSI 2024 net income needs a 10-K re-check**. Known architectural item deferred to
production: a small set of filers where even `Revenues` is absent and the contract tag is
mis-presented (PLXS) needs the presentation linkbase (pre.txt) or an override — flagged via
`revenue_FACE_FLAG`, not silently chosen.

**Verdict: clean enough to scale.** Recommended gate before the full ~3,400 run: (a) correct
the 3 GT cells, (b) record the PLXS override, (c) re-verify MKSI net income.
