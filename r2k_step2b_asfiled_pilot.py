"""
============================================================
PILOT v2: r2k_step2b_asfiled_pilot.py
US Small Cap Growth Review -- as-FILED extraction engine
============================================================
Revision adds the concept-definition heuristics the triage identified as
needing to be ported (vintage machinery stays deleted):

  FIX 1  Revenue "presented consolidated total": priority -> prefer-positive ->
         take `Revenues` when it is the larger complete total (PLXS neg-elim,
         BE non-606 financing/electricity revenue, CHDN zero/segment).
  FIX 2  Pretax priority: the full income-statement EBT (incl. equity-method)
         wins over the pre-equity-method subtotal (TRC).
  FIX 3  Equity identity repair: parent = incl-NCI - NCI; when the filer's
         StockholdersEquity tag disagrees with that identity it is a mis-tag,
         use the reconciled value (NNBR 2019/2021).
  FIX 4  Cash fallback: combined cash+restricted MINUS restricted; bare Cash
         (MGLN, CMC -- no standalone CashAndCashEquivalents tag).
  FIX 5  Total-debt assembly corrected.
  FIX 6  Early-XBRL recovery: when the original accession carries no fact, fall
         back to the EARLIEST-filed record -- but ONLY within FALLBACK_MAX_DAYS
         of the period end, so a years-later retro-tag (e.g. ASC-606 RFCwC on a
         pre-606 year) can never contaminate (keeps PDCE 2016 correct).

Outputs unchanged: edgar_annual_fundamentals_ASFILED.csv, asfiled_provenance.csv,
asfiled_vs_legacy_diff.csv.
============================================================
"""
from pathlib import Path
from datetime import date, timedelta
import json, csv, os, time
try:
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass
import urllib.request

USER_AGENT = os.environ.get("SEC_UA", "Alex Wikstrom Research awikstrom93@gmail.com")
BASE_FOLDER = Path(os.environ.get("R2KG_BASE", "."))
CF_CACHE, SUB_CACHE = BASE_FOLDER / "companyfacts_cache", BASE_FOLDER / "submissions_cache"
REG_SET    = BASE_FOLDER / "regression_set.csv"
LEGACY_CSV = BASE_FOLDER / "edgar_annual_fundamentals.csv"
OUT_CSV    = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
PROV_CSV   = BASE_FOLDER / "asfiled_provenance.csv"
DIFF_CSV   = BASE_FOLDER / "asfiled_vs_legacy_diff.csv"

MIN_YEAR, MAX_YEAR = 2010, 2026
DUR_LO, DUR_HI = 340, 380
FALLBACK_MAX_DAYS = 540          # original + first annual comparative; excludes multi-yr retro-tags
DURATION, INSTANT = "duration", "instant"

REVENUE_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
    "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
    "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"]
EQUITY_PARENT = ["StockholdersEquity", "MembersEquity", "PartnersCapital"]
EQUITY_TOTAL  = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
NCI_TAGS = ["MinorityInterest"]
CASH_PRIMARY = ["CashAndCashEquivalentsAtCarryingValue",
    "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
    "CashCashEquivalentsAndFederalFundsSold"]
CASH_COMBINED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
RESTRICTED_TOTAL = ["RestrictedCashAndCashEquivalentsAtCarryingValue", "RestrictedCash"]
RESTRICTED_CUR, RESTRICTED_NC = "RestrictedCashCurrent", "RestrictedCashNoncurrent"

TAGS = {
    "net_income": (DURATION, ["NetIncomeLoss", "ProfitLoss"]),
    "operating_income": (DURATION, ["OperatingIncomeLoss"]),
    "gross_profit": (DURATION, ["GrossProfit"]),
    "pretax_income": (DURATION, [   # FIX 2: full EBT (incl. equity-method) first
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]),
    "tax_expense": (DURATION, ["IncomeTaxExpenseBenefit"]),
    "total_assets": (INSTANT, ["Assets"]),
    "operating_cash_flow": (DURATION, ["NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": (DURATION, ["PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets", "PaymentsForCapitalImprovements",
        "PaymentsToAcquireMachineryAndEquipment"]),
}
DEBT_LTNC = ["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"]
DEBT_LTTOT = ["LongTermDebt"]
DEBT_LTCUR = ["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"]
DEBT_ST = ["ShortTermBorrowings", "DebtCurrent"]
BANK_DEFINING = ["InterestExpenseDeposits", "InterestIncomeExpenseAfterProvisionForLoanLoss"]
BANK_NII, BANK_NONI = ["InterestIncomeExpenseNet"], ["NoninterestIncome"]

OUTPUT_FIELDS = ["cik","ticker","name","fiscal_year","fye_date","revenue","net_income",
    "operating_income","gross_profit","tax_expense","pretax_income","stockholders_equity",
    "total_assets","cash","total_debt","operating_cash_flow","capex","free_cash_flow",
    "reporting_basis","currency","sector"]


def _fetch(url, cache_path):
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8", errors="replace") or "{}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    body = urllib.request.urlopen(req, timeout=40).read()
    json.loads(body)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(body); time.sleep(0.13)
    return json.loads(body)


def keyed_year(fye):
    d = date.fromisoformat(fye)
    return d.year - 1 if (d.month == 1 and d.day <= 7) else d.year


def _days(a, b):
    try: return (date.fromisoformat(b) - date.fromisoformat(a)).days
    except Exception: return None


def original_10k_map(cik):
    c = str(int(cik)).zfill(10)
    j = _fetch(f"https://data.sec.gov/submissions/CIK{c}.json", SUB_CACHE / f"CIK{c}.json")
    rows, rec = [], j.get("filings", {}).get("recent", {})
    rows += list(zip(rec.get("form",[]), rec.get("reportDate",[]),
                     rec.get("accessionNumber",[]), rec.get("filingDate",[])))
    for extra in j.get("filings", {}).get("files", []):
        e = _fetch(f"https://data.sec.gov/submissions/{extra['name']}", SUB_CACHE / extra["name"])
        rows += list(zip(e.get("form",[]), e.get("reportDate",[]),
                         e.get("accessionNumber",[]), e.get("filingDate",[])))
    orig = {}
    for form, rdate, accn, fdate in rows:
        if form != "10-K" or not rdate: continue
        if rdate not in orig or fdate < orig[rdate][1]:
            orig[rdate] = (accn, fdate)
    return orig, j.get("name", "")


def _records(node, kind, fye, currency="USD"):
    out = []
    for r in node.get("units", {}).get(currency, []):
        if r.get("end") != fye: continue
        if kind == DURATION:
            d = _days(r.get("start",""), r.get("end",""))
            if d is None or not (DUR_LO <= d <= DUR_HI): continue
        out.append((r.get("filed",""), r.get("accn",""), r.get("val")))
    return out


def asfiled(usgaap, tags, kind, fye, accn, fallback=True):
    """(val, tag, basis). Original accession first; else earliest-filed within
    FALLBACK_MAX_DAYS of the period end (so retro-tags can't contaminate)."""
    for tag in tags:
        node = usgaap.get(tag)
        if not node: continue
        for f, a, v in _records(node, kind, fye):
            if a == accn and v is not None:
                return v, tag, "asfiled"
    if fallback:
        try: cutoff = (date.fromisoformat(fye) + timedelta(days=FALLBACK_MAX_DAYS)).isoformat()
        except Exception: cutoff = None
        for tag in tags:
            node = usgaap.get(tag)
            if not node: continue
            recs = [(f,a,v) for f,a,v in _records(node, kind, fye)
                    if v is not None and f and (cutoff is None or f <= cutoff)]
            if recs:
                recs.sort(key=lambda r: r[0])
                return recs[0][2], tag, "earliest-filed"
    return None, None, None


def select_revenue(usgaap, fye, accn, bank):
    # `Revenues` is the us-gaap TOTAL-revenues element. When a filer tags it, it is the
    # presented income-statement top line -- LARGER than the contract-revenue tag for
    # non-606-revenue filers (BE: financing/electricity) and SMALLER for filers with a
    # negative contra line inside total revenue (PDCE: net of commodity derivatives).
    # Either way it is the reported total, so prefer it. (A real depository bank has no
    # `Revenues` line, so this also keeps StoneX-type financials off the bank carve-out.)
    revtot,_,_ = asfiled(usgaap, ["Revenues"], DURATION, fye, accn)
    if revtot is not None and revtot > 0:
        return revtot, "Revenues"
    if bank:
        nii,_,_ = asfiled(usgaap, BANK_NII, DURATION, fye, accn)
        noni,_,_ = asfiled(usgaap, BANK_NONI, DURATION, fye, accn)
        if nii is not None or noni is not None:
            return (nii or 0)+(noni or 0), "bank:NII+noninterest"
    cands = [(t, asfiled(usgaap,[t],DURATION,fye,accn)[0]) for t in REVENUE_TAGS if t != "Revenues"]
    cands = [(t,v) for t,v in cands if v is not None]
    if not cands: return None, None
    ctag, cval = cands[0]
    if cval <= 0:                                   # prefer-positive (neg contra mis-tag)
        pos = [(t,v) for t,v in cands if v > 0]
        if pos: ctag, cval = pos[0]
    return cval, ctag


def select_equity(usgaap, fye, accn):
    se, setag, _ = asfiled(usgaap, EQUITY_PARENT, INSTANT, fye, accn)
    tot,_,_ = asfiled(usgaap, [EQUITY_TOTAL], INSTANT, fye, accn)
    nci,_,_ = asfiled(usgaap, NCI_TAGS, INSTANT, fye, accn)
    if tot is not None:
        implied = tot - (nci or 0)                  # FIX 3: parent = total - NCI (identity)
        if se is None:
            return implied, "inclNCI_minus_NCI"
        if abs(se - implied) > max(abs(implied), 1) * 0.02 and abs(se - implied) > 1e6:
            return implied, "inclNCI_minus_NCI(repair:SE_mistag)"
    return se, setag


def select_cash(usgaap, fye, accn):
    for tag in CASH_PRIMARY:
        v,_,_ = asfiled(usgaap, [tag], INSTANT, fye, accn)
        if v is not None: return v, tag
    comb,_,_ = asfiled(usgaap, [CASH_COMBINED], INSTANT, fye, accn)   # FIX 4
    if comb is not None:
        rc = None
        for rt in RESTRICTED_TOTAL:
            v,_,_ = asfiled(usgaap, [rt], INSTANT, fye, accn)
            if v is not None: rc = v; break
        if rc is None:
            cur,_,_ = asfiled(usgaap, [RESTRICTED_CUR], INSTANT, fye, accn)
            nc,_,_ = asfiled(usgaap, [RESTRICTED_NC], INSTANT, fye, accn)
            if cur is not None or nc is not None: rc = (cur or 0) + (nc or 0)
        derived = comb - (rc or 0)
        if derived > 0: return derived, "combined_minus_restricted"
    v,_,_ = asfiled(usgaap, ["Cash"], INSTANT, fye, accn)
    if v is not None: return v, "Cash"
    return None, None


def extract_total_debt(usgaap, fye, accn):                            # FIX 5
    def g(tags):
        v,_,_ = asfiled(usgaap, tags, INSTANT, fye, accn); return v
    ltnc, ltcur, lttot, st = g(DEBT_LTNC), g(DEBT_LTCUR), g(DEBT_LTTOT), g(DEBT_ST)
    if ltnc is not None:   total = ltnc + (ltcur or 0) + (st or 0)
    elif lttot is not None: total = lttot + (st or 0)
    elif ltcur is not None or st is not None: total = (ltcur or 0) + (st or 0)
    else: return None
    return abs(total)


def is_bank(usgaap):
    # require a DEPOSIT or LOAN-LOSS signature -- only banks take deposits / provision
    # loans. NoninterestIncome/Expense alone false-positives broker-dealers (e.g. StoneX).
    return any(usgaap.get(t) for t in BANK_DEFINING)


def extract_company(cik):
    c = str(int(cik)).zfill(10)
    cf = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", CF_CACHE / f"CIK{c}.json")
    usgaap = cf.get("facts", {}).get("us-gaap", {})
    orig, name = original_10k_map(cik)
    bank = is_bank(usgaap)
    out, prov = {}, []
    for fye, (accn, fdate) in sorted(orig.items()):
        y = keyed_year(fye)
        if y < MIN_YEAR or y > MAX_YEAR: continue
        row = {"cik": c, "fiscal_year": y, "fye_date": fye, "filed": fdate,
               "reporting_basis": "us-gaap", "currency": "USD",
               "sector": "bank" if bank else "general"}
        for metric, (kind, tags) in TAGS.items():
            v, tag, basis = asfiled(usgaap, tags, kind, fye, accn)
            row[metric] = v
            if v is not None: prov.append([c, y, metric, tag, basis, accn, v])
        rev, rtag = select_revenue(usgaap, fye, accn, bank)
        row["revenue"] = rev
        if rev is not None: prov.append([c, y, "revenue", rtag, "", accn, rev])
        rev_total,_,_ = asfiled(usgaap, ["Revenues"], DURATION, fye, accn)
        if rev and not bank and rev_total is not None and rev_total > rev * 1.02:
            prov.append([c, y, "revenue_FACE_FLAG", "Revenues", "", accn,
                         f"Revenues {rev_total:,.0f} > chosen {rev:,.0f} (pre.txt tiebreak)"])
        eq, etag = select_equity(usgaap, fye, accn)
        row["stockholders_equity"] = eq
        if eq is not None: prov.append([c, y, "stockholders_equity", etag, "", accn, eq])
        cash, ctag = select_cash(usgaap, fye, accn)
        row["cash"] = cash
        row["total_debt"] = extract_total_debt(usgaap, fye, accn)
        if row.get("capex") is not None: row["capex"] = abs(row["capex"])
        cfo = row.get("operating_cash_flow")
        row["free_cash_flow"] = (cfo - (row.get("capex") or 0)) if cfo is not None else None
        if row.get("total_assets") == 0:           # shell-year signature (AMRX 2017): blank it
            row["total_assets"] = None
        out[y] = row
    return name, out, prov


def main():
    if not REG_SET.exists():
        print(f"!! {REG_SET} not found."); return
    reg = list(csv.DictReader(open(REG_SET, encoding="utf-8-sig")))
    print(f"As-filed extraction (v2) on {len(reg)} companies...")
    rows, prov_rows = [], []
    for r in reg:
        try:
            name, out, prov = extract_company(r["cik"])
        except Exception as e:
            print(f"  {r['ticker']}: ERROR {type(e).__name__}: {str(e)[:70]}"); continue
        for y in sorted(out):
            d = out[y]; d["ticker"] = r["ticker"]; d["name"] = name
            rows.append([d.get(f) for f in OUTPUT_FIELDS])
        prov_rows += prov
        print(f"  {r['ticker']:<6} {name[:30]:<30} {len(out)} fiscal years")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(OUTPUT_FIELDS); w.writerows(rows)
    with open(PROV_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","fiscal_year","metric","chosen_tag","basis","accession","value"]); w.writerows(prov_rows)
    print(f"  -> {OUT_CSV.name} ({len(rows)} rows), {PROV_CSV.name}")
    if not LEGACY_CSV.exists():
        print(f"  (legacy {LEGACY_CSV.name} not present -- skipping diff)"); return
    legacy = {(str(int(r["cik"])), int(r["fiscal_year"])): r
              for r in csv.DictReader(open(LEGACY_CSV, encoding="utf-8-sig"))}
    METRICS = [m for m in OUTPUT_FIELDS if m not in
               ("cik","ticker","name","fiscal_year","fye_date","reporting_basis","currency","sector")]
    diffs = []
    for row in rows:
        d = dict(zip(OUTPUT_FIELDS, row)); key = (str(int(d["cik"])), int(d["fiscal_year"]))
        lg = legacy.get(key)
        if not lg:
            diffs.append([d["ticker"], d["fiscal_year"], "(no legacy row)", "", "", ""]); continue
        for m in METRICS:
            try: a = float(d.get(m)) if d.get(m) not in (None,"") else None
            except: a = None
            try: b = float(lg.get(m)) if lg.get(m) not in (None,"") else None
            except: b = None
            if a is None and b is None: continue
            if a is None or b is None:
                diffs.append([d["ticker"], d["fiscal_year"], m, a, b, "PRESENCE"]); continue
            if abs(a-b)/max(abs(a),abs(b),1.0) > 0.005:
                diffs.append([d["ticker"], d["fiscal_year"], m, a, b, f"{(a-b)/max(abs(a),abs(b),1.0)*100:+.1f}%"])
    with open(DIFF_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ticker","fiscal_year","metric","asfiled","legacy","diff"]); w.writerows(diffs)
    print(f"  -> {DIFF_CSV.name}: {len(diffs)} cells differ from legacy")


if __name__ == "__main__":
    main()
