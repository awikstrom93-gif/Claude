"""
============================================================
PILOT: r2k_step2b_asfiled_pilot.py
US Small Cap Growth Review -- as-FILED extraction engine (pilot slice)
============================================================

WHAT THIS PROVES
    Replaces the companyfacts vintage-reconstruction engine with a clean
    AS-ORIGINALLY-FILED spine:
        submissions  -> original 10-K accession per fiscal-year-end
        companyfacts -> the fact whose `accn` == that original accession
        definitions  -> one concept per field (policy v1.0 priority lists)
    No cross-vintage blending, no magnitude/anchor heuristics -- a single
    original accession has no comparatives or restatements to disentangle.

    It emits the SAME schema as the legacy edgar_annual_fundamentals.csv and
    a DIFF report vs the legacy file, so the legacy pipeline acts purely as an
    ORACLE (never a value source). Run it on the regression set first; scale
    only after the diffs are all explained.

HOW TO RUN (on your machine, PowerShell)
    # uses companyfacts_cache/ and submissions_cache/ already built by Steps 2 & 7;
    # fetches anything missing (truststore + your User-Agent).
    python r2k_step2b_asfiled_pilot.py

OUTPUTS (into BASE_FOLDER)
    edgar_annual_fundamentals_ASFILED.csv     same schema, as-originally-filed
    asfiled_provenance.csv                     chosen tag + accession per cell
    asfiled_vs_legacy_diff.csv                 oracle diff (the review queue)
============================================================
"""
from pathlib import Path
from datetime import date
import json, csv, os, time

# ---- TLS / corporate proxy (same pattern as your other scripts) ----
try:
    import truststore; truststore.inject_into_ssl()
except Exception:
    pass
import urllib.request

USER_AGENT = os.environ.get("SEC_UA", "Alex Wikstrom Research awikstrom93@gmail.com")
BASE_FOLDER = Path(os.environ.get("R2KG_BASE", "."))
CF_CACHE   = BASE_FOLDER / "companyfacts_cache"
SUB_CACHE  = BASE_FOLDER / "submissions_cache"
REG_SET    = BASE_FOLDER / "regression_set.csv"
LEGACY_CSV = BASE_FOLDER / "edgar_annual_fundamentals.csv"
OUT_CSV    = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
PROV_CSV   = BASE_FOLDER / "asfiled_provenance.csv"
DIFF_CSV   = BASE_FOLDER / "asfiled_vs_legacy_diff.csv"

MIN_YEAR, MAX_YEAR = 2010, 2026
DUR_LO, DUR_HI = 340, 380          # ~annual (incl. 52/53-week)

DURATION, INSTANT = "duration", "instant"

# ---- Definitions policy v1.0 -> one concept per field (priority order) ----
TAGS = {
    "revenue": (DURATION, ["RevenueFromContractWithCustomerExcludingAssessedTax","Revenues",
        "RevenueFromContractWithCustomerIncludingAssessedTax","SalesRevenueNet",
        "SalesRevenueGoodsNet","SalesRevenueServicesNet","RevenuesNetOfInterestExpense"]),
    "net_income": (DURATION, ["NetIncomeLoss","ProfitLoss"]),                       # parent first
    "operating_income": (DURATION, ["OperatingIncomeLoss"]),
    "gross_profit": (DURATION, ["GrossProfit"]),
    "pretax_income": (DURATION, [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic"]),
    "tax_expense": (DURATION, ["IncomeTaxExpenseBenefit"]),
    # parent first; fall back to incl-NCI ONLY when parent is untagged (then NCI=0 so they are
    # equal). When the fallback fires AND NCI is material it is flagged in provenance for review.
    "stockholders_equity": (INSTANT, ["StockholdersEquity","MembersEquity","PartnersCapital",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
    "total_assets": (INSTANT, ["Assets"]),
    "cash": (INSTANT, ["CashAndCashEquivalentsAtCarryingValue",                     # excl restricted
        "CashAndCashEquivalentsAtCarryingValueIncludingDiscontinuedOperations",
        "CashCashEquivalentsAndFederalFundsSold"]),
    "operating_cash_flow": (DURATION, ["NetCashProvidedByUsedInOperatingActivities",  # total CFO
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "capex": (DURATION, ["PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets","PaymentsForCapitalImprovements",
        "PaymentsToAcquireMachineryAndEquipment"]),
}
DEBT_LTNC = ["LongTermDebtNoncurrent","LongTermDebtAndCapitalLeaseObligations"]
DEBT_LTTOT = ["LongTermDebt"]
DEBT_LTCUR = ["LongTermDebtCurrent","LongTermDebtAndCapitalLeaseObligationsCurrent"]
DEBT_ST   = ["ShortTermBorrowings","DebtCurrent"]
BANK_CORE = ["InterestExpenseDeposits","InterestIncomeExpenseAfterProvisionForLoanLoss",
             "NoninterestIncome","NoninterestExpense"]
BANK_NII = ["InterestIncomeExpenseNet"]; BANK_NONI = ["NoninterestIncome"]

OUTPUT_FIELDS = ["cik","ticker","name","fiscal_year","fye_date","revenue","net_income",
    "operating_income","gross_profit","tax_expense","pretax_income","stockholders_equity",
    "total_assets","cash","total_debt","operating_cash_flow","capex","free_cash_flow",
    "reporting_basis","currency","sector"]


def _fetch(url, cache_path):
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return json.loads(cache_path.read_text(encoding="utf-8", errors="replace") or "{}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    body = urllib.request.urlopen(req, timeout=40).read()
    json.loads(body)                       # validate before caching
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(body); time.sleep(0.13)
    return json.loads(body)


def keyed_year(fye):
    d = date.fromisoformat(fye)
    return d.year - 1 if (d.month == 1 and d.day <= 7) else d.year


def original_10k_map(cik):
    """{FYE 'YYYY-MM-DD' -> (accn, filing_date)} for each period's FIRST-filed 10-K."""
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
        if form != "10-K" or not rdate:
            continue
        if rdate not in orig or fdate < orig[rdate][1]:
            orig[rdate] = (accn, fdate)
    return orig, j.get("name", "")


def _days(start, end):
    try: return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except Exception: return None


def asfiled(usgaap, tags, kind, fye, accn):
    """First priority tag with a fact at (end==fye, accn==original). Returns (val, tag)."""
    for tag in tags:
        node = usgaap.get(tag)
        if not node: continue
        for rec in node.get("units", {}).get("USD", []):
            if rec.get("end") != fye or rec.get("accn") != accn: continue
            if kind == DURATION:
                d = _days(rec.get("start",""), rec.get("end",""))
                if d is None or not (DUR_LO <= d <= DUR_HI): continue
            return rec.get("val"), tag
    return None, None


def is_bank(usgaap):
    return sum(1 for t in BANK_CORE if usgaap.get(t)) >= 1


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
            val, tag = asfiled(usgaap, tags, kind, fye, accn)
            row[metric] = val
            if val is not None: prov.append([c, y, metric, tag, accn, val])
        # bank revenue carve-out (policy): NII + noninterest income
        if bank:
            nii,_ = asfiled(usgaap, BANK_NII, DURATION, fye, accn)
            noni,_ = asfiled(usgaap, BANK_NONI, DURATION, fye, accn)
            if nii is not None or noni is not None:
                row["revenue"] = (nii or 0) + (noni or 0)
        # revenue face-of-statement flag: a larger revenue-total tag in same accession
        chosen = row.get("revenue")
        if chosen and not bank:
            for tag in TAGS["revenue"][1]:
                alt,_ = asfiled(usgaap, [tag], DURATION, fye, accn)
                if alt and alt > chosen * 1.05:
                    prov.append([c, y, "revenue_FACE_FLAG", tag, accn,
                                 f"alt {alt:,.0f} > chosen {chosen:,.0f} -- verify top line"])
                    break
        # total debt = interest-bearing components (same accession)
        ltnc,_ = asfiled(usgaap, DEBT_LTNC, INSTANT, fye, accn)
        lttot,_ = asfiled(usgaap, DEBT_LTTOT, INSTANT, fye, accn)
        ltcur,_ = asfiled(usgaap, DEBT_LTCUR, INSTANT, fye, accn)
        st,_ = asfiled(usgaap, DEBT_ST, INSTANT, fye, accn)
        if any(v is not None for v in (ltnc, lttot, ltcur, st)):
            base = ltnc if ltnc is not None else (lttot if lttot is not None else 0)
            row["total_debt"] = abs((base or 0) + (ltcur or 0 if ltnc is not None else 0) + (st or 0))
        else:
            row["total_debt"] = None
        if row.get("capex") is not None: row["capex"] = abs(row["capex"])
        cfo = row.get("operating_cash_flow")
        row["free_cash_flow"] = (cfo - (row.get("capex") or 0)) if cfo is not None else None
        out[y] = row
    return name, out, prov


def main():
    if not REG_SET.exists():
        print(f"!! {REG_SET} not found."); return
    reg = list(csv.DictReader(open(REG_SET, encoding="utf-8-sig")))
    print(f"Pilot as-filed extraction on {len(reg)} companies...")
    rows, prov_rows = [], []
    for r in reg:
        cik, tk = r["cik"], r["ticker"]
        try:
            name, out, prov = extract_company(cik)
        except Exception as e:
            print(f"  {tk} ({cik}): ERROR {type(e).__name__}: {str(e)[:70]}"); continue
        for y in sorted(out):
            d = out[y]; d["ticker"] = tk; d["name"] = name
            rows.append([d.get(f) for f in OUTPUT_FIELDS])
        prov_rows += prov
        print(f"  {tk:<6} {name[:30]:<30} {len(out)} fiscal years")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(OUTPUT_FIELDS); w.writerows(rows)
    with open(PROV_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["cik","fiscal_year","metric","chosen_tag","accession","value"]); w.writerows(prov_rows)
    print(f"  -> {OUT_CSV.name} ({len(rows)} rows), {PROV_CSV.name}")

    # ---- oracle diff vs legacy ----
    if not LEGACY_CSV.exists():
        print(f"  (legacy {LEGACY_CSV.name} not present -- skipping diff)"); return
    legacy = {}
    for r in csv.DictReader(open(LEGACY_CSV, encoding="utf-8-sig")):
        legacy[(str(int(r["cik"])), int(r["fiscal_year"]))] = r
    METRICS = [m for m in OUTPUT_FIELDS if m in TAGS or m in ("total_debt","free_cash_flow")]
    diffs = []
    for row in rows:
        d = dict(zip(OUTPUT_FIELDS, row))
        key = (str(int(d["cik"])), int(d["fiscal_year"]))
        lg = legacy.get(key)
        if not lg:
            diffs.append([d["ticker"], d["fiscal_year"], "(no legacy row)", "", "", ""]); continue
        for m in METRICS:
            a = d.get(m); b = lg.get(m)
            try: a = float(a) if a not in (None,"") else None
            except: a = None
            try: b = float(b) if b not in (None,"") else None
            except: b = None
            if a is None and b is None: continue
            if a is None or b is None:
                diffs.append([d["ticker"], d["fiscal_year"], m, a, b, "PRESENCE"]); continue
            denom = max(abs(a), abs(b), 1.0)
            if abs(a-b)/denom > 0.005:
                diffs.append([d["ticker"], d["fiscal_year"], m, a, b,
                              f"{(a-b)/denom*100:+.1f}%"])
    with open(DIFF_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["ticker","fiscal_year","metric","asfiled","legacy","diff"]); w.writerows(diffs)
    print(f"  -> {DIFF_CSV.name}: {len(diffs)} cells differ from legacy (the review queue)")


if __name__ == "__main__":
    main()
