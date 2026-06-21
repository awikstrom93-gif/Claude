"""
============================================================
r2k_step2_asfiled.py  --  PRODUCTION as-filed extraction engine
US Small Cap Growth Review
============================================================
Builds on the validated as-filed-by-accession pilot (105/112 vs ground truth)
and adds the pieces the analytics layer needs, ported from the legacy engine onto
the clean spine:

  + short-term & long-term investments, restricted cash (biotech runway/liquidity)
  + sector classification (bank/insurance/broker_dealer/reit) with financial carve-outs
  + IFRS foreign filers (20-F/40-F, ifrs-full taxonomy)
  + per-value sanity bounds (drop physically-impossible values; log them)
  + manual value-override file (manual_value_overrides.csv) for the residual cases
    (PLXS 2024 revenue, PDCE 2017 tax sign, MKSI re-check, ...)
  + full-universe runner (reads security_cik_map.json [+ temporal map])

CORE PRINCIPLE (unchanged): each constituent-year is read from that period's
ORIGINAL 10-K accession (submissions -> first-filed accession; companyfacts facts
filtered to that accn), native value preferred over any time-bounded fallback, so
there is no cross-vintage blending. Vintage-reconstruction heuristics stay deleted.

RUN
    cd "...\\Benchmark Analysis"
    python r2k_step2_asfiled.py            # full universe (security_cik_map.json)
    # or set R2KG_CIKS=regression_set.csv to run the regression set only
============================================================
"""
from pathlib import Path
from datetime import date, timedelta
import json, csv, os, time
try:
    import truststore; truststore.inject_into_ssl()
    _TLS = "truststore (Windows cert store)"
except Exception:
    _TLS = "default certifi"
import urllib.request, urllib.error

USER_AGENT = os.environ.get("SEC_UA", "Alex Wikstrom Research awikstrom93@gmail.com")
BASE_FOLDER = Path(os.environ.get("R2KG_BASE", "."))
CF_CACHE, SUB_CACHE = BASE_FOLDER / "companyfacts_cache", BASE_FOLDER / "submissions_cache"
CIK_MAP    = BASE_FOLDER / "security_cik_map.json"
TEMPORAL   = BASE_FOLDER / "temporal_cik_map.json"
REG_SET    = BASE_FOLDER / os.environ.get("R2KG_CIKS", "regression_set.csv")
LEGACY_CSV = BASE_FOLDER / "edgar_annual_fundamentals.csv"
OVERRIDES  = BASE_FOLDER / "manual_value_overrides.csv"
OUT_CSV    = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
PROV_CSV   = BASE_FOLDER / "asfiled_provenance.csv"
SANITY_CSV = BASE_FOLDER / "asfiled_sanity_log.csv"
FOREIGN_CSV= BASE_FOLDER / "asfiled_foreign_ifrs.csv"
DIFF_CSV   = BASE_FOLDER / "asfiled_vs_legacy_diff.csv"

MIN_YEAR, MAX_YEAR = 2010, 2026
DUR_LO, DUR_HI = 340, 380
FALLBACK_MAX_DAYS = 540
DURATION, INSTANT = "duration", "instant"

# ---- tag dictionaries (priority order; ported from the validated pilot + legacy) ----
REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
    "SalesRevenueGoodsNet", "RevenuesNetOfInterestExpense",
    # industry-specific TOP-LINE fallbacks (each a reliable consolidated total for a filer
    # type; segment/component tags like CasinoRevenue/PassengerRevenue are excluded by design).
    "SalesRevenueServicesNet",
    "HealthCareOrganizationRevenueNetOfPatientServiceRevenueProvisions",
    "HealthCareOrganizationRevenue", "HealthCareOrganizationPatientServiceRevenue",
    "RegulatedAndUnregulatedOperatingRevenue", "ContractsRevenue", "RealEstateRevenueNet",
    "RefiningAndMarketingRevenue", "OilAndGasRevenue", "OilAndGasSalesRevenue",
    "RevenueMineralSales", "RevenuesExcludingInterestAndDividends"]
EQUITY_PARENT = ["StockholdersEquity", "MembersEquity",
    "LimitedLiabilityCompanyLlcMembersEquityIncludingPortionAttributableToNoncontrollingInterest",
    "PartnersCapital"]
EQUITY_TOTAL  = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
NCI_TAGS = ["MinorityInterest"]
CASH_PRIMARY = ["CashAndCashEquivalentsAtCarryingValue",
    "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    "CashCashEquivalentsAndFederalFundsSold"]
CASH_COMBINED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
CASH_BARE = "Cash"
RESTRICTED_TOTAL = ["RestrictedCashAndCashEquivalentsAtCarryingValue", "RestrictedCash"]
RESTRICTED_CUR, RESTRICTED_NC = "RestrictedCashCurrent", "RestrictedCashNoncurrent"
STI_ROLLUP = ["ShortTermInvestments", "ShortTermInvestmentsAndMarketableSecurities", "MarketableSecuritiesCurrent"]
STI_AFS_NEW, STI_AFS_OLD = "AvailableForSaleSecuritiesDebtSecuritiesCurrent", "AvailableForSaleSecuritiesCurrent"
STI_OTHER = ["HeldToMaturitySecuritiesCurrent", "EquitySecuritiesFvNiCurrent", "TradingSecuritiesCurrent",
    "AvailableForSaleSecuritiesEquitySecuritiesCurrent", "OtherMarketableSecuritiesCurrent", "OtherShortTermInvestments"]
LTI_AFS_NEW, LTI_AFS_OLD = "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent", "AvailableForSaleSecuritiesNoncurrent"
LTI_ROLLUP = ["MarketableSecuritiesNoncurrent"]   # roll-up: already includes the AFS line -> use alone
LTI_OTHER = ["HeldToMaturitySecuritiesNoncurrent", "OtherLongTermInvestments"]
DEBT_LTNC = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]
DEBT_LTTOT = ["LongTermDebt"]
DEBT_LTCUR = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]
DEBT_ST = ["ShortTermBorrowings", "DebtCurrent"]
BANK_DEFINING = ["InterestExpenseDeposits", "InterestIncomeExpenseAfterProvisionForLoanLoss"]
BANK_NII, BANK_NONI = ["InterestIncomeExpenseNet"], ["NoninterestIncome"]
BANK_TII, BANK_TIE = ["InterestAndDividendIncomeOperating"], ["InterestExpenseOperating"]

TAGS = {
    "net_income": (DURATION, ["NetIncomeLoss", "ProfitLoss",
        "IncomeLossFromContinuingOperationsIncludingPortionAttributableToNoncontrollingInterest"]),
    "operating_income": (DURATION, ["OperatingIncomeLoss"]),
    "gross_profit": (DURATION, ["GrossProfit"]),
    "pretax_income": (DURATION, [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]),
    "tax_expense": (DURATION, ["IncomeTaxExpenseBenefit", "CurrentIncomeTaxExpenseBenefit"]),
    "total_assets": (INSTANT, ["Assets"]),
    "operating_cash_flow": (DURATION, ["NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": (DURATION, ["PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements",
        "PaymentsToAcquireMachineryAndEquipment"]),
}

# ---- sector classification (financials break the revenue/margin model) ----
SECTOR_SIG = {
    "bank": {"core": ["InterestExpenseDeposits", "InterestIncomeExpenseAfterProvisionForLoanLoss",
                      "NoninterestIncome", "NoninterestExpense"],
             "support": ["InterestIncomeExpenseNet", "InterestAndDividendIncomeOperating"]},
    "insurance": {"core": ["PremiumsEarnedNet", "PremiumsEarnedNetLife",
                           "PolicyholderBenefitsAndClaimsIncurredNet", "LiabilityForFuturePolicyBenefits",
                           "DeferredPolicyAcquisitionCosts"], "support": []},
    "broker_dealer": {"core": ["PayablesToBrokerDealersAndClearingOrganizations",
                               "ReceivablesFromBrokersDealersAndClearingOrganizations"],
                      "support": ["RevenuesNetOfInterestExpense"]},
    "reit": {"core": ["RealEstateInvestmentPropertyNet", "StraightLineRentAdjustments", "RealEstateRevenueNet"],
             "support": ["OperatingLeasesIncomeStatementLeaseRevenue"]},
}
FINANCIAL = {"bank", "insurance", "broker_dealer", "reit"}

SANE_MAX = {"revenue": 250e9, "net_income": 30e9, "operating_income": 30e9, "gross_profit": 30e9,
    "stockholders_equity": 100e9, "total_assets": 200e9, "cash": 50e9, "short_term_investments": 50e9,
    "long_term_investments": 50e9, "restricted_cash": 50e9, "total_debt": 80e9,
    "operating_cash_flow": 40e9, "capex": 40e9, "tax_expense": 15e9, "pretax_income": 30e9}

# ---- IFRS (foreign 20-F/40-F, ifrs-full) ----
IFRS_TAGS = {
    "revenue": (DURATION, ["RevenueFromContractsWithCustomers", "Revenue"]),
    "net_income": (DURATION, ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"]),
    "operating_income": (DURATION, ["ProfitLossFromOperatingActivities"]),
    "gross_profit": (DURATION, ["GrossProfit"]),
    "pretax_income": (DURATION, ["ProfitLossBeforeTax"]),
    "tax_expense": (DURATION, ["IncomeTaxExpenseContinuingOperations"]),
    "total_assets": (INSTANT, ["Assets"]),
    "operating_cash_flow": (DURATION, ["CashFlowsFromUsedInOperatingActivities",
        "NetCashFlowsFromUsedInOperatingActivities", "CashFlowsFromUsedInOperatingActivitiesContinuingOperations"]),
    "capex": (DURATION, ["PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipment", "PaymentsToAcquirePropertyPlantAndEquipment"]),
}
IFRS_EQUITY = ["EquityAttributableToOwnersOfParent", "Equity"]
IFRS_CASH = ["CashAndCashEquivalents"]
IFRS_LT_DEBT = ["NoncurrentBorrowings", "BorrowingsNoncurrent", "LongtermBorrowings"]
IFRS_ST_DEBT = ["CurrentBorrowings", "BorrowingsCurrent", "ShorttermBorrowings"]
IFRS_FORMS = ("20-F", "40-F")

OUTPUT_FIELDS = ["cik","ticker","name","fiscal_year","fye_date","revenue","net_income",
    "operating_income","gross_profit","tax_expense","pretax_income","stockholders_equity",
    "total_assets","cash","short_term_investments","long_term_investments","restricted_cash",
    "total_debt","operating_cash_flow","capex","free_cash_flow","reporting_basis","currency","sector"]


# ==============================================================
# fetch / accession plumbing
# ==============================================================
def _fetch(url, cache_path):
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8", errors="replace") or "{}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(4):
        try:
            body = urllib.request.urlopen(req, timeout=40).read()
            json.loads(body)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(body); time.sleep(0.12)
            return json.loads(body)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                cache_path.write_text("{}"); return {}
            time.sleep(1.5*(attempt+1))
        except Exception:
            time.sleep(1.0*(attempt+1))
    return {}


def keyed_year(fye):
    d = date.fromisoformat(fye)
    return d.year - 1 if (d.month == 1 and d.day <= 7) else d.year


def _days(a, b):
    try: return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except Exception: return None


def original_filing_map(cik, forms=("10-K",)):
    """{FYE -> (accn, filing_date)} for each period's FIRST-filed filing of the given forms,
    plus the set of all forms seen and the entity name."""
    c = str(int(cik)).zfill(10)
    j = _fetch(f"https://data.sec.gov/submissions/CIK{c}.json", SUB_CACHE / f"CIK{c}.json")
    rows, seen = [], set()
    rec = j.get("filings", {}).get("recent", {})
    rows += list(zip(rec.get("form",[]), rec.get("reportDate",[]), rec.get("accessionNumber",[]), rec.get("filingDate",[])))
    for extra in j.get("filings", {}).get("files", []):
        e = _fetch(f"https://data.sec.gov/submissions/{extra['name']}", SUB_CACHE / extra["name"])
        rows += list(zip(e.get("form",[]), e.get("reportDate",[]), e.get("accessionNumber",[]), e.get("filingDate",[])))
    orig = {}
    for form, rdate, accn, fdate in rows:
        if form: seen.add(form)
        if form not in forms or not rdate: continue
        if rdate not in orig or fdate < orig[rdate][1]:
            orig[rdate] = (accn, fdate)
    return orig, seen, j.get("name", "")


def _records(node, kind, fye, currency="USD"):
    out = []
    for r in node.get("units", {}).get(currency, []):
        if r.get("end") != fye: continue
        if kind == DURATION:
            d = _days(r.get("start",""), r.get("end",""))
            if d is None or not (DUR_LO <= d <= DUR_HI): continue
        out.append((r.get("filed",""), r.get("accn",""), r.get("val")))
    return out


def asfiled(node_dict, tags, kind, fye, accn, currency="USD"):
    """(val, tag, basis). Native (original accession) first across tags; else earliest-filed
    within FALLBACK_MAX_DAYS of period end (so retro-tags cannot contaminate)."""
    for tag in tags:
        node = node_dict.get(tag)
        if not node: continue
        for f, a, v in _records(node, kind, fye, currency):
            if a == accn and v is not None:
                return v, tag, "asfiled"
    try: cutoff = (date.fromisoformat(fye) + timedelta(days=FALLBACK_MAX_DAYS)).isoformat()
    except Exception: cutoff = None
    for tag in tags:
        node = node_dict.get(tag)
        if not node: continue
        recs = [(f,a,v) for f,a,v in _records(node, kind, fye, currency)
                if v is not None and f and (cutoff is None or f <= cutoff)]
        if recs:
            recs.sort(key=lambda r: r[0]); return recs[0][2], tag, "earliest-filed"
    return None, None, None


# ==============================================================
# selection (validated) + new component extractors
# ==============================================================
def classify_sector(usgaap):
    def has(t): return t in usgaap and usgaap[t].get("units")
    best, best_score = "general", (0, 0)
    for sector, sig in SECTOR_SIG.items():
        core = sum(1 for t in sig["core"] if has(t))
        total = core + sum(1 for t in sig["support"] if has(t))
        if core >= 1 and total >= 2 and (core, total) > best_score:
            best, best_score = sector, (core, total)
    return best


def select_revenue(usgaap, fye, accn, sector):
    # gather all revenue candidates (native preferred; fallback only if no native exists)
    allc = []
    for t in REVENUE_TAGS:
        v,_,b = asfiled(usgaap, [t], DURATION, fye, accn)
        if v is not None: allc.append((t, v, b))
    native = [(t, v) for t, v, b in allc if b == "asfiled"]
    pool = native if native else [(t, v) for t, v, b in allc]
    pos = [(t, v) for t, v in pool if v > 0]
    # `Revenues` is the us-gaap TOTAL-revenues element -- prefer it, BUT only when it is not a
    # FRAGMENT: it must be >= 70% of the largest other revenue total. Keeps PDCE (Revenues net
    # of a derivative contra, ~90% of the gross contract tag) and BE (Revenues > contract tag)
    # on Revenues, while rejecting filers where `Revenues` is a tiny "other revenue" line and the
    # real total sits elsewhere (AIT: Revenues 20M vs SalesRevenueNet 2.46B).
    rev = next((v for t, v in pos if t == "Revenues"), None)
    if rev is not None:
        others = [v for t, v in pos if t != "Revenues"]
        if rev >= 0.7 * (max(others) if others else 0):
            return rev, "Revenues"
    # depository bank with no usable total-revenue line -> NII + noninterest carve-out
    if sector == "bank":
        nii,_,_ = asfiled(usgaap, BANK_NII, DURATION, fye, accn)
        if nii is None:
            tii,_,_ = asfiled(usgaap, BANK_TII, DURATION, fye, accn)
            tie,_,_ = asfiled(usgaap, BANK_TIE, DURATION, fye, accn)
            if tii is not None and tie is not None: nii = tii - tie
        noni,_,_ = asfiled(usgaap, BANK_NONI, DURATION, fye, accn)
        if nii is not None or noni is not None:
            return (nii or 0)+(noni or 0), "bank:NII+noninterest"
    # else the LARGEST real total among NON-Revenues candidates (rejects segment fragments like
    # ABG's services line); if none positive, the priority pick (preserves a genuine negative).
    nonrev_pos = [(t, v) for t, v in pos if t != "Revenues"]
    if nonrev_pos:
        ctag, cval = max(nonrev_pos, key=lambda x: x[1])
        return cval, ctag
    nonrev = [(t, v) for t, v in pool if t != "Revenues"]
    if nonrev: return nonrev[0][1], nonrev[0][0]
    return (rev, "Revenues") if rev is not None else (None, None)


def select_equity(usgaap, fye, accn):
    se, setag, _ = asfiled(usgaap, EQUITY_PARENT, INSTANT, fye, accn)
    tot,_,_ = asfiled(usgaap, [EQUITY_TOTAL], INSTANT, fye, accn)
    nci,_,_ = asfiled(usgaap, NCI_TAGS, INSTANT, fye, accn)
    if tot is not None:
        implied = tot - (nci or 0)
        if se is None: return implied, "inclNCI_minus_NCI"
        if abs(se - implied) > max(abs(implied), 1)*0.02 and abs(se - implied) > 1e6:
            return implied, "inclNCI_minus_NCI(repair)"
    return se, setag


def extract_restricted(usgaap, fye, accn):
    cur,_,_ = asfiled(usgaap, [RESTRICTED_CUR], INSTANT, fye, accn)
    nc,_,_ = asfiled(usgaap, [RESTRICTED_NC], INSTANT, fye, accn)
    if cur is not None or nc is not None: return (cur or 0)+(nc or 0)
    for t in RESTRICTED_TOTAL:
        v,_,_ = asfiled(usgaap, [t], INSTANT, fye, accn)
        if v is not None: return v
    return None


def select_cash(usgaap, fye, accn, restricted):
    for t in CASH_PRIMARY:
        v,_,_ = asfiled(usgaap, [t], INSTANT, fye, accn)
        if v is not None: return v
    comb,_,_ = asfiled(usgaap, [CASH_COMBINED], INSTANT, fye, accn)
    if comb is not None:
        derived = comb - (restricted or 0)
        if derived > 0: return derived
    v,_,_ = asfiled(usgaap, [CASH_BARE], INSTANT, fye, accn)
    return v


def _sum_components(usgaap, fye, accn, rollup, afs_new, afs_old, others):
    for t in rollup:
        v,_,_ = asfiled(usgaap, [t], INSTANT, fye, accn)
        if v is not None: return v
    s, got = 0.0, False
    afs,_,_ = asfiled(usgaap, [afs_new], INSTANT, fye, accn)
    if afs is None: afs,_,_ = asfiled(usgaap, [afs_old], INSTANT, fye, accn)
    if afs is not None: s += afs; got = True
    for t in others:
        v,_,_ = asfiled(usgaap, [t], INSTANT, fye, accn)
        if v is not None: s += v; got = True
    return s if got else None


def extract_sti(usgaap, fye, accn):
    return _sum_components(usgaap, fye, accn, STI_ROLLUP, STI_AFS_NEW, STI_AFS_OLD, STI_OTHER)


def extract_lti(usgaap, fye, accn):
    return _sum_components(usgaap, fye, accn, LTI_ROLLUP, LTI_AFS_NEW, LTI_AFS_OLD, LTI_OTHER)


def extract_total_debt(usgaap, fye, accn):
    def g(tags): return asfiled(usgaap, tags, INSTANT, fye, accn)[0]
    ltnc, ltcur, lttot, st = g(DEBT_LTNC), g(DEBT_LTCUR), g(DEBT_LTTOT), g(DEBT_ST)
    if ltnc is not None:   total = ltnc + (ltcur or 0) + (st or 0)
    elif lttot is not None: total = lttot + (st or 0)
    elif ltcur is not None or st is not None: total = (ltcur or 0) + (st or 0)
    else: return None
    return abs(total)


# ==============================================================
# per-company extraction
# ==============================================================
def extract_usgaap(usgaap, orig, name):
    sector = classify_sector(usgaap)
    out, prov = {}, []
    for fye, (accn, fdate) in sorted(orig.items()):
        y = keyed_year(fye)
        if y < MIN_YEAR or y > MAX_YEAR: continue
        row = {"fye_date": fye, "filed": fdate, "reporting_basis": "us-gaap",
               "currency": "USD", "sector": sector}
        for metric, (kind, tags) in TAGS.items():
            v, tag, basis = asfiled(usgaap, tags, kind, fye, accn)
            row[metric] = v
            if v is not None: prov.append([y, metric, tag, basis, accn, v])
        rev, rtag = select_revenue(usgaap, fye, accn, sector)
        row["revenue"] = rev
        if rev is not None: prov.append([y, "revenue", rtag, "", accn, rev])
        row["stockholders_equity"], _ = select_equity(usgaap, fye, accn)
        rc = extract_restricted(usgaap, fye, accn)
        row["restricted_cash"] = rc
        row["cash"] = select_cash(usgaap, fye, accn, rc)
        row["short_term_investments"] = extract_sti(usgaap, fye, accn)
        row["long_term_investments"] = extract_lti(usgaap, fye, accn)
        row["total_debt"] = extract_total_debt(usgaap, fye, accn)
        # financial-sector carve-outs (revenue/margin model doesn't apply)
        if sector in FINANCIAL: row["gross_profit"] = None
        if sector in ("bank", "insurance"): row["operating_income"] = None
        if row.get("capex") is not None: row["capex"] = abs(row["capex"])
        if row.get("total_debt") is not None: row["total_debt"] = abs(row["total_debt"])
        cfo = row.get("operating_cash_flow")
        row["free_cash_flow"] = (cfo - (row.get("capex") or 0)) if cfo is not None else None
        if row.get("total_assets") == 0:            # shell year: blank all balance-sheet items
            for bs in ("total_assets","stockholders_equity","cash","total_debt",
                       "short_term_investments","long_term_investments","restricted_cash"):
                row[bs] = None
        out[y] = row
    return out, prov, sector


def extract_ifrs(ifrs, orig, name):
    # presentation currency: prefer USD else most-common
    from collections import Counter
    cur = Counter()
    for t in ["Assets", "Revenue", "RevenueFromContractsWithCustomers", "Equity"]:
        node = ifrs.get(t)
        if node:
            for u, recs in node.get("units", {}).items(): cur[u] += len(recs)
    currency = "USD" if "USD" in cur else (cur.most_common(1)[0][0] if cur else None)
    if currency is None: return {}, []
    out, prov = {}, []
    for fye, (accn, fdate) in sorted(orig.items()):
        y = keyed_year(fye)
        if y < MIN_YEAR or y > MAX_YEAR: continue
        row = {"fye_date": fye, "filed": fdate, "reporting_basis": "ifrs",
               "currency": currency, "sector": "general"}
        for metric, (kind, tags) in IFRS_TAGS.items():
            v, tag, basis = asfiled(ifrs, tags, kind, fye, accn, currency)
            row[metric] = v
            if v is not None: prov.append([y, metric, tag, basis, accn, v])
        row["stockholders_equity"], _, _ = asfiled(ifrs, IFRS_EQUITY, INSTANT, fye, accn, currency)
        row["cash"], _, _ = asfiled(ifrs, IFRS_CASH, INSTANT, fye, accn, currency)
        nc,_,_ = asfiled(ifrs, IFRS_LT_DEBT, INSTANT, fye, accn, currency)
        st,_,_ = asfiled(ifrs, IFRS_ST_DEBT, INSTANT, fye, accn, currency)
        row["total_debt"] = abs((nc or 0)+(st or 0)) if (nc is not None or st is not None) else None
        for f in ("short_term_investments","long_term_investments","restricted_cash"): row[f] = None
        if row.get("capex") is not None: row["capex"] = abs(row["capex"])
        cfo = row.get("operating_cash_flow")
        row["free_cash_flow"] = (cfo - (row.get("capex") or 0)) if cfo is not None else None
        out[y] = row
    return out, prov


def extract_company(cik):
    """Returns (name, {year: row}, provenance, status). status in ok|ifrs|foreign|no_data."""
    c = str(int(cik)).zfill(10)
    cf = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", CF_CACHE / f"CIK{c}.json")
    facts = cf.get("facts", {})
    usgaap = facts.get("us-gaap", {})
    orig10k, forms, name = original_filing_map(cik, ("10-K",))
    has_10k = any(f.startswith("10-K") for f in forms)
    if usgaap and has_10k and orig10k:
        out, prov, sector = extract_usgaap(usgaap, orig10k, name)
        if out: return name, out, prov, "ok"
    # foreign annual filers
    origf, formsf, _ = original_filing_map(cik, IFRS_FORMS)
    has_foreign = any(f.startswith(IFRS_FORMS) for f in forms | formsf)
    if usgaap and has_foreign and origf:               # foreign private issuer on us-gaap
        out, prov, sector = extract_usgaap(usgaap, origf, name)
        if out: return name, out, prov, "ok"
    ifrs = facts.get("ifrs-full", {})
    if ifrs and has_foreign and origf:
        out, prov = extract_ifrs(ifrs, origf, name)
        if out: return name, out, prov, "ifrs"
    if ifrs or has_foreign:
        return name, {}, [], "foreign"
    return name, {}, [], "no_data"


# ==============================================================
# sanity + overrides
# ==============================================================
def apply_sanity(cik, ticker, annual):
    """Drop physically-impossible values (hard bounds); log each. Relationship breaks are
    FLAGGED, never dropped (one side is wrong and we can't tell which)."""
    log = []
    for y, row in annual.items():
        for field, limit in SANE_MAX.items():
            v = row.get(field)
            if v is not None and abs(v) > limit:
                log.append([cik, ticker, y, field, f"|val|>{limit:,.0f} DROPPED", v]); row[field] = None
        rev, ni = row.get("revenue"), row.get("net_income")
        cash, ta = row.get("cash"), row.get("total_assets")
        if cash is not None and ta is not None and ta > 0 and cash > ta*1.02:
            log.append([cik, ticker, y, "cash", "FLAG cash>assets (kept)", cash])
    return log


ALIAS = {"cfo":"operating_cash_flow","fcf":"free_cash_flow","equity":"stockholders_equity",
    "sti":"short_term_investments","lti":"long_term_investments"}

def load_overrides():
    if not OVERRIDES.exists(): return {}
    ov, bad = {}, 0
    for r in csv.DictReader(open(OVERRIDES, encoding="utf-8-sig")):
        cik = str(r.get("cik","")).strip().zfill(10)
        metric = ALIAS.get(str(r.get("metric","")).strip().lower(), str(r.get("metric","")).strip().lower())
        try: fy = int(r["fiscal_year"])
        except Exception: bad += 1; continue
        raw = str(r.get("value","")).strip()
        if metric == "fye_date": val = raw or None
        elif raw == "": val = None
        else:
            try: val = float(raw.replace(",","").replace("$",""))
            except ValueError: bad += 1; continue
        ov.setdefault((cik, fy), {})[metric] = (val, str(r.get("source","")).strip())
    if bad: print(f"  !! manual_value_overrides.csv: skipped {bad} malformed row(s)")
    return ov

def apply_overrides(cik, annual, overrides, log):
    for (ocik, fy), metrics in overrides.items():
        if ocik != cik: continue
        row = annual.setdefault(fy, {"fye_date": f"{fy}-12-31", "reporting_basis":"manual","currency":"USD","sector":"general"})
        for metric, (val, src) in metrics.items():
            log.append([cik, fy, metric, row.get(metric), val, src]); row[metric] = val
        if "free_cash_flow" not in metrics and row.get("operating_cash_flow") is not None:
            row["free_cash_flow"] = row["operating_cash_flow"] - (row.get("capex") or 0)


# ==============================================================
# main (full universe)
# ==============================================================
def load_cik_list():
    ciks = {}
    if CIK_MAP.exists():
        for tk, m in json.load(open(CIK_MAP)).items():
            cik = (m.get("cik") if isinstance(m, dict) else m)
            ciks[str(cik).zfill(10)] = (m.get("ticker", tk) if isinstance(m, dict) else tk,
                                        m.get("name","") if isinstance(m, dict) else "")
    if TEMPORAL.exists():
        for snap, d in json.load(open(TEMPORAL)).items():
            for tk, c in d.items():
                if c: ciks.setdefault(str(c).zfill(10), (tk, ""))
    if not ciks and REG_SET.exists():
        for r in csv.DictReader(open(REG_SET, encoding="utf-8-sig")):
            ciks[str(r["cik"]).zfill(10)] = (r["ticker"], r.get("name",""))
    return ciks


def main():
    print(f"TLS: {_TLS}")
    ciks = load_cik_list()
    print(f"Extracting {len(ciks)} CIKs (as-filed)...")
    overrides = load_overrides()
    rows, prov_rows, sanity_log, override_log, foreign = [], [], [], [], []
    n_ok = n_ifrs = n_foreign = n_nodata = 0
    for i, (cik, (ticker, nm)) in enumerate(sorted(ciks.items()), 1):
        try:
            name, annual, prov, status = extract_company(cik)
        except Exception as e:
            sanity_log.append([cik, ticker, "", "ERROR", f"{type(e).__name__}:{str(e)[:60]}", ""]); continue
        if status in ("foreign","no_data") or not annual:
            if status == "foreign": n_foreign += 1; foreign.append([cik, ticker, name, status])
            else: n_nodata += 1
            continue
        sanity_log += apply_sanity(cik, ticker, annual)
        if overrides: apply_overrides(cik, annual, overrides, override_log)
        n_ok += 1; n_ifrs += (status == "ifrs")
        for y in sorted(annual):
            d = annual[y]; d.update(cik=cik, ticker=ticker, name=name or nm, fiscal_year=y)
            rows.append([d.get(f) for f in OUTPUT_FIELDS])
        for pr in prov: prov_rows.append([cik, ticker] + pr)
        if i % 250 == 0: print(f"    {i}/{len(ciks)}  (ok {n_ok}, ifrs {n_ifrs}, foreign {n_foreign}, none {n_nodata})")

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(OUTPUT_FIELDS); w.writerows(rows)
    with open(PROV_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","ticker","fiscal_year","metric","chosen_tag","basis","accession","value"]); w.writerows(prov_rows)
    with open(SANITY_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","ticker","fiscal_year","field","reason","value"]); w.writerows(sanity_log)
    with open(FOREIGN_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","ticker","name","status"]); w.writerows(foreign)
    if override_log:
        with open(BASE_FOLDER/"asfiled_override_log.csv","w",newline="",encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["cik","fiscal_year","metric","old","new","source"]); w.writerows(override_log)
    print(f"\n  US/foreign-usgaap ok: {n_ok - n_ifrs}   IFRS: {n_ifrs}   foreign(unextracted): {n_foreign}   no_data: {n_nodata}")
    print(f"  rows: {len(rows)} -> {OUT_CSV.name}")

    # oracle diff vs legacy
    if LEGACY_CSV.exists():
        legacy = {(str(int(r["cik"])), int(r["fiscal_year"])): r for r in csv.DictReader(open(LEGACY_CSV, encoding="utf-8-sig"))}
        METRICS = [m for m in OUTPUT_FIELDS if m not in ("cik","ticker","name","fiscal_year","fye_date","reporting_basis","currency","sector")]
        diffs = []
        for row in rows:
            d = dict(zip(OUTPUT_FIELDS, row)); lg = legacy.get((str(int(d["cik"])), int(d["fiscal_year"])))
            if not lg: continue
            for m in METRICS:
                try: a = float(d.get(m)) if d.get(m) not in (None,"") else None
                except: a = None
                try: b = float(lg.get(m)) if lg.get(m) not in (None,"") else None
                except: b = None
                if a is None and b is None: continue
                if a is None or b is None: diffs.append([d["ticker"], d["fiscal_year"], m, a, b, "PRESENCE"]); continue
                if abs(a-b)/max(abs(a),abs(b),1.0) > 0.005:
                    diffs.append([d["ticker"], d["fiscal_year"], m, a, b, f"{(a-b)/max(abs(a),abs(b),1.0)*100:+.1f}%"])
        with open(DIFF_CSV, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["ticker","fiscal_year","metric","asfiled","legacy","diff"]); w.writerows(diffs)
        print(f"  oracle diff vs legacy: {len(diffs)} cells -> {DIFF_CSV.name}")


if __name__ == "__main__":
    main()
