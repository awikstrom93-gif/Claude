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
import os, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FACTS = BASE / "dera_facts.csv"
INDEX = BASE / "dera_filing_index.csv"          # optional, for SIC
OUT = BASE / "fundamentals_dera.csv"
TIEOUT = BASE / "tieout_report.csv"
TOL_REL, TOL_ABS = 0.005, 5000.0                # identity tie tolerance


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
EQ_PARENT = ["StockholdersEquity"]
EQ_INCL = ["StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
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
# bank / insurer markers and lines
BANK_NII_NET = ["InterestIncomeExpenseNet", "InterestIncomeExpenseAfterProvisionForLoanLoss"]
BANK_INT_INC = ["InterestAndDividendIncomeOperating", "InterestAndFeeIncomeLoansAndLeases"]
NONINT_INC = ["NoninterestIncome", "NoninterestIncomeOther"]
NONINT_EXP = ["NoninterestExpense"]
PROVISION = ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanAndLeaseLosses",
             "ProvisionForLoanLossesExpensed"]
INS_PREMIUMS = ["PremiumsEarnedNet"]
INS_BENEFITS = ["BenefitsLossesAndExpenses", "PolicyholderBenefitsAndClaimsIncurredNet"]


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
            # only valid if GP - OpEx lands near operating income, not pretax (position check downstream)
            oi, toi = r["gross_profit"] - opex, "GP-OpEx(derived)"
    put("operating_income", oi, toi)

    # net income: consolidated (incl NCI) and parent
    consol, tcon = first(d, *NI_CONSOL)
    if consol is None and pretax is not None and tax is not None:
        consol, tcon = pretax - tax, "Pretax-Tax(derived)"
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
    # total debt = current + long-term (lease-inclusive); sum present components, else None
    ltnc, _ = first(d, *DEBT_LTNC); lttot, _ = first(d, *DEBT_LTTOT)
    cur, _ = first(d, *DEBT_CUR); loc, _ = first(d, *LINE_OF_CREDIT)
    lt = ltnc if ltnc is not None else lttot
    parts = [x for x in (lt, cur, loc) if x is not None]
    put("total_debt", sum(parts) if parts else None,
        "sum(LT+current+LOC)" if parts else None)

    # ---------- cash flow ----------
    put("cfo", *first(d, *CFO))
    put("cfi", *first(d, *CFI))
    put("cff", *first(d, *CFF))
    capex, tcx = first(d, *CAPEX); put("capex", capex, tcx)
    put("free_cash_flow",
        (r.get("cfo") - abs(capex)) if (r.get("cfo") is not None and capex is not None) else None,
        "CFO-|Capex|(derived)" if (r.get("cfo") is not None and capex is not None) else None)

    # ---------- identity tie-outs ----------
    ident = []
    def tie(name, lhs, rhs):
        if lhs is None or rhs is None:
            ident.append((name, "n/a", None)); return
        ok = abs(lhs - rhs) <= max(TOL_ABS, TOL_REL * max(abs(lhs), abs(rhs)))
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
    tie("IS_NI(Pretax-Tax=Consol)", r.get("net_income_consolidated"),
        (None if pretax is None or tax is None else pretax - tax))
    tie("IS_NCI(Consol-Parent=NCI)",
        (None if consol is None or parent is None else consol - parent), nci_is)
    return r, prov, ident


def run(facts_rows, sic_of=None):
    by = defaultdict(dict); meta = {}
    for r in facts_rows:
        v = fnum(r["value"])
        if v is None:
            continue
        key = (r["cik"], r["fiscal_year"])
        by[key][r["tag"]] = v
        meta[key] = (r.get("taxonomy", ""), r.get("form", ""))
    out_rows = []; tie_rows = []
    for key in sorted(by):
        cik, fy = key
        d = by[key]
        sector = detect_sector(d)
        rec, prov, ident = classify_filing(d, sector)
        ntie = sum(1 for _, s, _ in ident if s == "tie")
        napp = sum(1 for _, s, _ in ident if s != "n/a")
        conf = ntie / napp if napp else None
        broke = [n for n, s, _ in ident if s == "BREAK"]
        rec_full = dict(cik=cik, fiscal_year=fy, sector=sector, taxonomy=meta[key][0],
                        form=meta[key][1], n_identities=napp, n_tie=ntie,
                        confidence=("%.2f" % conf if conf is not None else ""),
                        breaks=";".join(broke), **rec)
        out_rows.append(rec_full)
        for n, s, resid in ident:
            tie_rows.append(dict(cik=cik, fiscal_year=fy, sector=sector, identity=n, result=s,
                                 residual=("" if resid is None else "%.0f" % resid)))
    return out_rows, tie_rows


def main():
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    facts = list(csv.DictReader(open(FACTS, encoding="utf-8")))
    out_rows, tie_rows = run(facts)
    fields = ["cik", "fiscal_year", "sector", "taxonomy", "form", "n_identities", "n_tie",
              "confidence", "breaks", "revenue", "cost_of_revenue", "gross_profit",
              "operating_income", "ebitda", "depreciation_amortization", "interest_expense",
              "pretax_income", "tax_expense", "net_income_consolidated", "minority_interest",
              "net_income", "net_income_to_common", "cash", "short_term_investments",
              "total_current_assets", "total_assets", "total_current_liabilities",
              "total_liabilities", "total_debt", "parent_equity", "minority_interest_bs",
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
                  "PaymentsToAcquirePropertyPlantAndEquipment": 40, "LongTermDebt": 800, "DebtCurrent": 50}
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
