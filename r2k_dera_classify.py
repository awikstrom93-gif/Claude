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

BASE = Path(os.environ.get("R2KG_BASE", "."))
FACTS = BASE / "dera_facts.csv"
INDEX = BASE / "dera_filing_index.csv"          # optional, for SIC
OUT = BASE / "fundamentals_dera.csv"
TIEOUT = BASE / "tieout_report.csv"
TOL_REL, TOL_ABS = 0.005, 5000.0                # identity tie tolerance (BS / IS -- should tie exactly)
CF_TOL_REL, CF_TOL_ABS = 0.01, 2_000_000.0      # cash-flow legs -- materiality (reconciliation noise)
# reported but NOT counted toward confidence: CF_BS_CASH's restricted-cash decomposition is fragile
# (restricted-cash tag coverage), and CASH_ROLL already proves the CF<->BS cash linkage robustly.
NON_GATING = {"CF_BS_CASH(CFend=cash+restr)"}


def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def first(d, *tags):
    """first present tag value + the tag name; (None, None) if none present."""
    for t in tags:
        if t in d and d[t] is not None:
            return d[t], t
    return None, None


# ---- candidate tags per standardized role (us-gaap; IFRS variants appended) ----
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
       "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
       "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenueFromContractsWithCustomers",
       "Revenue"]
COGS = ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices",
        "CostOfSales"]
OPEX = ["OperatingExpenses", "OperatingCostsAndExpenses", "CostsAndExpenses"]
OINC = ["OperatingIncomeLoss", "ProfitLossFromOperatingActivities"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic", "ProfitLossBeforeTax"]
TAX = ["IncomeTaxExpenseBenefit", "IncomeTaxExpenseBenefitContinuingOperations",
       "IncomeTaxExpenseContinuingOperations", "CurrentIncomeTaxExpenseBenefit"]
NI_PARENT = ["NetIncomeLoss", "ProfitLossAttributableToOwnersOfParent"]
NI_CONSOL = ["ProfitLoss", "NetIncomeLossIncludingPortionAttributableToNoncontrollingInterest"]
NI_COMMON = ["NetIncomeLossAvailableToCommonStockholdersBasic"]
DISC_OPS = ["IncomeLossFromDiscontinuedOperationsNetOfTax",
            "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity",
            "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTax"]
NCI_IS = ["NetIncomeLossAttributableToNoncontrollingInterest",
          "ProfitLossAttributableToNoncontrollingInterests"]
PREF_DIV = ["PreferredStockDividendsIncomeStatementImpact", "PreferredStockDividendsAndOtherAdjustments"]
DA = ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
      "DepreciationAndAmortization", "DepreciationAmortizationAndDepletion", "Depreciation"]
INT_EXP = ["InterestExpense", "InterestExpenseDebt", "InterestExpenseNonoperating",
           "InterestAndDebtExpense", "InterestExpenseOperating"]
# balance sheet
CASH = ["CashAndCashEquivalentsAtCarryingValue", "Cash", "CashAndCashEquivalents",
        "CashCashEquivalentsAndShortTermInvestments"]
STI = ["ShortTermInvestments", "OtherShortTermInvestments", "AvailableForSaleSecuritiesCurrent"]
ASSETS = ["Assets"]
ASSETS_CUR = ["AssetsCurrent"]
LIAB = ["Liabilities"]
LIAB_CUR = ["LiabilitiesCurrent"]
EQ_PARENT = ["StockholdersEquity", "PartnersCapital", "MembersEquity", "CommonStockholdersEquity"]
EQ_INCL = ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
           "PartnersCapitalIncludingPortionAttributableToNoncontrollingInterest"]
NCI_BS = ["MinorityInterest"]
REDEEM_NCI = ["RedeemableNoncontrollingInterestEquityCarryingAmount",
              "RedeemableNoncontrollingInterestEquityOtherCarryingAmount",
              "RedeemableNoncontrollingInterestEquityFairValue"]
TEMP_EQUITY_TOTAL = ["TemporaryEquityCarryingAmountIncludingPortionAttributableToNoncontrollingInterests"]
TEMP_EQUITY_PARENT = ["TemporaryEquityCarryingAmountAttributableToParent", "TemporaryEquityCarryingAmount"]
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
                       r"line.?of.?credit|lineofcredit|convertible|financingobligation|"
                       r"loanspayable|subordinat|mediumterm|commercialpaper|"
                       r"vehicleprogram|floor.?plan", re.I)   # incl fleet/floorplan (consolidated debt)
LEASE_FIN_PAT = re.compile(r"(finance|capital)lease.*(liabilit|obligation)", re.I)
LEASE_OP_PAT = re.compile(r"operatinglease.*liabilit", re.I)
DEBT_EXCL = re.compile(r"securit|heldtomaturity|availableforsale|receivable|investment|"
                       r"repaymentsof|proceedsfrom|faceamount|fairvalue|interestrate|"
                       r"interestexpense|rightofuseasset|netinvestmentinlease|salestypelease|"
                       r"fixedmaturit|weightedaverage|allowance|unamortized|issuancecost|"
                       r"financingcost|covenant|redemption|conversion|numberof|percentage|"
                       r"paymentsof|accruedinterest|deferred|restrictedcash|grossnotes|noncash", re.I)
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
                leaves_nc, leaves_cur = {}, {}
                for t, v in bs.items():
                    if (v is None or t in _SUBTOTAL or t in _LEASE_TAGS or t in STBORROW
                            or LEASE_FIN_PAT.search(t) or LEASE_OP_PAT.search(t)):
                        continue
                    if DEBT_INCL.search(t) and not DEBT_EXCL.search(t):
                        (leaves_cur if _is_current(t) else leaves_nc)[t] = v
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
        cogs, tc = first(d, *COGS); put("cost_of_revenue", cogs, tc)
        gp0, _ = first(d, "GrossProfit")
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

    pretax, tp = first(d, *PRETAX); put("pretax_income", pretax, tp)
    tax, tt = first(d, *TAX); put("tax_expense", tax, tt)

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
    disc, _ = first(d, *DISC_OPS)
    if consol is None and pretax is not None and tax is not None:
        consol, tcon = pretax - tax + (disc or 0), "Pretax-Tax+Disc(derived)"
    parent, tpar = first(d, *NI_PARENT)
    nci_is, tnci = first(d, *NCI_IS)
    if parent is None and consol is not None and nci_is is not None:
        parent, tpar = consol - nci_is, "Consol-NCI(derived)"
    if parent is None and consol is not None and nci_is is None:
        parent, tpar = consol, tcon       # no NCI -> parent == consolidated
    put("net_income", parent, tpar)
    put("net_income_consolidated", consol, tcon)
    put("minority_interest", nci_is, tnci)
    nicom, tnicom = first(d, *NI_COMMON)
    pref, _ = first(d, *PREF_DIV)
    if nicom is None and parent is not None and pref is not None:
        nicom, tnicom = parent - pref, "Parent-Preferred(derived)"
    put("net_income_to_common", nicom, tnicom)

    put("discontinued_operations", disc, "IncomeLossFromDiscontinuedOperationsNetOfTax" if disc is not None else None)
    da, tda = first(d, *DA); put("depreciation_amortization", da, tda)
    ebitda = (oi + da) if (oi is not None and da is not None) else None
    put("ebitda", ebitda, "OperatingIncome+D&A(derived)" if ebitda is not None else None)
    inte, tie = first(d, *INT_EXP); put("interest_expense", inte, tie)

    # ---------- balance sheet ----------
    cash, tcash = first(d, *CASH); put("cash", cash, tcash)
    sti, tsti = first(d, *STI); put("short_term_investments", sti, tsti)
    ta, tta = first(d, *ASSETS); put("total_assets", ta, tta)
    put("total_current_assets", *first(d, *ASSETS_CUR))
    tl, ttl = first(d, *LIAB); put("total_liabilities", tl, ttl)
    put("total_current_liabilities", *first(d, *LIAB_CUR))
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
    cash_total = (cash + (rcash or 0)) if cash is not None else None   # cash + restricted
    put("cash_total", cash_total, "cash+restricted" if cash_total is not None else None)
    # ending-cash basis the CF reconciles to: restricted-inclusive post-2018, cash-only before
    cf_basis = cf_end if cf_end is not None else cash_total
    r["_cf_end"] = cf_end if restr_basis else None      # CF_BS_CASH only when restricted-inclusive
    r["_cash_total"] = cash_total
    r["_dcash"] = dcash
    r["_roll_cash"] = (cf_basis if restr_basis else cash)   # roll cash on the matching basis

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
    if sector == "commercial":
        tie("IS_GP(Rev-COGS)", r.get("gross_profit"),
            (None if r.get("revenue") is None or r.get("cost_of_revenue") is None
             else r["revenue"] - r["cost_of_revenue"]))
    tie("IS_NI(Pretax-Tax+Disc=Consol)", r.get("net_income_consolidated"),
        (None if pretax is None or tax is None else pretax - tax + (disc or 0)))
    tie("IS_NCI(Consol-Parent=NCI)",
        (None if consol is None or parent is None else consol - parent), nci_is)
    # cash-flow articulation (within-year), judged on MATERIALITY (1% / $2M) -- cash-flow
    # reconciliations carry small "other"/rounding noise that isn't a real break. The CF foots,
    # and its ending cash equals BS cash + restricted. The cross-year roll-forward is added in run().
    tie("CF_FOOT(CFO+CFI+CFF+FX=dCash)",
        (None if (r.get("cfo") is None or r.get("cfi") is None or r.get("cff") is None)
         else r["cfo"] + r["cfi"] + r["cff"] + (fx_c or 0)), dcash, rel=CF_TOL_REL, ab=CF_TOL_ABS)
    tie("CF_BS_CASH(CFend=cash+restr)", cf_end, cash_total, rel=CF_TOL_REL, ab=CF_TOL_ABS)
    return r, prov, ident


def run(facts_rows, sic_of=None):
    by = defaultdict(dict); bs = defaultdict(dict); meta = {}
    for r in facts_rows:
        v = fnum(r["value"])
        if v is None:
            continue
        key = (r["cik"], r["fiscal_year"])
        by[key][r["tag"]] = v
        # balance-sheet, reported-currency facts only -> debt reconstruction (stmt/uom absent in
        # the selftest, so treat blank as eligible)
        if r.get("stmt", "") in ("BS", "") and r.get("uom", "USD") in ("USD", ""):
            bs[key][r["tag"]] = v
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
        stage[key] = dict(rec=rec, ident=ident, sector=sector)

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
            ok = abs(lhs - rhs) <= max(CF_TOL_ABS, CF_TOL_REL * max(abs(lhs), abs(rhs)))
            st["ident"].append(("CASH_ROLL(cash[t]=cash[t-1]+dCash)", "tie" if ok else "BREAK", lhs - rhs))

    # ---- pass 3: finalize confidence/breaks over all identities (incl the cash-flow legs) ----
    out_rows = []; tie_rows = []
    for key in sorted(stage):
        cik, fy = key; st = stage[key]; rec = st["rec"]; ident = st["ident"]
        # confidence/breaks gate on the core articulation legs; NON_GATING legs are still reported
        ntie = sum(1 for n, s, _ in ident if s == "tie" and n not in NON_GATING)
        napp = sum(1 for n, s, _ in ident if s != "n/a" and n not in NON_GATING)
        conf = ntie / napp if napp else None
        broke = [n for n, s, _ in ident if s == "BREAK" and n not in NON_GATING]
        rec_full = dict(cik=cik, fiscal_year=fy, sector=st["sector"], taxonomy=meta[key][0],
                        form=meta[key][1], n_identities=napp, n_tie=ntie,
                        confidence=("%.2f" % conf if conf is not None else ""),
                        breaks=";".join(broke), **rec)
        out_rows.append(rec_full)
        for n, s, resid in ident:
            tie_rows.append(dict(cik=cik, fiscal_year=fy, sector=st["sector"], identity=n, result=s,
                                 residual=("" if resid is None else "%.0f" % resid)))
    return out_rows, tie_rows


def main():
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    facts = list(csv.DictReader(open(FACTS, encoding="utf-8")))
    sic_of = {}
    if INDEX.exists():
        for r in csv.DictReader(open(INDEX, encoding="utf-8")):
            sic_of.setdefault(r.get("cik", ""), r.get("sic", ""))
    out_rows, tie_rows = run(facts, sic_of)
    fields = ["cik", "fiscal_year", "sector", "taxonomy", "form", "n_identities", "n_tie",
              "confidence", "breaks", "revenue", "cost_of_revenue", "gross_profit",
              "operating_income", "ebitda", "depreciation_amortization", "interest_expense",
              "pretax_income", "tax_expense", "net_income_consolidated", "minority_interest",
              "discontinued_operations", "net_income", "net_income_to_common", "cash",
              "restricted_cash", "cash_total", "short_term_investments",
              "total_current_assets", "total_assets", "total_current_liabilities",
              "total_liabilities", "total_debt", "total_debt_incl_leases",
              "operating_lease_liability", "debt_flag", "parent_equity", "minority_interest_bs",
              "total_equity", "redeemable_nci", "cfo", "cfi", "cff", "capex", "free_cash_flow"]
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
    facts = []
    for cik, d in (("1", industrial), ("2", bank)):
        for tag, v in d.items():
            facts.append(dict(cik=cik, fiscal_year="2024", taxonomy="usgaap", form="10-K", tag=tag, value=str(v)))
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
    print(f"\n  SELFTEST industrial+bank cascade & identities: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
