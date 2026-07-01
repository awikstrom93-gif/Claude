"""
============================================================
r2k_dera_classify.py  --  the accounting brain (engine 2b). Reads dera_facts.csv (reconstructed
statement lines) and turns each filing into a standardized full-cascade fundamentals record,
validated by accounting identities, with a per-filing tie-out report and confidence flag.
============================================================
PRINCIPLES
  * map tags -> standardized roles by a role dictionary + sector template (commercial / bank /
    insurer), chosen from the tags actually present (self-contained; SIC optional).
  * DERIVE missing subtotals from identities (GP = Rev - COGS; OI = GP - OpEx; EBITDA = OI + D&A;
    consolidated NI = Pretax - Tax; parent NI = consolidated - NCI), and VALIDATE wherever both a
    reported and a derived value exist -- a mismatch is flagged, not hidden.
  * NEVER zero-fill: a role with no tag and no derivation stays blank (None), never 0.

OUTPUT
  fundamentals_dera.csv   one row per (cik, fiscal_year): the standardized cascade + provenance
                          (which tag/derivation each value came from) + confidence
  tieout_report.csv       per filing: each identity checked, tied/broken, and the residual

RUN:  python r2k_dera_classify.py
SELFTEST: python r2k_dera_classify.py --selftest   (synthetic industrial + bank)
============================================================
"""
from pathlib import Path
import os, csv, sys, re
from collections import defaultdict

import r2k_validated_tags as vt

BASE = Path(os.environ.get("R2KG_BASE", "."))
FACTS = BASE / "dera_facts.csv"
INDEX = BASE / "dera_filing_index.csv"          # optional, for SIC
OUT = BASE / "fundamentals_dera.csv"
TIEOUT = BASE / "tieout_report.csv"
TOL_REL, TOL_ABS = 0.005, 5000.0                # identity tie tolerance (BS / IS -- should tie exactly)
CF_TOL_REL, CF_TOL_ABS = 0.01, 2_000_000.0      # cash-flow legs -- materiality (reconciliation noise)
CF_TOL_CASH = 0.02                              # cash-flow legs also tie within 2% of the CASH balance:
# CF_FOOT compares the NET CHANGE in cash, so a small FX/M&A residual on a large cash position trips
# the abs floor though it is immaterial to the cash statement. A 2%-of-cash floor clears that noise.
# reported but NOT counted toward confidence: these break for legitimate, identifiable reasons
# rather than data error -- CF_BS_CASH (restricted-cash tag coverage), RE_ROLL (cumulative-effect
# accounting adoptions / declared-vs-paid dividend timing / treasury retirements). They are
# high-value diagnostics to surface, not trust penalties.
NON_GATING = {"CF_BS_CASH(CFend=cash+restr)", "RE_ROLL(RE[t]=RE[t-1]+NI-Div)",
              "DA_CONSISTENCY(IS=CF)",   # IS vs CF D&A legitimately differs by presentation
              "OI_PRETAX(OI+nonop=Pretax)",   # non-operating section is filer-specific
              "PPE_ROLL(PPE[t]=PPE[t-1]+capex-dep)",   # disposals/M&A/impairment break it legitimately
              # current/non-current split: BS_FOOTS already gates the balance sheet; these break on
              # held-for-sale / presentation quirks where the non-current subtotal is tagged, so they
              # are diagnostics, not trust penalties.
              "BS_ASSETS(cur+noncur=total)", "BS_LIAB(cur+noncur=total)"}


def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def first(d, *tags):
    """first present tag value + the tag name; (None, None) if none present."""
    for t in tags:
        if t in d and d[t] is not None:
            return d[t], t
    return None, None


def _close(a, b, rel=TOL_REL, ab=TOL_ABS):
    return a is not None and b is not None and abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def cands(d, *tag_lists):
    """all present as-filed candidate values for a role, in tag-PRIOR order (canonical first).
    Each is (value, tag) -- the tag carries the meaning; the identity will pick among them the value
    that articulates, and this priority order breaks ties the accounting can't."""
    out, seen = [], set()
    for tags in tag_lists:
        for t in tags:
            if t in d and d[t] is not None and t not in seen:
                out.append((d[t], t)); seen.add(t)
    return out


def select_articulating(role, current_val, current_tag, candidates, foots):
    """TAG-INFORMED, IDENTITY-DRIVEN selection. `candidates` (value,tag) are the as-filed values the
    tag says are eligible for this role; `foots(v)` returns True if choosing v makes the identity
    articulate. Keep the current pick if it already foots; else choose the first candidate (highest
    tag prior) that does. Returns (value, tag, source) -- source records what selected it."""
    if current_val is not None and foots(current_val):
        return current_val, current_tag, "tag+identity"        # tag pick already articulates
    for v, t in candidates:
        if foots(v):
            return v, t, "identity-selected"                   # the value that makes it foot
    return current_val, current_tag, "tag-only(unresolved)"     # nothing articulates -> flag


# ---- candidate tags per standardized role (us-gaap; IFRS variants appended) ----
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
       "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenueFromContractsWithCustomers",
       "Revenue", "RevenueLossFromContractWithCustomerIncludingAssessedTax",
       # sector top-line tags verified as-filed vs the target (r2k_gap_tags.py), LOW priority so the
       # canonical tags above always win: lessor lease income, insurance-broker commissions, asset-
       # manager advisory fees, financial interest+dividend income -- fill names the standard list missed.
       "OperatingLeaseLeaseIncome", "InsuranceCommissionsAndFees",
       "InvestmentAdvisoryManagementAndAdministrativeFees", "InterestAndDividendIncomeOperating"]
COGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfGoods",
        "CostOfServices", "CostOfSales"]
# curated operating-revenue lines (a GROSS top line, positive). Used only to recover a real revenue
# when the selected tag is net-NEGATIVE (an insurer/holdco "Revenues" swamped by investment losses).
# Deliberately NOT a broad pattern -- must exclude gains (GainLossOnSalesOf...) that aren't top line.
REV_OPERATING = ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                 "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                 "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "PremiumsEarnedNet",
                 "OperatingLeasesIncomeStatementLeaseRevenue",
                 "OperatingAndCapitalLeasesIncomeStatementLeaseRevenue", "RealEstateRevenueNet",
                 "RoyaltyRevenue", "RegulatedAndUnregulatedOperatingRevenue",
                 "InterestAndDividendIncomeOperating"]
# COGS is frequently SPLIT across lines: a base cost line + separately-struck services cost,
# depreciation/amortization inside COGS, or restructuring/impairment charged to COGS. Summed onto the
# base only when it reconciles revenue - GP (and the base alone does not), so a base line that already
# aggregates them is never double-counted. The IS_GP gap the probe most often points at.
COGS_EXTRA = ["CostOfServices", "CostOfServicesLicensesAndServices", "CostOfGoods",
              "CostOfGoodsSoldAmortization", "CostOfGoodsSoldDepreciation",
              "CostOfGoodsSoldDepreciationAndAmortization",
              "CostOfGoodsAndServicesSoldAmortization", "CostOfGoodsAndServicesSoldDepreciation",
              "CostOfGoodsAndServicesSoldImpairmentCharges", "CostOfImpairmentOfIntangibleAssets",
              "CostOfGoodsSoldRestructuringCharges", "CostofGoodsSoldRestructuringCharges",
              "RestructuringCostsCostOfGoodsSold"]
OPEX = ["OperatingExpenses", "OperatingCostsAndExpenses", "CostsAndExpenses"]
OINC = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities",
        # EBIT = income before INTEREST and taxes == operating income (verified via r2k_gap_tags.py).
        # NOT the plain "...BeforeIncomeTaxes" pretax line (that includes non-operating items).
        "IncomeLossFromContinuingOperationsBeforeInterestExpenseInterestIncomeIncomeTaxesExtraordinaryItemsNoncontrollingInterestsNet"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic", "ProfitLossBeforeTax"]
TAX = ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseBenefitContinuingOperations",
       "IncomeTaxExpenseContinuingOperations", "CurrentIncomeTaxExpenseBenefit"]
NI_PARENT = ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent"]
NI_CONSOL = ["ProfitLoss", "NetIncomeLossIncludingPortionAttributableToNoncontrollingInterest"]
NI_COMMON = ["NetIncomeLossAvailableToCommonStockholdersBasic"]
# equity-method earnings (may sit ABOVE or BELOW the tax line -- both are valid GAAP) and the
# reported after-tax continuing-operations subtotal, used to make the IS_NI cascade tie either way.
EQUITY_METHOD = ["IncomeLossFromEquityMethodInvestments",
                 "IncomeLossFromContinuingOperationsAfterEquityMethodInvestments"]
INC_CONT_AFTERTAX = ["IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest",
                     "IncomeLossFromContinuingOperations"]
DISC_OPS = ["IncomeLossFromDiscontinuedOperationsNetOfTax",
            "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity",
            "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTax"]
# discontinued operations total = income during the period + gain/loss on disposal. Filers often
# report these as TWO lines; prefer a reported total, else SUM the operating + disposal components
# (the disposal gain is the biggest IS_NI gap the probe found).
DISC_TOTAL = ["IncomeLossFromDiscontinuedOperationsNetOfTax",
              "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity",
              "ProfitLossFromDiscontinuedOperations"]
DISC_OP_PART = ["DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTax",
                "DiscontinuedOperationIncomeLossFromDiscontinuedOperationDuringPhaseOutPeriodNetOfTax"]
DISC_DISPOSAL = ["DiscontinuedOperationGainLossOnDisposalOfDiscontinuedOperationNetOfTax",
                 "DiscontinuedOperationAmountOfOtherIncomeLossFromDispositionOfDiscontinuedOperationNetOfTax"]
# REIT gains on sale of real estate, struck BELOW the pre-tax subtotal -> a below-the-line bridge
# item to consolidated NI (like equity-method earnings). REITs are largely untaxed, so the net-of-tax,
# before-tax, and generic variants all flow ~dollar-for-dollar to NI; offered as an addend that the
# IS_NI cascade adopts only when it reconciles to the reported consolidated NI (the next IS_NI gap).
PROP_GAIN = ["GainLossOnSaleOfPropertiesNetOfApplicableIncomeTaxes",
             "GainLossOnSaleOfPropertiesBeforeApplicableIncomeTaxes",
             "GainLossOnSaleOfProperties"]
NCI_IS = ["NetIncomeLossAttributableToNoncontrollingInterest",
          "ProfitLossAttributableToNoncontrollingInterests"]
# NCI income attribution is frequently SPLIT across lines: the main attribution + a separately-struck
# redeemable / operating-partnership / subsidiary / disc-ops NCI (redeemable NCI lives in mezzanine
# per ASC 480, so its income share is often on its own line). Summed only when it reconciles
# consol - parent (and the bare main line does not), so a main line that already aggregates them is
# never double-counted. The IS_NCI gap the probe most often points at.
NCI_EXTRA = ["NetIncomeLossAttributableToRedeemableNoncontrollingInterest",
             "NoncontrollingInterestInNetIncomeLossOperatingPartnershipsRedeemable",
             "NoncontrollingInterestInNetIncomeLossOperatingPartnershipsNonredeemable",
             "NoncontrollingInterestInNetIncomeLossJointVenturePartnersRedeemable",
             "MinorityInterestInNetIncomeLossJointVenturePartners",
             "NetIncomeLossAttributableToNoncontrollingInterestOfSubsidiary",
             "NetIncomeLossAttributableToNoncontrollingInterestConsolidatedEntities",
             "NetIncomeLossAttributableToNonredeemableNoncontrollingInterest",
             "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToNoncontrollingInterest"]
PREF_DIV = ["PreferredStockDividendsIncomeStatementImpact", "PreferredStockDividendsAndOtherAdjustments"]
DA = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
      "DepreciationAndAmortization", "DepreciationAmortizationAndDepletion", "Depreciation"]
INT_EXP = ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating",
           "InterestAndDebtExpense", "InterestExpenseOperating"]
# balance sheet
CASH = ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashAndCashEquivalents",
        "CashCashEquivalentsAndShortTermInvestments", "CashEquivalentsAtCarryingValue",
        # ASU 2016-18 combined line (cash + restricted), used ONLY when no pure-cash tag is present.
        # For these filers restricted cash is immaterial (verified ~0% vs target, r2k_gap_tags.py);
        # cash_total below is guarded so restricted is not double-counted when cash came from this tag.
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations"]
STI = ["ShortTermInvestments", "OtherShortTermInvestments", "AvailableForSaleSecuritiesCurrent"]
ASSETS = ["Assets"]
ASSETS_CUR = ["AssetsCurrent"]
ASSETS_NC = ["AssetsNoncurrent"]
LIAB = ["Liabilities"]
LIAB_CUR = ["LiabilitiesCurrent"]
LIAB_NC = ["LiabilitiesNoncurrent"]
PPE_NET = ["PropertyPlantAndEquipmentNet"]
# operating-income -> pretax bridge (the non-operating section). Prefer the reported aggregate net
# non-operating; else build from interest expense + interest income + other non-operating items.
NONOP_AGG = ["NonoperatingIncomeExpense"]
NONOP_OTHER = ["OtherNonoperatingIncomeExpense", "OtherNonoperatingGainsLosses",
               "NonoperatingGainsLosses", "OtherNonoperatingIncome"]
INT_INCOME = ["InvestmentIncomeInterest", "InterestIncomeOther", "InvestmentIncomeNonoperating"]
EQ_PARENT = ["StockholdersEquity", "PartnersCapital", "MembersEquity", "CommonStockholdersEquity"]
EQ_INCL = ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
           "PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest"]
NCI_BS = ["MinorityInterest"]
REDEEM_NCI = ["RedeemableNoncontrollingInterestEquityCarryingAmount",
              "RedeemableNoncontrollingInterestEquityOtherCarryingAmount",
              "RedeemableNoncontrollingInterestEquityCommonCarryingAmount",
              "RedeemableNoncontrollingInterestEquityPreferredCarryingAmount",
              "NoncontrollingInterestRedeemable",
              "RedeemableNoncontrollingInterestEquityFairValue"]
TEMP_EQUITY_TOTAL = ["TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"]
TEMP_EQUITY_PARENT = ["TemporaryEquityCarryingAmountAttributableToParent", "TemporaryEquityCarryingAmount"]
# components that legitimately sit between liabilities and equity, or are NCI/preferred not yet in
# total equity. When A != L + E, the identity gives the GAP size; these tags name which line it is.
BS_GAP_EQUITY = ["MinorityInterest", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
                 # partnership / LLC noncontrolling interest (equity-class NCI) the probe found at the gap
                 "MembersEquityAttributableToNoncontrollingInterest", "MinorityInterestInPreferredUnitHolders",
                 "MinorityInterestInOperatingPartnerships", "MinorityInterestInLimitedPartnerships"]
BS_GAP_MEZZ = (TEMP_EQUITY_TOTAL + TEMP_EQUITY_PARENT + REDEEM_NCI +
               ["TemporaryEquityValueExcludingAdditionalPaidInCapital", "PreferredStockRedemptionAmount",
                "RedeemablePreferredStockCarryingAmount", "TotalMezzanineEquity",
                "PreferredStockValueNotIncludedInStockholdersEquity", "PreferredStockOfSubsidiaryValue"])
# temporary / redeemable equity (the mezzanine slot) is often under CUSTOM extension tags -- SPAC
# Class A shares "subject to possible redemption", redeemable preferred/common -- that a fixed list
# misses. Identify it structurally by name (like debt), so the BS_FOOTS gap can be filled from the
# as-filed custom tag. EXCLUDE per-share / share-count tags (not a dollar carrying amount).
TEMP_EQ_PAT = re.compile(r"temporaryequity|mezzanine|subjecttopossibleredemption|subjecttoredemption|"
                         r"redeemablecommon|redeemableconvertible|redeemablepreferred|"
                         r"mandatorilyredeemable|mandatoryredemption|"
                         r"redeemablenoncontrollinginterest|"          # redeemable NCI = mezzanine (ASC 480)
                         r"convertiblepreferred|preferredstockvalue|"  # custom preferred series outside equity
                         r"preferredstockofsubsidiary|warrantsandrights", re.I)
# EXCLUDE per-share / share-count tags (not a dollar carrying amount) AND the look-alike traps the
# broad probe surfaced: liquidation-preference disclosures and product-WARRANTY accruals (a liability,
# not mezzanine -- adding it would foot the sheet with the wrong classification).
TEMP_EQ_EXCL = re.compile(r"pershare|shares|numberof|pershareamount|fairvaluedisclosure|"
                          r"liquidationpreference|productwarranty|parorstated", re.I)
DEBT_LTNC = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
             "ConvertibleDebtNoncurrent", "ConvertibleNotesPayableNoncurrent", "SeniorNotesNoncurrent",
             "UnsecuredLongTermDebt", "NotesPayableNoncurrent"]
DEBT_LTTOT = ["LongTermDebt", "DebtLongtermAndShorttermCombinedAmount",
             "DebtAndCapitalLeaseObligations", "DebtAndCapitalLeaseObligation"]
DEBT_CUR = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent",
            "ShortTermBorrowings", "ConvertibleNotesPayableCurrent"]
LINE_OF_CREDIT = ["LongTermLineOfCredit", "LinesOfCreditCurrent"]
# cash flow
CFO = ["NetCashProvidedByUsedInOperatingActivities",
       "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
       "CashFlowsFromUsedInOperatingActivities"]
CFI = ["NetCashProvidedByUsedInInvestingActivities",
       "NetCashProvidedByUsedInInvestingActivitiesContinuingOperations"]
CFF = ["NetCashProvidedByUsedInFinancingActivities",
       "NetCashProvidedByUsedInFinancingActivitiesContinuingOperations"]
CAPEX = ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
         "PaymentsForCapitalImprovements", "PaymentsToAcquireMachineryAndEquipment",
         "PaymentsToAcquireOilAndGasPropertyAndEquipment"]
# cash-flow articulation: foot the CF, tie ending cash to the BS, roll cash across years.
# post-2018 filers reconcile cash+restricted, so the restricted-inclusive tags come first.
FX_CASH = ["EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
           "EffectOfExchangeRateOnCashAndCashEquivalents",
           "EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations"]
DCASH = ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
         "CashAndCashEquivalentsPeriodIncreaseDecrease",
         "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect"]
CF_END_CASH = ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"]   # CF ending balance (incl restricted)
RESTR_TOTAL = ["RestrictedCashAndCashEquivalentsAtCarryingValue", "RestrictedCashAndCashEquivalents",
               "RestrictedCash"]
RESTR_CUR = ["RestrictedCashCurrent", "RestrictedCashAndCashEquivalentsAtCarryingValueCurrent"]
RESTR_NC = ["RestrictedCashNoncurrent", "RestrictedCashAndCashEquivalentsNoncurrent",
            "RestrictedCashAndInvestmentsNoncurrent"]
# retained-earnings roll-forward (IS net income + CF dividends -> BS retained earnings) and
# D&A / SBC consistency (income statement add-back == cash-flow add-back).
RETAINED = ["RetainedEarningsAccumulatedDeficit", "RetainedEarningsAccumulatedDeficitLimitedPartnership"]
DIV_TOTAL = ["PaymentsOfDividends"]                      # total cash dividends paid (parent)
DIV_COMMON = ["PaymentsOfDividendsCommonStock"]
DIV_PREF = ["PaymentsOfDividendsPreferredStockAndPreferenceStock", "PaymentsOfDividendsPreferredStock"]
# operating D&A (for EBITDA + the IS<->CF diagnostic): depreciation/depletion + intangible
# amortization ONLY. Prefer a reported subtotal; else sum depreciation components plus a separately
# tagged intangible amortization. Deliberately NARROW -- generic "amortization" on the cash flow
# also covers deferred-cost / lease / debt-discount amortizations that are not operating D&A.
DA_SUBTOTAL = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
               "DepreciationAndAmortization", "DepreciationAmortizationAndDepletion"]
DA_DEP_PAT = re.compile(r"depreciation|depletion", re.I)   # catches DepreciationAndAmortization* too
DA_INTANGIBLE = ["AmortizationOfIntangibleAssets", "AmortizationOfAcquiredIntangibleAssets",
                 "AmortizationOfFiniteLivedIntangibleAssets", "AmortizationOfDevelopedTechnology"]
SBC_TAGS = ["ShareBasedCompensation", "ShareBasedCompensationExpense",
            "AllocatedShareBasedCompensationExpense", "StockBasedCompensation"]


def sum_da(facts):
    """operating depreciation & amortization: a reported D&A subtotal if present, else depreciation/
    depletion components plus a separately-tagged intangible amortization (narrow on purpose)."""
    for t in DA_SUBTOTAL:
        if facts.get(t) is not None:
            return facts[t]
    comps = [v for tg, v in facts.items() if v is not None and DA_DEP_PAT.search(tg)]
    intang = next((facts[t] for t in DA_INTANGIBLE if facts.get(t) is not None), None)
    # avoid double counting: only add intangible amortization if no component already covers it
    if intang is not None and not any("amortization" in tg.lower() for tg in
                                      [t for t, v in facts.items() if v is not None and DA_DEP_PAT.search(t)]):
        comps.append(intang)
    return sum(comps) if comps else None
# bank / insurer markers and lines
BANK_NII_NET = ["InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss"]
BANK_INT_INC = ["InterestAndDividendIncomeOperating", "InterestAndFeeIncomeLoansAndLeases"]
NONINT_INC = ["NoninterestIncome", "NoninterestIncomeOther"]
NONINT_EXP = ["NoninterestExpense"]
PROVISION = ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses",
             "ProvisionForLoanLossesExpensed"]
INS_PREMIUMS = ["PremiumsEarnedNet"]
INS_BENEFITS = ["BenefitsLossesAndExpenses", "PolicyholderBenefitsAndClaimsIncurredNet"]


# ============================================================================================
# STRUCTURAL DEBT RECONSTRUCTION  --  rebuild funded debt from the balance-sheet debt lines as
# filed, by PATTERN (so custom extension tags are caught, not just the standard taxonomy), with
# subtotal-preference to avoid double-counting. Funded debt INCLUDES finance/capital leases
# (analyst convention); operating leases are tracked separately for a lease-adjusted column.
# ============================================================================================
DEBT_INCL = re.compile(r"debt|borrow|notespayable|senior.?notes?|term.?loan|revolv|"
                       r"line.?of.?credit|lineofcredit|financingobligation|"
                       r"loanspayable|subordinat|mediumterm|commercialpaper|"
                       r"vehicleprogram|floor.?plan", re.I)   # incl fleet/floorplan (consolidated debt)
# NOTE: bare "convertible" was dropped from DEBT_INCL -- it swept convertible PREFERRED stock (equity)
# into debt. Convertible DEBT/notes still match via the debt/notespayable/senior/subordinated tokens.
LEASE_FIN_PAT = re.compile(r"(finance|capital)lease.*(liabilit|obligation)", re.I)
LEASE_OP_PAT = re.compile(r"operatinglease.*liabilit", re.I)
DEBT_EXCL = re.compile(r"securit|heldtomaturity|availableforsale|receivable|investment|"
                       r"repaymentsof|proceedsfrom|faceamount|fairvalue|interestrate|"
                       r"interestexpense|rightofuseasset|netinvestmentinlease|salestypelease|"
                       r"fixedmaturit|weightedaverage|allowance|unamortized|issuancecost|"
                       r"financingcost|covenant|redemption|conversion|numberof|percentage|"
                       r"paymentsof|accruedinterest|deferred|restrictedcash|grossnotes|noncash|"
                       # not debt: preferred/equity swept in by name; the ASSET side of secured-borrowing
                       # transfers; VIE sub-portions of a parent borrowings line; footnote carrying-amount
                       # roll-ups that overlap the balance-sheet debt lines (all caused debt>liabilities);
                       # the EQUITY component of a convertible note; and "liabilities OTHER THAN debt".
                       r"preferred|liquidation|capitalization|stockholdersequity|equitydeficit|andequity|"
                       r"variableinterestentity|assetscarryingamount|debtinstrumentcarryingamount|"
                       r"equitycomponent|otherthanlongtermdebt|otherthanlongtermdebtnoncurrent", re.I)
GRAND_LEASE_INCL = ["DebtAndCapitalLeaseObligations", "DebtAndCapitalLeaseObligation",
                    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]
GRAND_DEBT_ONLY = ["DebtLongtermAndShorttermCombinedAmount"]
LT_TOTAL_INCL_CUR = ["LongTermDebt"]                      # total LTD incl current maturities (excl ST/leases)
LT_NC = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]
LT_CUR = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent"]
STBORROW = ["ShortTermBorrowings", "OtherShortTermBorrowings", "CommercialPaper"]
FINLEASE_TOT = ["FinanceLeaseLiability", "CapitalLeaseObligations"]
FINLEASE_CUR = ["FinanceLeaseLiabilityCurrent", "CapitalLeaseObligationsCurrent"]
FINLEASE_NC = ["FinanceLeaseLiabilityNoncurrent", "CapitalLeaseObligationsNoncurrent"]
OPLEASE_TOT = ["OperatingLeaseLiability"]
OPLEASE_CUR = ["OperatingLeaseLiabilityCurrent"]
OPLEASE_NC = ["OperatingLeaseLiabilityNoncurrent"]
_SUBTOTAL = set(GRAND_LEASE_INCL + GRAND_DEBT_ONLY + LT_TOTAL_INCL_CUR + LT_NC + LT_CUR)
_LEASE_TAGS = set(FINLEASE_TOT + FINLEASE_CUR + FINLEASE_NC + OPLEASE_TOT + OPLEASE_CUR + OPLEASE_NC)


def _sum_present(bs, names):
    parts = [(n, bs[n]) for n in names if bs.get(n) is not None]
    return (sum(v for _, v in parts) if parts else None), [n for n, _ in parts]


def _is_current(tag):
    t = tag.lower()
    return ("current" in t) and ("noncurrent" not in t) and ("excludingcurrent" not in t)


def _dedup_components(leaves):
    """Drop debt double-counts among matched leaves:
      (a) PREFIX: a tag whose name extends another present tag's (SecuredDebt -> SecuredDebtNonrelated
          Party) is a component of that more-general total -> keep the total, drop the component;
      (b) GROSS: a 'Gross' line whose non-gross sibling is also present (OtherLongtermDebtGrossNoncurrent
          vs OtherLongTermDebtNoncurrent) -> keep the net carrying value, drop the gross;
      (c) DUP current/noncurrent: a current and noncurrent line of the same base with IDENTICAL value
          is a filer tagging error (the same amount tagged twice) -> drop the current one."""
    nm = {t: re.sub(r"[^a-z0-9]", "", t.lower()) for t in leaves}
    drop = set()
    for b in leaves:
        for a in leaves:
            if a == b or b in drop:
                continue
            # (a) prefix-component
            if len(nm[a]) >= 8 and len(nm[b]) > len(nm[a]) and nm[b].startswith(nm[a]):
                drop.add(b)
            # (b) gross vs net
            elif "gross" in nm[b] and nm[b].replace("gross", "") == nm[a]:
                drop.add(b)
            # (c) duplicate current/noncurrent with identical value
            elif (leaves[a] == leaves[b] and "current" in nm[b]
                  and nm[a].replace("noncurrent", "") == nm[b].replace("noncurrent", "").replace("current", "")):
                drop.add(b)
    return {t: v for t, v in leaves.items() if t not in drop}


def reconstruct_debt(bs):
    """bs: {tag: value} for the filing's BALANCE-SHEET facts (USD). Returns funded debt (incl
    finance leases), operating-lease liability, lease-adjusted total, and provenance. Never plugs a
    vendor value -- it sums the as-filed debt lines, preferring a reported subtotal over its parts."""
    prov = []
    fl, fln = first(bs, *FINLEASE_TOT)
    if fl is None:
        flc, _ = first(bs, *FINLEASE_CUR); flnc, _ = first(bs, *FINLEASE_NC)
        parts = [x for x in (flc, flnc) if x is not None]
        fl = sum(parts) if parts else None
    opl, _ = first(bs, *OPLEASE_TOT)
    if opl is None:
        oc, _ = first(bs, *OPLEASE_CUR); onc, _ = first(bs, *OPLEASE_NC)
        parts = [x for x in (oc, onc) if x is not None]
        opl = sum(parts) if parts else None

    leases_in_core = False
    core, src = first(bs, *GRAND_LEASE_INCL)
    if core is not None:
        leases_in_core = True; prov.append(src)
    else:
        core, src = first(bs, *GRAND_DEBT_ONLY)
        if core is not None:
            prov.append(src)
        else:
            ltt, ltsrc = first(bs, *LT_TOTAL_INCL_CUR)
            stb, ssrc = _sum_present(bs, STBORROW)
            if ltt is not None:                       # LongTermDebt already includes current maturities
                core = ltt; prov.append(ltsrc)
                if stb:
                    core += stb; prov += ssrc
            else:
                nonc, nsrc = first(bs, *LT_NC)
                cur, csrc = first(bs, *LT_CUR)
                if nonc is not None and cur is not None and nonc == cur:
                    cur, csrc = None, None     # identical current==noncurrent -> duplicate tag, drop current
                leaves = {}
                for t, v in bs.items():
                    if (v is None or t in _SUBTOTAL or t in _LEASE_TAGS or t in STBORROW
                            or LEASE_FIN_PAT.search(t) or LEASE_OP_PAT.search(t)):
                        continue
                    if DEBT_INCL.search(t) and not DEBT_EXCL.search(t):
                        leaves[t] = v
                leaves = _dedup_components(leaves)      # drop total/component double-counts
                leaves_nc = {t: v for t, v in leaves.items() if not _is_current(t)}
                leaves_cur = {t: v for t, v in leaves.items() if _is_current(t)}
                if nonc is not None:
                    prov.append(nsrc)
                else:
                    nonc = sum(leaves_nc.values()) if leaves_nc else None; prov += list(leaves_nc)
                if cur is not None:
                    prov.append(csrc)
                else:
                    cur = sum(leaves_cur.values()) if leaves_cur else None; prov += list(leaves_cur)
                parts = [x for x in (nonc, cur, stb) if x is not None]
                core = sum(parts) if parts else None
                if stb:
                    prov += ssrc

    funded = core
    if funded is not None and fl is not None and not leases_in_core:
        funded += fl; prov.append(fln or "FinanceLease")
    elif funded is None and fl is not None:
        funded = fl; prov.append(fln or "FinanceLease")
    # hard bound: funded debt is a SUBSET of total liabilities. If the reconstruction still exceeds
    # liabilities it is an unresolved over-capture (overlapping totals/components we cannot disentangle
    # by name -- e.g. a NonRecourseDebt total alongside its NonRecourseSecuredNotes) -> fall back to the
    # largest single as-filed debt line that itself fits within liabilities (a real, conservative value)
    # and flag it in the provenance rather than emit an impossible number.
    tl = bs.get("Liabilities")
    if tl is not None and tl > 0 and funded is not None and funded > 1.15 * tl:
        singles = [v for t, v in bs.items() if v is not None and DEBT_INCL.search(t)
                   and not DEBT_EXCL.search(t) and not LEASE_OP_PAT.search(t)
                   and not LEASE_FIN_PAT.search(t) and v <= 1.05 * tl]
        if singles:
            funded = max(singles); prov.append("[overcapture>liab->largest single line]")
    incl_op = (funded or 0) + (opl or 0) if (funded is not None or opl is not None) else None
    return dict(funded=funded, op_lease=opl, incl_op=incl_op,
                provenance="+".join(p for p in prov if p))


def is_financial_sic(sic):
    """SIC ranges where 'debt' is a funding book, not corporate leverage -- depository &
    non-depository credit, mortgage finance, brokers/dealers, holding & investment offices /
    BDCs. We flag these so a financial intermediary's funding debt (e.g. Farmer Mac's ~$29B) is
    not silently fed into the same leverage screens as an operating company. Insurers (6300-6411)
    are caught by detect_sector. Real estate / REITs (6500s) are left as operating (normal
    property debt)."""
    try:
        s = int(str(sic)[:4])
    except (TypeError, ValueError):
        return False
    return (6000 <= s <= 6299) or (6700 <= s <= 6799)


def detect_sector(d):
    has = lambda lst: any(t in d for t in lst)
    bank = (has(BANK_NII_NET) or has(BANK_INT_INC)) and (has(NONINT_INC) or has(NONINT_EXP))
    insurer = has(INS_PREMIUMS) or has(INS_BENEFITS)
    if insurer:
        return "insurer"
    if bank:
        return "bank"
    return "commercial"


def classify_filing(d, sector):
    """d: {tag: value} for one filing. Returns (record, prov, identities)."""
    r = {}; prov = {}
    def put(role, val, src):
        r[role] = val; prov[role] = src

    # ---------- income statement ----------
    if sector in ("bank", "insurer"):
        if sector == "bank":
            nii, _ = first(d, *BANK_NII_NET)
            noni, _ = first(d, *NONINT_INC)
            put("revenue", (nii + noni) if (nii is not None and noni is not None) else None,
                "NetInterestIncome+Noninterest")
        else:
            prem, t = first(d, "Revenues", *INS_PREMIUMS)
            put("revenue", prem, t)
        put("cost_of_revenue", None, "n/a(financial)")
        put("gross_profit", None, "n/a(financial)")
    else:
        cogs, tc = first(d, *COGS)
        if cogs is not None and cogs < 0:    # cost of revenue is non-negative; a negative tag is a sign artifact
            cogs, tc = abs(cogs), (tc or "COGS") + "[sign-normalized]"
        gp0, _ = first(d, "GrossProfit")
        # aggregate split COGS components: when revenue and GP are reported, true COGS = rev - GP.
        # Adopt base + the separately-struck component lines only when that sum reconciles and the
        # base alone does not (never double-counts a base line that already includes them).
        rev_total = next((d[t] for t in ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                                         "RevenueFromContractWithCustomerIncludingAssessedTax",
                                         "SalesRevenueNet", "Revenue") if d.get(t) is not None), None)
        if cogs is not None and gp0 is not None and rev_total is not None:
            extras = [d[t] for t in COGS_EXTRA if d.get(t) is not None and t != tc]
            if extras:
                target = rev_total - gp0
                summed = cogs + sum(extras)
                if _close(summed, target) and not _close(cogs, target):
                    cogs, tc = summed, (tc or "COGS") + "+splitCOGS"
        put("cost_of_revenue", cogs, tc)
        rev_cands = [(t, d[t]) for t in REV if t in d and d[t] is not None]
        rev = tr = None
        # identity-driven: when GP & COGS are known, the TRUE revenue is GP+COGS -> pick that tag
        if gp0 is not None and cogs is not None and rev_cands:
            target = gp0 + cogs
            for t, v in rev_cands:
                if abs(v - target) <= max(TOL_ABS, TOL_REL * max(abs(v), abs(target))):
                    rev, tr = v, t; break
        if rev is None and rev_cands:                      # else prefer the reported total 'Revenues'
            byt = {t: v for t, v in rev_cands}
            if "Revenues" in byt:
                rev, tr = byt["Revenues"], "Revenues"
            else:
                rev, tr = rev_cands[0][1], rev_cands[0][0]
        put("revenue", rev, tr)
        gp, tg = gp0, ("GrossProfit" if gp0 is not None else None)
        if gp is None and rev is not None and cogs is not None:
            gp, tg = rev - cogs, "Revenue-COGS(derived)"
        put("gross_profit", gp, tg)

    # revenue is a GROSS top line and should not be negative. When the selected tag is net-negative
    # (an insurer/holdco "Revenues" swamped by investment losses, or a contra-heavy year), recover the
    # largest positive operating-revenue line actually reported. Genuine all-negative filers keep theirs.
    if (r.get("revenue") or 0) < 0:
        alt = max(((d[t], t) for t in REV_OPERATING if d.get(t) is not None and d[t] > 0), default=None)
        if alt is not None:
            put("revenue", alt[0], alt[1] + "[neg-revenue->positive operating]")
            if prov.get("gross_profit") == "Revenue-COGS(derived)" and r.get("cost_of_revenue") is not None:
                put("gross_profit", alt[0] - r["cost_of_revenue"], "Revenue-COGS(derived)")

    pretax, tp = first(d, *PRETAX); put("pretax_income", pretax, tp)
    tax, tt = first(d, *TAX)
    # when only the CURRENT tax piece is tagged (no reported total), total tax = current + deferred,
    # else the income cascade is short by the deferred portion (probe-confirmed IS_NI gap).
    if tt == "CurrentIncomeTaxExpenseBenefit":
        dft, _ = first(d, "DeferredIncomeTaxExpenseBenefit",
                       "DeferredIncomeTaxExpenseBenefitContinuingOperations")
        if dft is not None:
            tax, tt = tax + dft, "Current+DeferredIncomeTax"
    put("tax_expense", tax, tt)

    # operating income: direct tag, else sector rule, else GP - OpEx
    oi, toi = first(d, *OINC)
    if oi is None and sector in ("bank", "insurer"):
        oi, toi = pretax, "=Pretax(financial)"      # financials: operating income := pretax
    if oi is None and r.get("gross_profit") is not None:
        opex, _ = first(d, *OPEX)
        if opex is not None:
            cand = r["gross_profit"] - opex
            # accept the derivation ONLY if it doesn't just reproduce pretax -- a GP-OpEx that lands
            # on pretax means the "OperatingExpenses" tag was the grand total (incl. interest/non-op),
            # so it's NOT operating income. Don't fabricate: leave OI blank in that case.
            if pretax is None or abs(cand - pretax) > max(TOL_ABS, TOL_REL * max(abs(cand), abs(pretax))):
                oi, toi = cand, "GP-OpEx(derived)"
    put("operating_income", oi, toi)

    # net income: consolidated (incl NCI) and parent
    consol, tcon = first(d, *NI_CONSOL)
    em, _ = first(d, *EQUITY_METHOD)            # equity-method earnings (placement varies)
    cont_at, _ = first(d, *INC_CONT_AFTERTAX)   # reported after-tax continuing-ops subtotal (incl EM)
    prop_gain, _ = first(d, *PROP_GAIN)         # REIT gain on sale of real estate (below-pretax bridge)
    # discontinued operations: reported total, else operating income from disc ops + gain on disposal.
    disc, disc_src = first(d, *DISC_TOTAL)
    op_d, _ = first(d, *DISC_OP_PART)
    dsp_d, _ = first(d, *DISC_DISPOSAL)
    if disc is None:
        parts = [x for x in (op_d, dsp_d) if x is not None]
        disc = sum(parts) if parts else None
        disc_src = ("DiscOps:OpPart+Disposal(summed)" if len(parts) > 1
                    else ("IncomeLossFromDiscontinuedOperationsNetOfTax" if disc is not None else None))
    elif dsp_d is not None:
        # a disc-ops TOTAL and a separate gain-on-disposal are both tagged. Some filers' "total" is
        # only the OPERATING portion, with the disposal struck separately -> the disposal gain is then
        # missing from the cascade (the single biggest IS_NI gap the probe found). Identity decides:
        # adopt total+disposal only when it reconciles Pretax-Tax+Disc to the reported consolidated
        # NI and the bare total does not (so we never double-count a total that already includes it).
        cont = (cont_at if cont_at is not None
                else (pretax - tax if (pretax is not None and tax is not None) else None))
        if consol is not None and cont is not None:
            implied = consol - cont
            if _close(disc + dsp_d, implied) and not _close(disc, implied):
                disc, disc_src = disc + dsp_d, "DiscTotal+Disposal"
    if consol is None:
        # prefer the reported after-tax continuing subtotal (it already contains equity-method and
        # other below-the-line items); else the standard Pretax - Tax + Disc derivation.
        if cont_at is not None:
            consol, tcon = cont_at + (disc or 0), "ContinuingAfterTax+Disc(derived)"
        elif pretax is not None and tax is not None:
            consol, tcon = pretax - tax + (disc or 0), "Pretax-Tax+Disc(derived)"
    parent, tpar = first(d, *NI_PARENT)
    nci_is, tnci = first(d, *NCI_IS)
    if nci_is is None:
        # no main NCI line -> the NCI income attribution is carried only on a separately-struck line
        # (JV partners, nonredeemable/redeemable, subsidiary). Sum them so consol - parent reconciles.
        exo = [(first(d, t)[0], t) for t in NCI_EXTRA]
        exo = [(v, t) for v, t in exo if v is not None]
        if exo:
            nci_is = sum(v for v, _ in exo); tnci = "+".join(t for _, t in exo)
    # when the filer reports both consol and parent NI, the true total NCI = consol - parent. If NCI
    # is split across lines, adopt main + the separately-struck components, but only when that sum
    # reconciles and the bare main does not (never double-counts a main line that already includes them).
    if nci_is is not None and consol is not None and parent is not None:
        extras = [first(d, t)[0] for t in NCI_EXTRA]
        extras = [v for v in extras if v is not None]
        if extras:
            target = consol - parent
            summed = nci_is + sum(extras)
            if _close(summed, target) and not _close(nci_is, target):
                nci_is, tnci = summed, (tnci or "NCI") + "+separateNCI"
    if parent is None and consol is not None and nci_is is not None:
        parent, tpar = consol - nci_is, "Consol-NCI(derived)"
    if parent is None and consol is not None and nci_is is None:
        parent, tpar = consol, tcon       # no NCI -> parent == consolidated
    # parent-NI sanity: income available to common cannot exceed parent NI (common = parent - preferred,
    # preferred >= 0). When the reported nicom exceeds our parent, the parent tag is mis-tagged -- often
    # NetIncomeLoss set equal to CONSOLIDATED while NCI is negative (NCI absorbed losses), so the true
    # parent = consol - NCI is larger. Recover it when consol - NCI reconciles to nicom (independent
    # confirmation), and is >= and at least as close to nicom as the mis-tagged value.
    nicom_rep, _ = first(d, *NI_COMMON)
    if (nicom_rep is not None and parent is not None and consol is not None and nci_is is not None
            and nicom_rep - parent > max(TOL_ABS, 0.02 * abs(parent))):
        cand = consol - nci_is
        if cand >= parent and abs(cand - nicom_rep) <= abs(parent - nicom_rep):
            parent, tpar = cand, "Consol-NCI(parent mis-tagged: nicom>parent)"
    put("net_income", parent, tpar)
    put("net_income_consolidated", consol, tcon)
    put("minority_interest", nci_is, tnci)
    nicom, tnicom = first(d, *NI_COMMON)
    pref, _ = first(d, *PREF_DIV)
    if nicom is None and parent is not None and pref is not None:
        nicom, tnicom = parent - pref, "Parent-Preferred(derived)"
    put("net_income_to_common", nicom, tnicom)

    put("discontinued_operations", disc, disc_src)
    da, tda = first(d, *DA); put("depreciation_amortization", da, tda)
    ebitda = (oi + da) if (oi is not None and da is not None) else None
    put("ebitda", ebitda, "OperatingIncome+D&A(derived)" if ebitda is not None else None)
    inte, tie = first(d, *INT_EXP); put("interest_expense", inte, tie)

    # ---------- balance sheet ----------
    cash, tcash = first(d, *CASH); put("cash", cash, tcash)
    sti, tsti = first(d, *STI); put("short_term_investments", sti, tsti)
    ta, tta = first(d, *ASSETS); put("total_assets", ta, tta)
    ca, _ = first(d, *ASSETS_CUR); put("total_current_assets", ca, None)
    nca, _ = first(d, *ASSETS_NC)
    tl, ttl = first(d, *LIAB); put("total_liabilities", tl, ttl)
    cl, _ = first(d, *LIAB_CUR); put("total_current_liabilities", cl, None)
    ncl, _ = first(d, *LIAB_NC)
    ppe, tppe = first(d, *PPE_NET); put("ppe_net", ppe, tppe)
    eqp, teqp = first(d, *EQ_PARENT)
    eqi, teqi = first(d, *EQ_INCL)
    ncibs, _ = first(d, *NCI_BS)
    total_equity = eqi if eqi is not None else (
        (eqp + ncibs) if (eqp is not None and ncibs is not None) else eqp)
    put("parent_equity", eqp, teqp)
    put("minority_interest_bs", ncibs, "MinorityInterest")
    put("total_equity", total_equity, teqi or teqp)
    # mezzanine (between liabilities and equity): temporary equity + redeemable NCI. Prefer the
    # all-in "including NCI" total; else sum temporary-equity(parent) + a redeemable-NCI tag.
    mezz, msrc = first(d, *TEMP_EQUITY_TOTAL)
    if mezz is None:
        te, tte = first(d, *TEMP_EQUITY_PARENT)
        rn, trn = first(d, *REDEEM_NCI)
        parts = [(x, t) for x, t in ((te, tte), (rn, trn)) if x is not None]
        mezz = sum(x for x, _ in parts) if parts else None
        msrc = "+".join(t for _, t in parts) if parts else None
    put("redeemable_nci", mezz, msrc)
    # back-fill a missing total via the BS identity (early/sparse filers omit the Liabilities
    # subtotal). Mark derived so the BS_FOOTS check below doesn't count it as an independent tie.
    liab_derived = False
    if tl is None and ta is not None and total_equity is not None:
        tl = ta - total_equity - (mezz or 0); liab_derived = True
        r["total_liabilities"] = tl; prov["total_liabilities"] = "Assets-Equity-Mezz(derived)"
    # total debt is reconstructed structurally in run() (needs balance-sheet-only facts); the role
    # keys are created here so the schema is stable even if run() can't compute them.
    put("total_debt", None, None)
    put("total_debt_incl_leases", None, None)
    put("operating_lease_liability", None, None)

    # ---------- cash flow ----------
    put("cfo", *first(d, *CFO))
    put("cfi", *first(d, *CFI))
    put("cff", *first(d, *CFF))
    capex, tcx = first(d, *CAPEX); put("capex", capex, tcx)
    put("free_cash_flow",
        (r.get("cfo") - abs(capex)) if (r.get("cfo") is not None and capex is not None) else None,
        "CFO-|Capex|(derived)" if (r.get("cfo") is not None and capex is not None) else None)

    # ---------- cash-flow articulation (foot CF; ending cash = BS cash incl restricted) ----------
    # Pre-ASU-2016-18 (FY<2018) the CF reconciled cash & equivalents only; after, cash + restricted.
    # Match the basis to the ΔCash tag the filer used so each era is internally consistent.
    fx_c, _ = first(d, *FX_CASH)
    dcash, tdc = first(d, *DCASH)
    restr_basis = bool(tdc and "RestrictedCash" in tdc)
    cf_end, _ = first(d, *CF_END_CASH)
    rcash, trc = first(d, *RESTR_TOTAL)
    if rcash is None:
        rc, _ = first(d, *RESTR_CUR); rnc, _ = first(d, *RESTR_NC)
        parts = [x for x in (rc, rnc) if x is not None]
        rcash = sum(parts) if parts else None
        trc = "RestrictedCash(cur+nc)" if parts else None
    put("restricted_cash", rcash, trc)
    # if `cash` itself came from a restricted-INCLUSIVE tag (the ASU 2016-18 combined line), it already
    # contains restricted -> don't add it again, or cash_total double-counts.
    cash_incl_restr = bool(tcash and "restricted" in tcash.lower())
    cash_total = (cash if cash_incl_restr else cash + (rcash or 0)) if cash is not None else None
    put("cash_total", cash_total, ("cash(incl restricted)" if cash_incl_restr else "cash+restricted")
        if cash_total is not None else None)
    # ending-cash basis the CF reconciles to: restricted-inclusive post-2018, cash-only before
    cf_basis = cf_end if cf_end is not None else cash_total
    r["_cf_end"] = cf_end if restr_basis else None      # CF_BS_CASH only when restricted-inclusive
    r["_cash_total"] = cash_total
    r["_dcash"] = dcash
    r["_roll_cash"] = (cf_basis if restr_basis else cash)   # roll cash on the matching basis

    # retained earnings + dividends (for the cross-year RE roll-forward in run()). Dividends reduce
    # parent RE -> common + preferred (NOT noncontrolling-interest distributions).
    re_bal, tre = first(d, *RETAINED)
    put("retained_earnings", re_bal, tre)
    div_t, _ = first(d, *DIV_TOTAL)
    if div_t is None:
        dc, _ = first(d, *DIV_COMMON); dp, _ = first(d, *DIV_PREF)
        parts = [x for x in (dc, dp) if x is not None]
        div_t = sum(parts) if parts else None
    put("dividends_paid", div_t, None)
    r["_re"] = re_bal
    r["_div"] = div_t
    r["_ppe"] = ppe                                   # for the cross-year PP&E roll-forward in run()

    # ============================================================================================
    # IDENTITY-DRIVEN VALUE SELECTION (tag-informed): where the tag-prior rebuild does NOT articulate,
    # let the ACCOUNTING choose the value. The identity gives the gap; the tag (semantic eligibility)
    # names which as-filed component fills it. We never plug -- we adopt an as-filed value the tag
    # says is eligible and the identity says completes the statement. Provenance records both.
    # ============================================================================================
    # --- BS_FOOTS: A = L + E + mezz. If it doesn't foot, the gap is a real as-filed component
    #     (NCI not yet in equity, redeemable/temporary equity, redeemable preferred) -> locate it. ---
    A, L, E = r.get("total_assets"), r.get("total_liabilities"), r.get("total_equity")
    mzv = r.get("redeemable_nci") or 0
    used = prov.get("redeemable_nci") or ""
    if (not liab_derived) and A is not None and L is not None and E is not None and not _close(A, L + E + mzv):
        gap = A - (L + E + mzv)
        # equity-class gap (NCI / equity-incl-NCI) -> raise total equity; mezzanine-class -> raise mezz.
        # Try the named candidates first, then a structural (pattern) match for custom temporary/
        # redeemable-equity tags (SPAC "subject to possible redemption", redeemable preferred, ...).
        hit = next(((v, t, "equity") for v, t in cands(d, BS_GAP_EQUITY) if t not in used and _close(v, gap)),
                   next(((v, t, "mezz") for v, t in cands(d, BS_GAP_MEZZ) if t not in used and _close(v, gap)), None))
        if hit is None:
            hit = next(((v, t, "mezz") for t, v in d.items()
                        if v is not None and t not in used and _close(v, gap)
                        and TEMP_EQ_PAT.search(t) and not TEMP_EQ_EXCL.search(t)), None)
        if hit is None:
            # multi-series mezzanine: a redeemable NCI split into common + preferred, or several
            # preferred series -- no single line equals the gap, but their SUM does. Adopt the summed
            # temporary/redeemable-equity lines only when the sum closes the gap (identity-gated).
            mtags = [(t, v) for t, v in d.items() if v is not None and t not in used
                     and TEMP_EQ_PAT.search(t) and not TEMP_EQ_EXCL.search(t)]
            if len(mtags) > 1:
                s = sum(v for _, v in mtags)
                if _close(s, gap):
                    hit = (s, "+".join(sorted(t for t, _ in mtags)), "mezz")
        if hit is None:
            # foot-validated temporary-equity residual. The filer's own bottom line
            # (LiabilitiesAndStockholdersEquity) equals assets -> the sheet foots, and the only GAAP
            # section between total liabilities and total equity is temporary/redeemable equity. When
            # our liabilities AND equity each already match the filer's reported subtotals, the residual
            # A - L - E is the filer's OWN implied temporary equity -- the value the balance sheet
            # requires, built from the filer's reported totals (not a plug). Common for Up-C / post-SPAC
            # / redeemable-preferred structures whose mezzanine carries a custom, unpatternable tag.
            lse, _ = first(d, "LiabilitiesAndStockholdersEquity")
            rL, _ = first(d, *LIAB)
            rEi, _ = first(d, *EQ_INCL)
            rEp, _ = first(d, *EQ_PARENT)
            eq_ok = (rEi is not None and _close(E, rEi)) or (rEp is not None and _close(E, rEp))
            if (lse is not None and _close(lse, A) and rL is not None and _close(L, rL)
                    and eq_ok and gap > TOL_ABS):
                hit = (gap, "TemporaryEquity[A-L-E; L&SE=Assets]", "mezz")
        if hit:
            v, t, slot = hit
            if slot == "equity":
                r["total_equity"] = E + v
                if t == "MinorityInterest":
                    r["minority_interest_bs"] = v
                prov["total_equity"] = f"{prov.get('total_equity','')}+{t}[BS_FOOTS gap]"
            else:
                r["redeemable_nci"] = mzv + v
                prov["redeemable_nci"] = f"{used}+{t}[BS_FOOTS gap]"

    # (the income-cascade selection — equity-method placement — is handled in the IS_NI identity
    #  check below, which already tries the as-filed candidates that make the cascade reconcile.)

    # ---------- identity tie-outs ----------
    ident = []
    def tie(name, lhs, rhs, rel=TOL_REL, ab=TOL_ABS):
        if lhs is None or rhs is None:
            ident.append((name, "n/a", None)); return
        ok = abs(lhs - rhs) <= max(ab, rel * max(abs(lhs), abs(rhs)))
        ident.append((name, "tie" if ok else "BREAK", lhs - rhs))
    A, L, E, mz = r.get("total_assets"), r.get("total_liabilities"), r.get("total_equity"), r.get("redeemable_nci")
    # skip BS_FOOTS when liabilities was back-filled from this very identity (would be tautological)
    tie("BS_FOOTS(A=L+E+mezz)", (None if liab_derived else A),
        (None if (liab_derived or L is None or E is None) else L + E + (mz or 0)))
    tie("BS_EQUITY(incl=parent+NCI)", eqi, (None if eqp is None or ncibs is None else eqp + ncibs))
    # balance-sheet subtotal foots: total = current + non-current (only when the filer reports the
    # non-current subtotal; many give current + total only, in which case this is n/a, not a break).
    tie("BS_ASSETS(cur+noncur=total)", ta, (None if ca is None or nca is None else ca + nca))
    tie("BS_LIAB(cur+noncur=total)", tl, (None if cl is None or ncl is None else cl + ncl))
    if sector == "commercial":
        tie("IS_GP(Rev-COGS)", r.get("gross_profit"),
            (None if r.get("revenue") is None or r.get("cost_of_revenue") is None
             else r["revenue"] - r["cost_of_revenue"]))
        # OI -> Pretax bridge (the non-operating section): Pretax = OI + net non-operating. Prefer the
        # reported aggregate (interest may be inside it or struck separately -> try both); n/a when
        # only scattered components are tagged. Non-gating (non-operating is filer-specific).
        nonop_agg, _ = first(d, *NONOP_AGG)
        opb = []
        if oi is not None and nonop_agg is not None:
            opb.append(oi + nonop_agg)
            opb.append(oi + nonop_agg - (inte or 0))
        ob_rhs = (min(opb, key=lambda c: abs(c - pretax)) if (opb and pretax is not None)
                  else (opb[0] if opb else None))
        tie("OI_PRETAX(OI+nonop=Pretax)", pretax, ob_rhs, rel=CF_TOL_REL, ab=CF_TOL_ABS)
    # IS_NI: consolidated NI = Pretax - Tax + Disc, plus any below-the-pretax bridge items the filer
    # struck separately -- equity-method earnings (above or below the tax line) and REIT gains on sale
    # of real estate. Each is a real as-filed component that legitimately belongs in the NI bridge;
    # we offer base, base+each, and base+both, then accept whichever construction the reported consol
    # actually matches (closest within tolerance). Adding only genuine line items (not free residuals)
    # keeps this from over-fitting -- a coincidental tie within 0.5%/$5k is implausible.
    consol_rep = r.get("net_income_consolidated")
    addends = [x for x in (em, prop_gain) if x is not None]

    def _expand(start):
        cs = [start]
        for a in addends:
            cs.append(start + a)
        if len(addends) == 2:
            cs.append(start + addends[0] + addends[1])
        return cs
    ni_cands = []
    if pretax is not None and tax is not None:
        ni_cands += _expand(pretax - tax + (disc or 0))
    if cont_at is not None:
        ni_cands += _expand(cont_at + (disc or 0))  # reported after-tax continuing subtotal + disc
    ni_rhs = (min(ni_cands, key=lambda c: abs(c - consol_rep)) if (ni_cands and consol_rep is not None)
              else (ni_cands[0] if ni_cands else None))
    tie("IS_NI(Pretax-Tax+Disc=Consol)", consol_rep, ni_rhs)
    tie("IS_NCI(Consol-Parent=NCI)",
        (None if consol is None or parent is None else consol - parent), nci_is)
    # cash-flow articulation (within-year), judged on MATERIALITY (1% / $2M / 2% of cash) -- cash-flow
    # reconciliations carry small "other"/rounding noise that isn't a real break. The CF foots,
    # and its ending cash equals BS cash + restricted. The cross-year roll-forward is added in run().
    _cashref = abs(r.get("cash_total") or r.get("cash") or 0.0)   # tolerate CF noise up to 2% of cash
    _cf_ab = max(CF_TOL_ABS, CF_TOL_CASH * _cashref)
    tie("CF_FOOT(CFO+CFI+CFF+FX=dCash)",
        (None if (r.get("cfo") is None or r.get("cfi") is None or r.get("cff") is None)
         else r["cfo"] + r["cfi"] + r["cff"] + (fx_c or 0)), dcash, rel=CF_TOL_REL, ab=_cf_ab)
    tie("CF_BS_CASH(CFend=cash+restr)", cf_end, cash_total, rel=CF_TOL_REL, ab=_cf_ab)
    return r, prov, ident


_NAME_SUFFIX = ("incorporated", "corporation", "company", "holdings", "holding", "group", "industries",
                "international", "enterprises", "inc", "corp", "co", "ltd", "llc", "lp", "plc", "the",
                "sa", "nv", "ag", "trust", "partners")


def _norm_name(s):
    s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    for suf in _NAME_SUFFIX:
        s = re.sub(rf"\b{suf}\b", "", s)
    return re.sub(r"\s+", "", s)


def _flag_predecessors(out_rows, name_of):
    """Mark a CIK's earlier years as PREDECESSOR when the SEC registrant NAME changes substantially AND
    total assets step >=4x at the same boundary -- the signature of a reverse merger (a different,
    usually larger, entity previously occupied this CIK). Conservative on purpose: a same-name
    divestiture (real shrink) or a rebrand that keeps the core name is NOT flagged."""
    from collections import defaultdict as _dd
    by = _dd(list)
    for r in out_rows:
        if str(r.get("fiscal_year", "")).isdigit():
            by[r["cik"]].append(r)
    for cik, rows in by.items():
        rows.sort(key=lambda r: r["fiscal_year"])
        boundary = None
        for i in range(1, len(rows)):
            a0, a1 = rows[i - 1].get("total_assets"), rows[i].get("total_assets")
            n0 = _norm_name(name_of.get((cik, rows[i - 1]["fiscal_year"])))
            n1 = _norm_name(name_of.get((cik, rows[i]["fiscal_year"])))
            if not a0 or not a1 or len(n0) < 3 or len(n1) < 3:
                continue
            step = max(a0, a1) / min(a0, a1)
            # "substantially different" = neither normalized name contains the other AND they don't share
            # a name root. A rename/pivot of the SAME entity that keeps the root -- "Cipher Mining" ->
            # "Cipher Digital", "Marathon Patent" -> "Marathon Digital", "Plymouth Opportunity REIT" ->
            # "Plymouth Industrial REIT" -- is NOT a different entity, so it is not flagged.
            diff = (n0 not in n1) and (n1 not in n0) and n0[:5] != n1[:5]
            if step >= 4 and diff:
                boundary = i      # rows[:i] precede the current entity
        if boundary is not None:
            for r in rows[:boundary]:
                r["entity_flag"] = "PREDECESSOR"


def apply_validated_promotions(verbose=True):
    """Fold recovery-VALIDATED as-filed tags (validated_tags.csv) into the role candidate lists, at
    LOWEST priority. This is the source-side half of the recovery loop: a tag that r2k_metric_recover /
    r2k_revenue_recover confirmed was the right line for a role (reconciled to Morningstar) gets picked
    HERE next build, so the role is no longer blank and recovery has nothing left to do for it.

    Appended at the END of each list, so every curated tag still wins; a promoted tag fires only when a
    filing has no canonical tag for that role -- exactly the blank it was validated on. Idempotent:
    tags already in the list are skipped, so repeated run() calls don't duplicate. Returns {field:[added]}."""
    # built at call time so it captures the current (module-global) list objects
    role_list = {"revenue": REV, "cost_of_revenue": COGS, "operating_income": OINC,
                 "total_equity": EQ_PARENT, "cash": CASH, "capex": CAPEX}
    added = {}
    for fld, tags in vt.load_promotions().items():
        lst = role_list.get(fld)
        if lst is None:
            continue
        new = [t for t in tags if t not in lst]
        if new:
            lst.extend(new)                     # lowest priority -> canonical tags always selected first
            added[fld] = new
    if verbose and added:
        n = sum(len(v) for v in added.values())
        print(f"  learned {n} validated as-filed tag(s) from {vt.PATH.name} (appended at low priority):")
        for fld in sorted(added):
            print(f"    +{fld}: {', '.join(added[fld])}")
    return added


def run(facts_rows, sic_of=None, name_of=None):
    apply_validated_promotions()                # fold in tags learned from prior recovery passes
    by = defaultdict(dict); bs = defaultdict(dict)
    isf = defaultdict(dict); cff = defaultdict(dict); meta = {}
    for r in facts_rows:
        v = fnum(r["value"])
        if v is None:
            continue
        key = (r["cik"], r["fiscal_year"])
        by[key][r["tag"]] = v
        stmt = r.get("stmt", ""); usd = r.get("uom", "USD") in ("USD", "")
        # statement-split facts: BS for debt; IS vs CF for the D&A / SBC consistency checks
        if usd and stmt in ("BS", ""):
            bs[key][r["tag"]] = v
        if usd and stmt in ("IS", ""):
            isf[key][r["tag"]] = v
        if usd and stmt in ("CF", ""):
            cff[key][r["tag"]] = v
        meta[key] = (r.get("taxonomy", ""), r.get("form", ""))
    # ---- pass 1: per-filing rebuild + within-year identities + structural debt ----
    stage = {}
    for key in sorted(by):
        cik, fy = key
        sector = detect_sector(by[key])
        rec, prov, ident = classify_filing(by[key], sector)
        dd = reconstruct_debt(bs[key])
        rec["total_debt"] = dd["funded"]
        rec["total_debt_incl_leases"] = dd["incl_op"]
        rec["operating_lease_liability"] = dd["op_lease"]
        prov["total_debt"] = dd["provenance"]
        flags = []
        if sector in ("bank", "insurer") or is_financial_sic((sic_of or {}).get(cik)):
            flags.append("financial")
        tl = rec.get("total_liabilities")
        if dd["funded"] is not None and tl is not None and dd["funded"] > 1.05 * tl:
            flags.append("DEBT>LIAB")
        rec["debt_flag"] = ";".join(flags)

        # ---- D&A capture + IS<->CF consistency (a non-financial / EBITDA concept) ----
        def cons(lhs, rhs):
            if lhs is None or rhs is None:
                return ("n/a", None)
            ok = abs(lhs - rhs) <= max(CF_TOL_ABS, CF_TOL_REL * max(abs(lhs), abs(rhs)))
            return ("tie" if ok else "BREAK", lhs - rhs)
        if sector == "commercial":
            da_cf = sum_da(cff[key])    # cash flow is authoritative for TOTAL operating D&A
            # GAP-FILL ONLY: keep the standard-tag D&A where present (don't override a good value);
            # only fill from the cash-flow add-back when the income-statement role found nothing
            # (e.g. FirstCash, which tags D&A under non-standard names). Recompute EBITDA only then.
            if rec.get("depreciation_amortization") is None and da_cf is not None:
                rec["depreciation_amortization"] = da_cf
                oi = rec.get("operating_income")
                if oi is not None:
                    rec["ebitda"] = oi + da_cf
            # CONSISTENCY is only valid when the income statement states a TOTAL D&A (a recognized
            # subtotal). Most income statements bury D&A in COGS/SG&A and break out only part of it,
            # so comparing that partial figure to the CF's full add-back is apples-to-oranges -> n/a.
            da_is_total = next((isf[key][t] for t in DA_SUBTOTAL if isf[key].get(t) is not None), None)
            ident.append(("DA_CONSISTENCY(IS=CF)", *cons(da_is_total, da_cf)))
        else:
            # banks/insurers: D&A mixes premium/discount & intangible amortization -> not a clean
            # operating concept and EBITDA is n/a, so don't impose the consistency check.
            ident.append(("DA_CONSISTENCY(IS=CF)", "n/a", None))
        # SBC add-back (cash flow) vs expensed (income statement, when separately tagged)
        sbc_cf = next((cff[key][t] for t in SBC_TAGS if cff[key].get(t) is not None), None)
        sbc_is = next((isf[key][t] for t in SBC_TAGS if isf[key].get(t) is not None), None)
        rec["share_based_comp"] = sbc_cf if sbc_cf is not None else sbc_is
        ident.append(("SBC_CONSISTENCY(IS=CF)", *cons(sbc_is, sbc_cf)))
        stage[key] = dict(rec=rec, ident=ident, sector=sector, prov=prov)

    # ---- source-XBRL scale correction: a filing whose magnitudes are uniformly ~1000x SMALLER than
    # BOTH neighbors is a filer decimals/scale mis-tag (e.g. cik 1048268 FY2013: Assets tagged 179,252
    # instead of 179,252,000). Rescale that year's dollar outputs x1000. Gated on the precise
    # dip-and-recover signature (300x-3000x vs BOTH neighbors) so a genuine large change never triggers.
    _DOLLARS = {"revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebitda",
                "depreciation_amortization", "interest_expense", "pretax_income", "tax_expense",
                "net_income_consolidated", "minority_interest", "discontinued_operations", "net_income",
                "net_income_to_common", "cash", "restricted_cash", "cash_total", "short_term_investments",
                "ppe_net", "total_current_assets", "total_assets", "total_current_liabilities",
                "total_liabilities", "total_debt", "total_debt_incl_leases", "operating_lease_liability",
                "parent_equity", "minority_interest_bs", "total_equity", "redeemable_nci",
                "retained_earnings", "dividends_paid", "share_based_comp", "cfo", "cfi", "cff",
                "capex", "free_cash_flow"}
    for (cik, fy), st in list(stage.items()):
        if not fy.isdigit():
            continue
        a = st["rec"].get("total_assets")
        p = stage.get((cik, str(int(fy) - 1)))
        n = stage.get((cik, str(int(fy) + 1)))
        if not a or p is None or n is None:
            continue
        ap, an = p["rec"].get("total_assets"), n["rec"].get("total_assets")
        if not ap or not an:
            continue
        if 300 <= abs(ap / a) <= 3000 and 300 <= abs(an / a) <= 3000:   # ~1000x smaller than both
            rec = st["rec"]
            for k, v in list(rec.items()):
                if isinstance(v, (int, float)) and (k in _DOLLARS or k.startswith("_")):
                    rec[k] = v * 1000
            rec["debt_flag"] = ((rec.get("debt_flag") or "") + ";RESCALED_1000x").strip(";")

    # ---- pass 2: cross-year cash roll-forward (cash[t] = cash[t-1] + dCash[t]) ----
    # ties the cash flow statement's net change to the balance-sheet cash year over year -- the
    # leg that makes the three statements actually flow. First covered year per company is n/a.
    for (cik, fy), st in stage.items():
        rec = st["rec"]
        lhs = rec.get("_roll_cash"); dc = rec.get("_dcash")
        prior = stage.get((cik, str(int(fy) - 1))) if fy.isdigit() else None
        rhs = None
        if prior is not None and dc is not None:
            pc = prior["rec"].get("_roll_cash")
            rhs = (pc + dc) if pc is not None else None
        if lhs is None or rhs is None:
            st["ident"].append(("CASH_ROLL(cash[t]=cash[t-1]+dCash)", "n/a", None))
        else:
            _cr = abs(rec.get("cash_total") or rec.get("cash") or 0.0)
            ok = abs(lhs - rhs) <= max(CF_TOL_ABS, CF_TOL_CASH * _cr, CF_TOL_REL * max(abs(lhs), abs(rhs)))
            st["ident"].append(("CASH_ROLL(cash[t]=cash[t-1]+dCash)", "tie" if ok else "BREAK", lhs - rhs))

        # ---- retained-earnings roll-forward: RE[t] = RE[t-1] + NI(parent) - dividends ----
        # the canonical IS->BS tie (net income, less payout, accumulates into book equity). Breaks
        # legitimately flag cumulative-effect accounting adoptions (ASC 606/842, CECL), treasury
        # retirements, and declared-vs-paid dividend timing -- which is exactly what we want to see.
        re_t = rec.get("_re"); ni_p = rec.get("net_income"); div = rec.get("_div") or 0
        re_rhs = None
        if prior is not None and re_t is not None and ni_p is not None:
            re_1 = prior["rec"].get("_re")
            re_rhs = (re_1 + ni_p - div) if re_1 is not None else None
        if re_t is None or re_rhs is None:
            st["ident"].append(("RE_ROLL(RE[t]=RE[t-1]+NI-Div)", "n/a", None))
        else:
            ok = abs(re_t - re_rhs) <= max(CF_TOL_ABS, CF_TOL_REL * max(abs(re_t), abs(re_rhs)))
            st["ident"].append(("RE_ROLL(RE[t]=RE[t-1]+NI-Div)", "tie" if ok else "BREAK", re_t - re_rhs))

        # ---- PP&E roll-forward: PP&E[t] ~= PP&E[t-1] + capex - depreciation ----
        # the asset-side articulation: capex (CF) builds PP&E, depreciation (IS) reduces it. A
        # diagnostic (non-gating): breaks flag disposals, acquisitions, impairments, and -- usefully
        # -- implausible D&A (depreciation a balance sheet can't support). Uses total D&A as a proxy
        # for depreciation, so intangible-heavy filers carry a small expected residual.
        ppe_t = rec.get("_ppe"); capex = rec.get("capex"); da = rec.get("depreciation_amortization")
        ppe_rhs = None
        if prior is not None and ppe_t is not None and capex is not None and da is not None:
            ppe_1 = prior["rec"].get("_ppe")
            ppe_rhs = (ppe_1 + abs(capex) - da) if ppe_1 is not None else None
        if ppe_t is None or ppe_rhs is None:
            st["ident"].append(("PPE_ROLL(PPE[t]=PPE[t-1]+capex-dep)", "n/a", None))
        else:
            ok = abs(ppe_t - ppe_rhs) <= max(CF_TOL_ABS * 5, 0.05 * max(abs(ppe_t), abs(ppe_rhs)))
            st["ident"].append(("PPE_ROLL(PPE[t]=PPE[t-1]+capex-dep)", "tie" if ok else "BREAK", ppe_t - ppe_rhs))

    # ---- pass 3: finalize confidence/breaks over all identities (incl the cash-flow legs) ----
    out_rows = []; tie_rows = []
    for key in sorted(stage):
        cik, fy = key; st = stage[key]; rec = st["rec"]; ident = st["ident"]
        # confidence/breaks gate on the core articulation legs; NON_GATING legs are still reported
        ntie = sum(1 for n, s, _ in ident if s == "tie" and n not in NON_GATING)
        napp = sum(1 for n, s, _ in ident if s != "n/a" and n not in NON_GATING)
        conf = ntie / napp if napp else None
        broke = [n for n, s, _ in ident if s == "BREAK" and n not in NON_GATING]
        # provenance: surface only the NON-OBVIOUS derivations -- values we aggregated, derived, or
        # identity-selected (markers below), not the plain single-tag picks -- so an auditor sees
        # exactly which numbers were engineered and how (split COGS, disposal gain, temp-equity=A-L-E).
        MARK = ("+", "[", "=", "derived", "summed", "split", "Disc")
        prov_summary = "; ".join(f"{k}={v}" for k, v in st["prov"].items()
                                 if v and any(m in str(v) for m in MARK))
        rec_full = dict(cik=cik, fiscal_year=fy, sector=st["sector"], taxonomy=meta[key][0],
                        form=meta[key][1], n_identities=napp, n_tie=ntie,
                        confidence=("%.2f" % conf if conf is not None else ""),
                        breaks=";".join(broke), provenance=prov_summary, entity_flag="", **rec)
        out_rows.append(rec_full)
        for n, s, resid in ident:
            tie_rows.append(dict(cik=cik, fiscal_year=fy, sector=st["sector"], identity=n, result=s,
                                 residual=("" if resid is None else "%.0f" % resid)))
    _flag_predecessors(out_rows, name_of or {})    # mark reverse-merger predecessor years
    return out_rows, tie_rows


def main():
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    facts = list(csv.DictReader(open(FACTS, encoding="utf-8")))
    sic_of, name_of = {}, {}
    if INDEX.exists():
        for r in csv.DictReader(open(INDEX, encoding="utf-8")):
            sic_of.setdefault(r.get("cik", ""), r.get("sic", ""))
            fy = r.get("fy", "")
            if str(fy).isdigit():
                name_of[(r.get("cik", ""), str(fy))] = r.get("name", "")
    out_rows, tie_rows = run(facts, sic_of, name_of)
    fields = ["cik", "fiscal_year", "sector", "taxonomy", "form", "n_identities", "n_tie",
              "confidence", "breaks", "provenance", "entity_flag", "revenue", "cost_of_revenue", "gross_profit",
              "operating_income", "ebitda", "depreciation_amortization", "interest_expense",
              "pretax_income", "tax_expense", "net_income_consolidated", "minority_interest",
              "discontinued_operations", "net_income", "net_income_to_common", "cash",
              "restricted_cash", "cash_total", "short_term_investments", "ppe_net",
              "total_current_assets", "total_assets", "total_current_liabilities",
              "total_liabilities", "total_debt", "total_debt_incl_leases",
              "operating_lease_liability", "debt_flag", "parent_equity", "minority_interest_bs",
              "total_equity", "redeemable_nci", "retained_earnings", "dividends_paid",
              "share_based_comp", "cfo", "cfi", "cff", "capex", "free_cash_flow"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in out_rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in fields})
    with open(TIEOUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "sector", "identity", "result", "residual"])
        w.writeheader(); w.writerows(tie_rows)
    # summary
    n = len(out_rows)
    fulltie = sum(1 for r in out_rows if r["confidence"] == "1.00")
    anybreak = sum(1 for r in out_rows if r["breaks"])
    from collections import Counter
    print(f"  -> {OUT.name}: {n:,} filings classified")
    print(f"  -> {TIEOUT.name}")
    print(f"  sectors: {dict(Counter(r['sector'] for r in out_rows))}")
    print(f"  all-identities-tie: {fulltie:,}/{n:,}   filings with >=1 break: {anybreak:,}")


# --------------------------------------------------------------------------- selftest
def selftest():
    industrial = {"Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 300,
                  "OperatingIncomeLoss": 100, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 90,
                  "IncomeTaxExpenseBenefit": 20, "ProfitLoss": 70, "NetIncomeLoss": 65,
                  "NetIncomeLossAttributableToNoncontrollingInterest": 5,
                  "DepreciationDepletionAndAmortization": 50,
                  "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 1900,
                  "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest": 2000,
                  "MinorityInterest": 100, "NetCashProvidedByUsedInOperatingActivities": 150,
                  "PaymentsToAcquirePropertyPlantAndEquipment": 40,
                  "LongTermDebtNoncurrent": 800, "LongTermDebtCurrent": 50}
    bank = {"InterestAndDividendIncomeOperating": 600, "InterestExpenseOperating": 200,
            "InterestIncomeExpenseNet": 400, "ProvisionForLoanLeaseAndOtherLosses": 10,
            "NoninterestIncome": 120, "NoninterestExpense": 250,
            "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 260,
            "IncomeTaxExpenseBenefit": 50, "NetIncomeLoss": 210,
            "Assets": 13000, "Liabilities": 11500, "StockholdersEquity": 1500,
            "NetCashProvidedByUsedInOperatingActivities": 300}
    # disc-ops disposal: "total" is the OPERATING portion only (-2M); disposal (+5M) tagged separately;
    # reported consol = 90 - 20 + (-2 + 5) = 73M. Engine must adopt total+disposal (disc=3M), not -2M.
    # (values at $M scale so the disposal gap exceeds the absolute tie-out tolerance.)
    _m = 1_000_000
    discops = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 300,
        "OperatingIncomeLoss": 100,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 90,
        "IncomeTaxExpenseBenefit": 20,
        "IncomeLossFromDiscontinuedOperationsNetOfTax": -2,
        "DiscontinuedOperationGainLossOnDisposalOfDiscontinuedOperationNetOfTax": 5,
        "ProfitLoss": 73, "NetIncomeLoss": 73,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # REIT property-sale gain struck BELOW the pre-tax subtotal: pretax (pre-gain) 50, tax 0 (REIT),
    # gain on sale 20 -> reported consol = 50 - 0 + 20 = 70. The base cascade (50) is short by the gain;
    # the engine must adopt the gain as a below-line addend so IS_NI ties.
    reit = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 300,
        "OperatingIncomeLoss": 100,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 50,
        "IncomeTaxExpenseBenefit": 0,
        "GainLossOnSaleOfPropertiesNetOfApplicableIncomeTaxes": 20,
        "ProfitLoss": 70, "NetIncomeLoss": 70,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # NCI split across lines: consol 100, parent 85 -> true total NCI 15 = main 10 + redeemable 5.
    # The engine must aggregate the separate redeemable-NCI line so minority_interest=15 and IS_NCI ties.
    splitnci = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 270,
        "OperatingIncomeLoss": 130,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 130,
        "IncomeTaxExpenseBenefit": 30, "ProfitLoss": 100, "NetIncomeLoss": 85,
        "NetIncomeLossAttributableToNoncontrollingInterest": 10,
        "NetIncomeLossAttributableToRedeemableNoncontrollingInterest": 5,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # split COGS: a product+services filer reports CostOfGoodsSold 400 + a separate CostOfServices 200;
    # reported GrossProfit 400 = rev 1000 - true COGS 600. The engine must aggregate the services cost
    # so cost_of_revenue=600 and IS_GP ties (the base line alone, 400, leaves a 200 gap).
    splitcogs = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfGoodsSold": 400, "CostOfServices": 200, "GrossProfit": 400,
        "OperatingExpenses": 250, "OperatingIncomeLoss": 150,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 150,
        "IncomeTaxExpenseBenefit": 30, "ProfitLoss": 120, "NetIncomeLoss": 120,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # mezzanine gap-fill: the sheet doesn't foot because redeemable/temporary equity (between
    # liabilities and permanent equity) is uncaptured. (7) a single custom convertible-preferred
    # series = the gap; (8) two preferred series that SUM to the gap (multi-series mezzanine).
    mezz_single = {k: v * _m for k, v in {
        "Assets": 1000, "Liabilities": 600, "StockholdersEquity": 300,
        "ConvertiblePreferredStockSeriesC": 100,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    mezz_sum = {k: v * _m for k, v in {
        "Assets": 1000, "Liabilities": 600, "StockholdersEquity": 200,
        "ConvertiblePreferredStockSeriesH": 120, "ConvertiblePreferredStockSeriesI": 80,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # foot-validated residual mezzanine: the filer reports L&SE = Assets (sheet foots) and our L and E
    # match the reported subtotals, but the temporary equity (100) carries a CUSTOM tag we can't match.
    # The residual A - L - E is the filer's own implied temporary equity -> capture it, BS_FOOTS ties.
    residual_mezz = {k: v * _m for k, v in {
        "Assets": 1000, "LiabilitiesAndStockholdersEquity": 1000,
        "Liabilities": 600, "StockholdersEquity": 300,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # debt over-capture guards: SecuredDebtNonrelatedParty is a COMPONENT of SecuredDebt (drop it);
    # convertible PREFERRED, a debt+equity capitalization line, and a footnote carrying-amount roll-up
    # must NOT be swept into funded debt. True funded debt = SecuredDebt 500 (< liabilities 700).
    debt_overcap = {k: v * _m for k, v in {
        "Assets": 2000, "Liabilities": 700, "StockholdersEquity": 1300,
        "SecuredDebt": 500, "SecuredDebtNonrelatedParty": 480,
        "Series6ConvertiblePreferredStock": 1000, "CapitalizationLongtermDebtAndEquity": 3000,
        "DebtInstrumentCarryingAmount": 900,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # hard-bound fallback: overlapping non-recourse totals/components sum to 2,963 > liabilities 1,600
    # (impossible) and don't share name prefixes -> fall back to the largest single debt line <= liab.
    debt_overcap2 = {k: v * _m for k, v in {
        "Assets": 3000, "Liabilities": 1600, "StockholdersEquity": 1400,
        "NonRecourseDebt": 1200, "NonRecourseSecuredNotesPayable": 1545,
        "LineOfCredit": 70, "ConvertibleNotesPayable": 148,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # revenue<0: "Revenues" is net-negative (-1000); recover the positive operating revenue (royalty 300).
    revneg = {k: v * _m for k, v in {
        "Revenues": -1000, "RoyaltyRevenue": 300,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # COGS<0: CostOfRevenue tagged negative (-500) -> sign-normalize to 500, gross profit = 2000-500.
    cogsneg = {k: v * _m for k, v in {
        "Revenues": 2000, "CostOfRevenue": -500,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    # parent NI mis-tagged: NetIncomeLoss set = consolidated (128) while NCI is NEGATIVE (-17.8); the
    # true parent = consol - NCI = 145.8 = the reported income-available-to-common. Engine must recover it.
    parentfix = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 250,
        "OperatingIncomeLoss": 150,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 160,
        "IncomeTaxExpenseBenefit": 32, "ProfitLoss": 128, "NetIncomeLoss": 128,
        "NetIncomeLossAttributableToNoncontrollingInterest": -17.8,
        "NetIncomeLossAvailableToCommonStockholdersBasic": 145.8,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # same mis-tagged parent, but the NCI is carried ONLY on a separate JV-partners line (no main NCI
    # tag) -> the engine must capture NCI from that line, then recover parent = consol - NCI = 145.8.
    parentfix2 = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfRevenue": 600, "GrossProfit": 400, "OperatingExpenses": 250,
        "OperatingIncomeLoss": 150,
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 160,
        "IncomeTaxExpenseBenefit": 32, "ProfitLoss": 128, "NetIncomeLoss": 128,
        "NoncontrollingInterestInNetIncomeLossJointVenturePartnersRedeemable": -17.8,
        "NetIncomeLossAvailableToCommonStockholdersBasic": 145.8,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 150}.items()}
    # split COGS under non-standard tags: a "CostOfGoods" base (not CostOfGoodsSold) + CostOfServices,
    # and an intangible-impairment folded into cost of revenue -- both must aggregate to true COGS=900.
    cogsgoods = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfGoods": 600, "CostOfServices": 300, "GrossProfit": 100,
        "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    cogsimpair = {k: v * _m for k, v in {
        "Revenues": 1000, "CostOfGoodsAndServicesSold": 600, "CostOfImpairmentOfIntangibleAssets": 300,
        "GrossProfit": 100, "Assets": 5000, "Liabilities": 3000, "StockholdersEquity": 2000,
        "NetCashProvidedByUsedInOperatingActivities": 50}.items()}
    facts = []
    for cik, d in (("1", industrial), ("2", bank), ("3", discops), ("4", reit),
                   ("5", splitnci), ("6", splitcogs), ("7", mezz_single), ("8", mezz_sum),
                   ("9", residual_mezz), ("10", debt_overcap), ("11", debt_overcap2),
                   ("12", revneg), ("13", cogsneg), ("14", parentfix), ("15", parentfix2),
                   ("17", cogsgoods), ("18", cogsimpair)):
        for tag, v in d.items():
            facts.append(dict(cik=cik, fiscal_year="2024", taxonomy="usgaap", form="10-K", tag=tag, value=str(v)))
    # cik 16: a 3-year company whose MIDDLE year (2023) is uniformly 1000x too small (filer scale
    # mis-tag); the engine must rescale it x1000 so total_assets returns to 300M.
    for yr, sc in (("2022", _m), ("2023", _m / 1000.0), ("2024", _m)):
        for tag, base in {"Assets": 300, "Liabilities": 180, "StockholdersEquity": 120, "Revenues": 250,
                          "CostOfRevenue": 150, "GrossProfit": 100,
                          "NetCashProvidedByUsedInOperatingActivities": 40}.items():
            facts.append(dict(cik="16", fiscal_year=yr, taxonomy="usgaap", form="10-K",
                              tag=tag, value=str(base * sc)))
    out, tie = run(facts)
    for r in out:
        print(f"\n  cik {r['cik']} sector={r['sector']} conf={r['confidence']} breaks=[{r['breaks']}]")
        for k in ("revenue", "gross_profit", "operating_income", "ebitda", "pretax_income",
                  "net_income_consolidated", "net_income", "minority_interest", "total_equity", "total_debt"):
            print(f"     {k:<24} {r.get(k)}")
    ind = next(r for r in out if r["cik"] == "1")
    bk = next(r for r in out if r["cik"] == "2")
    ok = (ind["confidence"] == "1.00" and ind["ebitda"] == 150 and ind["net_income"] == 65
          and ind["minority_interest"] == 5 and ind["total_equity"] == 2000 and ind["total_debt"] == 850
          and bk["sector"] == "bank" and bk["revenue"] == 520 and bk["operating_income"] == 260
          and bk["gross_profit"] is None and bk["confidence"] == "1.00")
    dops = next(r for r in out if r["cik"] == "3")
    ok_dops = (dops["discontinued_operations"] == 3 * _m and dops["net_income_consolidated"] == 73 * _m
               and "IS_NI" not in dops["breaks"])
    rt = next(r for r in out if r["cik"] == "4")
    ok_reit = (rt["net_income_consolidated"] == 70 * _m and "IS_NI" not in rt["breaks"])
    sn = next(r for r in out if r["cik"] == "5")
    ok_snci = (sn["minority_interest"] == 15 * _m and "IS_NCI" not in sn["breaks"])
    sc = next(r for r in out if r["cik"] == "6")
    ok_scogs = (sc["cost_of_revenue"] == 600 * _m and sc["gross_profit"] == 400 * _m
                and "IS_GP" not in sc["breaks"])
    mz1 = next(r for r in out if r["cik"] == "7")
    ok_mz1 = (mz1["redeemable_nci"] == 100 * _m and "BS_FOOTS" not in mz1["breaks"])
    mz2 = next(r for r in out if r["cik"] == "8")
    ok_mz2 = (mz2["redeemable_nci"] == 200 * _m and "BS_FOOTS" not in mz2["breaks"])
    mz3 = next(r for r in out if r["cik"] == "9")
    ok_mz3 = (mz3["redeemable_nci"] == 100 * _m and "BS_FOOTS" not in mz3["breaks"])
    dov = next(r for r in out if r["cik"] == "10")
    ok_dov = (dov["total_debt"] == 500 * _m)
    dov2 = next(r for r in out if r["cik"] == "11")
    ok_dov2 = (dov2["total_debt"] == 1545 * _m)
    rvn = next(r for r in out if r["cik"] == "12")
    ok_rvn = (rvn["revenue"] == 300 * _m)
    cgn = next(r for r in out if r["cik"] == "13")
    ok_cgn = (cgn["cost_of_revenue"] == 500 * _m and cgn["gross_profit"] == 1500 * _m)
    pfx = next(r for r in out if r["cik"] == "14")
    ok_pfx = (pfx["net_income"] == 145.8 * _m and "IS_NCI" not in pfx["breaks"])
    pfx2 = next(r for r in out if r["cik"] == "15")
    ok_pfx2 = (pfx2["net_income"] == 145.8 * _m and "IS_NCI" not in pfx2["breaks"])
    rsc = next(r for r in out if r["cik"] == "16" and r["fiscal_year"] == "2023")
    ok_rsc = (rsc["total_assets"] == 300 * _m and "RESCALED_1000x" in (rsc.get("debt_flag") or ""))
    cg = next(r for r in out if r["cik"] == "17")
    ok_cg = (cg["cost_of_revenue"] == 900 * _m and "IS_GP" not in cg["breaks"])
    ci = next(r for r in out if r["cik"] == "18")
    ok_ci = (ci["cost_of_revenue"] == 900 * _m and "IS_GP" not in ci["breaks"])
    # predecessor (reverse-merger) detection: name change + >=4x asset step flags the earlier years;
    # a same-name divestiture (real shrink, no name change) must NOT be flagged.
    pred = [{"cik": "X", "fiscal_year": y, "total_assets": a * _m, "entity_flag": ""}
            for y, a in (("2013", 20000), ("2014", 21000), ("2015", 3000), ("2016", 3200))]
    _flag_predecessors(pred, {("X", "2013"): "BigCorp Industries Inc", ("X", "2014"): "BigCorp Industries Inc",
                              ("X", "2015"): "NewCo Therapeutics Inc", ("X", "2016"): "NewCo Therapeutics Inc"})
    div = [{"cik": "Y", "fiscal_year": y, "total_assets": a * _m, "entity_flag": ""}
           for y, a in (("2014", 20000), ("2015", 3000))]
    _flag_predecessors(div, {("Y", "2014"): "Acme Corp", ("Y", "2015"): "Acme Corp"})
    # a rename/pivot of the SAME entity (shared name root) + big step must NOT be flagged
    ren = [{"cik": "Z", "fiscal_year": y, "total_assets": a * _m, "entity_flag": ""}
           for y, a in (("2023", 800), ("2024", 4300))]
    _flag_predecessors(ren, {("Z", "2023"): "Cipher Mining Inc", ("Z", "2024"): "Cipher Digital Inc"})
    ok_pred = ([r["entity_flag"] for r in pred] == ["PREDECESSOR", "PREDECESSOR", "", ""]
               and all(r["entity_flag"] == "" for r in div)
               and all(r["entity_flag"] == "" for r in ren))
    print(f"\n  SELFTEST industrial+bank cascade & identities: {'PASS' if ok else 'FAIL'}")
    print(f"  SELFTEST disc-ops disposal selection (disc={dops['discontinued_operations']}, "
          f"consol={dops['net_income_consolidated']}, IS_NI tie): {'PASS' if ok_dops else 'FAIL'}")
    print(f"  SELFTEST REIT property-gain bridge (consol={rt['net_income_consolidated']}, "
          f"IS_NI tie): {'PASS' if ok_reit else 'FAIL'}")
    print(f"  SELFTEST split-NCI aggregation (minority_interest={sn['minority_interest']}, "
          f"IS_NCI tie): {'PASS' if ok_snci else 'FAIL'}")
    print(f"  SELFTEST split-COGS aggregation (cost_of_revenue={sc['cost_of_revenue']}, "
          f"IS_GP tie): {'PASS' if ok_scogs else 'FAIL'}")
    print(f"  SELFTEST mezzanine gap-fill, single series (redeemable_nci={mz1['redeemable_nci']}, "
          f"BS_FOOTS tie): {'PASS' if ok_mz1 else 'FAIL'}")
    print(f"  SELFTEST mezzanine gap-fill, multi-series sum (redeemable_nci={mz2['redeemable_nci']}, "
          f"BS_FOOTS tie): {'PASS' if ok_mz2 else 'FAIL'}")
    print(f"  SELFTEST foot-validated residual mezz (redeemable_nci={mz3['redeemable_nci']}, "
          f"BS_FOOTS tie): {'PASS' if ok_mz3 else 'FAIL'}")
    print(f"  SELFTEST debt over-capture guard (total_debt={dov['total_debt']}, "
          f"expect 500M): {'PASS' if ok_dov else 'FAIL'}")
    print(f"  SELFTEST debt hard-bound fallback (total_debt={dov2['total_debt']}, "
          f"expect 1545M): {'PASS' if ok_dov2 else 'FAIL'}")
    print(f"  SELFTEST revenue<0 recovery (revenue={rvn['revenue']}, expect 300M): "
          f"{'PASS' if ok_rvn else 'FAIL'}")
    print(f"  SELFTEST COGS<0 sign-normalize (cost={cgn['cost_of_revenue']}, gp={cgn['gross_profit']}): "
          f"{'PASS' if ok_cgn else 'FAIL'}")
    print(f"  SELFTEST parent-NI recovery (net_income={pfx['net_income']}, expect 145.8M): "
          f"{'PASS' if ok_pfx else 'FAIL'}")
    print(f"  SELFTEST parent-NI via separate-line NCI (net_income={pfx2['net_income']}, expect 145.8M): "
          f"{'PASS' if ok_pfx2 else 'FAIL'}")
    print(f"  SELFTEST 1000x scale correction (2023 total_assets={rsc['total_assets']}, expect 300M): "
          f"{'PASS' if ok_rsc else 'FAIL'}")
    print(f"  SELFTEST CostOfGoods split-COGS (cost_of_revenue={cg['cost_of_revenue']}, expect 900M): "
          f"{'PASS' if ok_cg else 'FAIL'}")
    print(f"  SELFTEST impairment-in-COGS (cost_of_revenue={ci['cost_of_revenue']}, expect 900M): "
          f"{'PASS' if ok_ci else 'FAIL'}")
    print(f"  SELFTEST predecessor-entity detection (reverse merger flagged, divestiture not): "
          f"{'PASS' if ok_pred else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
