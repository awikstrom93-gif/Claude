# Russell 2000 Growth — Index Quality Study
## Fundamental Data Definitions Policy (v1.0)

**Purpose.** A single, written standard for *what each fundamental field means*, *which
XBRL concept implements it*, and *how disagreements are resolved*. It exists so that the
pipeline‑vs‑DERA reconciliation stops being a per‑row judgment call: every recurring
"DIFF" maps to a documented decision here. Apply this policy **identically** to whichever
extraction source is used (companyfacts pipeline, DERA/FSDS, or per‑accession filings) —
the source is an implementation detail; the *definition* is the contract.

---

### 0. Standards basis

This is a **longitudinal fundamental‑quality characterization of the constituents of an
equity style index**. The governing conventions are therefore:

1. **Vendor standardization conventions** — S&P Compustat, S&P Capital IQ, and FactSet
   Fundamentals all map heterogeneous filer line items onto a *single standardized concept*
   per metric (e.g. Compustat `REVT`, `NI`, `SEQ`, `DT`). We mirror that discipline: one
   economic concept per field, with a priority list of acceptable XBRL tags that all
   express it.
2. **CFA Institute analytical conventions** — for *derived* ratios: average balance‑sheet
   denominators in return measures (ROE, ROA, ROIC), DuPont consistency between numerator
   and denominator, and NOPAT‑based ROIC.
3. **Point‑in‑time / as‑reported discipline** — the standard for any index‑composition or
   backtest study: each constituent‑year reflects what was *knowable at the snapshot*
   (as originally filed), never hindsight restatements. This is why as‑first‑reported
   databases exist.
4. **Index‑provider factor definitions** — FTSE Russell constructs the Russell 2000 Growth
   universe and its style scores from sales‑per‑share growth, medium‑term forecast growth,
   and value characteristics; its quality/profitability work keys off return‑on‑capital and
   margins. Our metric choices are kept consistent with how the index itself is built so the
   study describes the index on its own terms.

**Master rule (numerator/denominator consistency).** Wherever a flow is divided by a stock
(ROE, ROIC, ROA), both must be measured at the **same level of the corporate hierarchy**
(parent/controlling interest) and the **same claimants** (see Net Income and Equity). This
single rule decides most of the observed DERA‑vs‑pipeline forks.

---

### 1. Cross‑cutting policies

**1.1 Vintage — as‑originally‑reported, repair‑defects‑only.**
Each constituent‑year uses the value as stated in that period's **original 10‑K** (the
first‑filed 10‑K for that fiscal‑year‑end). A later vintage (10‑K/A, or the value as a
comparative in a subsequent filing) is adopted **only** to repair a *defect in the original
XBRL* — a units/scale typo, a sign error, or a segment/fragment mis‑tag. A genuine
**restatement of the business** is never absorbed; it is left as originally reported and, if
needed, surfaced for review.
*Rationale:* restatements cluster in low‑quality/troubled names — exactly the cohort whose
evolution this study measures — so importing hindsight corrections would bias the index's
apparent quality upward. The market, the index constructor, and investors all saw the
original numbers.

**1.2 Point‑in‑time selection (FY0).**
For an April‑30 snapshot, a constituent's FY0 is its latest fiscal year **whose 10‑K had
actually been filed by the snapshot date** (use the filing/acceptance date, not a fixed
lag). FY‑1/FY‑2/FY‑3 are the consecutive prior years for growth and persistence. All
trailing history is restricted to years ≤ FY0 (no look‑ahead).

**1.3 Return‑ratio denominators use averages.**
ROE, ROA, and ROIC use the **average of opening and closing** balance‑sheet values
(½·(FY0 + FY‑1)). This is the CFA‑standard treatment and matters here because high‑growth
small caps raise equity during the year, so ending equity understates ROE for exactly the
fast‑growth names that define the index.

**1.4 Financial sectors are handled separately.**
Banks, insurers, broker‑dealers, and REITs do not have a comparable "revenue → COGS →
operating income → margin" stack. For these: revenue is defined sector‑specifically (§2.1),
and gross profit / operating margin are **left blank, not zero** (a blank is excluded from
aggregates; a zero would corrupt them).

**1.5 Currency, scale, sign.**
Values are in **reported presentation currency** (USD for domestic filers; foreign‑filer
currency is flagged and not silently mixed into USD aggregates). Values are in **raw
dollars**; an obvious thousands/millions units typo is repaired against the company's own
scale. Definitionally non‑negative fields (capex, total debt) are sign‑normalized;
sign‑variable fields (net income, tax, equity, pretax) keep their reported sign.

**1.6 Missing vs zero.**
A field that the filer does not report is **blank**, never 0 — except **total debt**, where
a company with a full balance sheet and no debt tag is treated as **debt‑free (0)**, because
the cash‑rich, equity‑financed majority of this universe genuinely carries no debt and
dropping them would overstate index leverage.

---

### 2. Income‑statement fields

**2.1 Revenue (Total Net Revenue).**
*Definition:* the consolidated **top line from continuing operations** as presented — net
revenue / net sales — standardized across filer types (Compustat `REVT` analogue).
*Selection:*
- **Non‑financials:** the income‑statement top line. Post‑ASC‑606: `RevenueFromContract
  WithCustomerExcludingAssessedTax`; pre‑606 or non‑606 streams: `Revenues`,
  `SalesRevenueNet`, `SalesRevenueGoodsNet`, with industry top‑line fallbacks. **Take the
  filer's presented consolidated total**, not a segment/component, and not a smaller generic
  `Revenues` line when a larger reported total (`SalesRevenueNet`) is the actual top line
  (the Eastman Kodak / Federal Signal fork → **the true top line wins**).
- **Banks:** Total revenue = **net interest income + total noninterest income**
  (`InterestIncomeExpenseNet` + `NoninterestIncome`; NII may be built from total interest
  income − total interest expense). This is the analyst‑standard bank top line and
  **supersedes** any `RevenueFromContractWithCustomer…` fragment a bank happens to tag (the
  288‑row bank fork → **DERA's NII+noninterest construction wins**).
- **Insurers:** Total revenues = premiums earned + net investment income + fee/other income.
- **No‑revenue companies** (e.g. clinical‑stage biotech with no product sales): revenue is
  **blank**, and "% of index with revenue" is reported as its own series.

**2.2 Net Income (Net Income Attributable to Parent).**
*Definition:* consolidated net income **attributable to the controlling (parent) interest,
after non‑controlling interest, before preferred dividends** (Compustat `NI`).
*Selection:* `NetIncomeLoss` (parent) is primary; `ProfitLoss` (total incl. NCI) is a
fallback **only** when a filer tags nothing else.
*Resolves the largest net‑income fork:* DERA's `ProfitLoss` (includes NCI) and
`NetIncomeLossAvailableToCommonStockholdersBasic` (deducts preferred dividends) are **both
rejected** for this field, because:
- it must match the **parent equity** denominator in ROE (Master rule);
- "available to common" is the *EPS numerator*, not the profitability/ROE numerator;
- profitability flags ("is the company profitable") use total net income to the parent, the
  standard screen.
*(`…AvailableToCommon` is retained only if a future per‑share analysis is added.)*

**2.3 Operating Income (EBIT, operating basis).**
*Definition:* income from operations = revenue − operating costs (COGS + SG&A + other
operating), before interest and tax. `OperatingIncomeLoss`. Blank for banks/insurers (§1.4).

**2.4 Gross Profit.**
*Definition:* revenue − cost of goods/services sold. `GrossProfit`, only where the filer
presents a gross‑profit line (many services/financial filers do not — leave blank).

**2.5 Pretax Income (EBT, continuing operations).**
*Definition:* income from **continuing operations before income taxes**
(`IncomeLossFromContinuingOperationsBeforeIncomeTaxes…`). Used for the effective tax rate
and ROIC's NOPAT. Most pretax DIFFs are vintage, not definitional (§1.1 applies).

**2.6 Income Tax Expense.**
*Definition:* total income tax provision on continuing operations
(`IncomeTaxExpenseBenefit`). Paired with §2.5 for the effective rate. Where pretax ≤ 0,
ROIC's tax adjustment uses the 21% statutory rate (loss‑year benefit not annualized).

---

### 3. Balance‑sheet fields

**3.1 Stockholders' Equity (Parent / Common + Preferred).**
*Definition:* total equity **attributable to the parent (controlling interest), excluding
NCI** (Compustat `SEQ`). `StockholdersEquity` primary; LLC/LP equivalents (`MembersEquity`,
`PartnersCapital`) for non‑corporate filers.
*Resolves the equity fork:* DERA's `…IncludingPortionAttributableToNoncontrollingInterest`
is **rejected** — it would mismatch the parent net‑income numerator and overstate the ROE
denominator (Master rule). Use the **average** of FY0 and FY‑1 in ROE (§1.3).

**3.2 Total Assets.** `Assets`. Average of FY0/FY‑1 where used as a ratio denominator.

**3.3 Cash & Cash Equivalents (excl. restricted).**
*Definition:* unrestricted cash and equivalents available to fund operations
(`CashAndCashEquivalentsAtCarryingValue`).
*Resolves the cash fork:* **exclude restricted cash** — DERA's combined
`Cash…RestrictedCash…` line and the bare `Cash` (cash without equivalents) are **rejected**.
Restricted cash is captured in its own field and excluded from liquidity, matching how
biotechs report their own runway. *(Including restricted cash would inflate liquidity and
cash‑runway metrics for exactly the cash‑burning cohort under study.)*

**3.4 Short‑term & Long‑term Investments (marketable securities).**
*Definition:* liquid marketable securities, current (`short_term_investments`) and
noncurrent (`long_term_investments`), used to complete cash‑runway liquidity (cash + STI +
LTI). Assembled from a roll‑up tag where present, else a de‑duplicated sum of AFS / HTM /
trading / equity‑security components. Excludes illiquid strategic/equity‑method stakes.

**3.5 Restricted Cash.** Current + noncurrent restricted cash, tracked separately and
**excluded** from cash and from runway liquidity (surfaced for an optional broader view).

**3.6 Total Debt (interest‑bearing).**
*Definition:* total interest‑bearing debt = short‑term borrowings + current portion of
long‑term debt + long‑term debt, **including finance/capital‑lease obligations** (Compustat
`DT = DLC + DLTT`). Assembled from components to avoid double counting.
*Scope note:* **operating‑lease liabilities (post‑ASC‑842) are excluded** from total debt —
the standard convention for leverage ratios; including them would create a 2019 break in the
leverage series unrelated to financing. Missing‑debt → 0 only with a present balance sheet
(§1.6).

---

### 4. Cash‑flow fields

**4.1 Operating Cash Flow (CFO).**
*Definition:* net cash provided by operating activities — the **total** CFO subtotal,
including discontinued operations (`NetCashProvidedByUsedInOperatingActivities`).
*Resolves the CFO fork:* DERA's `…ContinuingOperations` variant is **rejected** in favor of
the total, because total CFO is what funds the enterprise and is the FCF basis below
(291‑row fork → **pipeline's total wins**).

**4.2 Capital Expenditures.**
*Definition:* cash purchases of property, plant & equipment and other productive long‑lived
assets (Compustat `CAPX`). For **multi‑line capital spenders** (REITs, E&P, homebuilders)
capex is the **sum of the capital‑investment lines** (e.g. PP&E + development + capital
improvements + oil‑&‑gas property), not a single tag.
*Resolves the capex fork:* DERA's **summed** construction is adopted as the standard for
multi‑line filers (the 170/57/56‑row capex forks); single‑tag extraction is the special
case, not the rule. Sign‑normalized to a positive outflow.

**4.3 Free Cash Flow (simple / levered).**
*Definition:* FCF = **CFO − Capex**. Because US‑GAAP CFO is already after cash interest paid
and after cash taxes, this is a *levered, after‑tax* free cash flow — cash available to
equity holders and for debt paydown after maintaining the asset base. Computed only where CFO
is present; capex absent is treated as 0 but kept visible.
*Why levered, not FCFF:* both of this study's FCF use‑cases want the levered number — (a) the
biotech **cash‑runway/burn** metric must reflect *actual* cash leaving the firm including debt
service (FCFF would add interest back and overstate runway), and (b) the **"GAAP‑unprofitable
but cash‑generative" cohort flag** is an inherently levered self‑funding question. FCFF
(`CFO + interest×(1−tax) − Capex`) is also low‑value here: the universe is mostly debt‑free, so
FCFF ≈ simple FCF for most names, while the interest/effective‑tax inputs add coverage loss and
loss‑year noise. If a capital‑structure‑neutral cross‑sectional quality comparison is later
wanted, add **FCFF as a secondary field**, do not replace the headline.
*Caveat (applies to CFO and both FCF measures equally):* US‑GAAP CFO **adds back stock‑based
compensation**, which is large for growth/biotech constituents — so reported CFO/FCF flatters
true cash earnings for SBC‑heavy names. This does not affect the simple‑vs‑FCFF choice but
should be surfaced in the README.
*IFRS note:* IFRS permits interest paid in *financing* rather than operating, so foreign‑filer
CFO/FCF requires normalization before mixing with US‑GAAP names.

---

### 5. Derived quality ratios

| Ratio | Definition | Standard basis |
|---|---|---|
| **Operating margin** | Operating income ÷ Revenue | margin analysis; non‑financials only |
| **ROE** | Net income (parent) ÷ **average** parent equity | CFA/DuPont; positive‑equity guard |
| **ROIC** | NOPAT ÷ **average** invested capital, NOPAT = Operating income × (1 − effective tax rate); Invested capital = total debt + parent equity − unrestricted cash | CFA ROIC; clamp \|ROIC\| at 500% on tiny denominators |
| **Debt/Equity** | Total debt ÷ parent equity | leverage |
| **Debt/Capital** | Total debt ÷ (total debt + parent equity) | leverage |
| **Revenue growth** | YoY and 3‑yr CAGR, as‑originally‑reported | reported on same‑constituent and aggregate bases |
| **Earnings growth** | YoY net income, as‑originally‑reported | guard against negative‑base CAGR |

**Aggregation.** Every index‑level ratio is reported three ways — weight‑weighted average,
equal‑weight median (with IQR), and dollar‑aggregate (Σ numerator ÷ Σ denominator) — so no
single distortion dominates. Per‑company ratios are winsorized before weight‑averaging;
dollar levels are never winsorized.

---

### 6. Fork‑resolution lookup (for the reconciliation harness)

When a pipeline‑vs‑DERA cell disagrees, classify and act:

1. **Same XBRL tag, different value** (`tag_agree = Y`) → **vintage**. Adjudicate against the
   original‑10‑K accession (as‑first‑reported). Pipeline drift to a restated value loses;
   DERA on a different period loses. *(Deterministic — no filing review.)*
2. **Different tag, both on a recognized concept for the field** → **definitional**. Apply
   §2–§4. The tables in this document already name the winner for every high‑frequency fork.
   *(Policy lookup — no filing review.)*
3. **Neither matches the original face / scale or sign anomaly** → **genuine defect**. Route
   to the short manual queue; repair under §1.1.

Buckets 1 and 2 are auto‑resolvable and cover the large majority of the ~9.6k both‑present
disagreements. Only bucket 3 reaches a human.

---

### 7. Known deviations & open items

- **Operating leases** excluded from total debt (§3.6) — revisit only if a lease‑inclusive
  leverage view is wanted.
- **`NetIncomeLossAvailableToCommonStockholdersBasic`** is not used for profitability/ROE
  (§2.2); reintroduce only for a dedicated per‑share/earnings‑to‑common analysis.
- **DERA‑only coverage gaps** (~6.4k cells where the pipeline is blank but DERA has a value)
  are a *completeness* item, tracked separately from value disagreements.
- **FCFF** is an optional secondary field, not the headline FCF (§4.3); add only if a
  leverage‑neutral quality comparison is needed.
- **Stock‑based compensation add‑back** inflates CFO/FCF for SBC‑heavy growth/biotech names
  (§4.3); surface this in the README and consider an SBC‑adjusted CFO view if it materially
  moves the cash‑generative cohort.
- **Foreign filers / IFRS** mapped parent‑first to mirror these definitions; reconciled
  against the same standard.
