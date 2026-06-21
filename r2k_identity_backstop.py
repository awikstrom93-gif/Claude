"""
============================================================
r2k_identity_backstop.py  --  accounting-identity validation over the full
as-filed dataset. Catches systematic extraction errors (scale, sign, wrong-tag,
mis-footing) structurally, so the dataset can be trusted without per-cell review.
============================================================
Reads edgar_annual_fundamentals_ASFILED.csv and emits identity_check_report.csv
with one row per break (severity ERROR | WARN), sorted ERROR-first.

CHECKS
  ERROR (a break = a definite extraction error):
    BS_FOOTS     total assets ~= Liabilities + total equity (fresh at original accession)
    GP_LE_REV    gross profit <= revenue
    OI_LE_GP     operating income <= gross profit (non-financials)
    CASH_LE_TA   cash <= total assets
    INV_LE_TA    cash + STI + LTI <= total assets
    DEBT_LE_TA   total debt <= 1.05 x total assets
    NONNEG       capex/total_debt/total_assets/revenue sign sanity
  WARN (usually benign; large breaks flag errors):
    NI_RECON     net income ~= pretax - tax   (gap = disc-ops/NCI; flag > 15% & > $5M)
    OPM_BAND     operating margin within [-500%, +100%] (non-financial)
    CO_OUTLIER   a metric > 100x the company's own median across years

RUN
    python r2k_identity_backstop.py            # add --no-foots to skip the fetch-based BS check
============================================================
"""
import csv, sys
from pathlib import Path
from statistics import median
from r2k_step2_asfiled import (original_filing_map, asfiled, _fetch, CF_CACHE, INSTANT,
                               EQUITY_TOTAL, NCI_TAGS, keyed_year, BASE_FOLDER, FINANCIAL)

IN = BASE_FOLDER / "edgar_annual_fundamentals_ASFILED.csv"
OUT = BASE_FOLDER / "identity_check_report.csv"
REL_TOL, ABS_FLOOR = 0.02, 5e6          # balance-sheet foot tolerance
NI_TOL, NI_FLOOR = 0.15, 5e6            # NI reconciliation tolerance
OUTLIER_MULT = 100
DO_FOOTS = "--no-foots" not in sys.argv


def f(x):
    try: return float(x) if x not in (None, "") else None
    except (TypeError, ValueError): return None


def per_row_checks(d):
    """Cheap checks using only the extracted row. Returns [(check, severity, detail)]."""
    out = []
    rev, gp, oi = d["revenue"], d["gross_profit"], d["operating_income"]
    ni, pre, tax = d["net_income"], d["pretax_income"], d["tax_expense"]
    cash, ta = d["cash"], d["total_assets"]
    sti, lti, debt = d["short_term_investments"], d["long_term_investments"], d["total_debt"]
    fin = d["sector"] in FINANCIAL

    if rev is not None and gp is not None and gp > rev * 1.02:
        out.append(("GP_LE_REV", "ERROR", f"GP {gp:,.0f} > rev {rev:,.0f}"))
    if not fin and gp is not None and oi is not None and oi > gp * 1.02:
        out.append(("OI_LE_GP", "ERROR", f"OI {oi:,.0f} > GP {gp:,.0f}"))
    if cash is not None and ta is not None and ta > 0 and cash > ta * 1.02:
        out.append(("CASH_LE_TA", "ERROR", f"cash {cash:,.0f} > TA {ta:,.0f}"))
    if ta is not None and ta > 0:
        liq = (cash or 0) + (sti or 0) + (lti or 0)
        if liq > ta * 1.05:
            out.append(("INV_LE_TA", "ERROR", f"cash+STI+LTI {liq:,.0f} > TA {ta:,.0f}"))
    if debt is not None and ta is not None and ta > 0 and debt > ta * 1.05:
        # debt > assets is REAL for distressed / negative-equity names (WARN); only a gross
        # excess (>3x) signals a scale error (ERROR).
        sev = "ERROR" if debt > ta * 3 else "WARN"
        out.append(("DEBT_LE_TA", sev, f"debt {debt:,.0f} > TA {ta:,.0f} ({debt/ta:.1f}x)"))
    for k in ("capex", "total_debt", "total_assets"):
        v = d[k]
        if v is not None and v < 0:
            out.append(("NONNEG", "ERROR", f"{k} negative: {v:,.0f}"))
    if rev is not None and rev < 0:
        out.append(("NONNEG", "WARN", f"revenue negative: {rev:,.0f}"))

    if ni is not None and pre is not None and tax is not None:
        implied = pre - tax
        denom = max(abs(ni), abs(implied), NI_FLOOR)
        if abs(ni - implied) / denom > NI_TOL and abs(ni - implied) > NI_FLOOR:
            out.append(("NI_RECON", "WARN", f"NI {ni:,.0f} vs pretax-tax {implied:,.0f}"))
    if not fin and rev is not None and rev > 0 and oi is not None:
        m = oi / rev
        if m < -5.0 or m > 1.0:
            out.append(("OPM_BAND", "WARN", f"op margin {m*100:,.0f}% (rev {rev:,.0f}, OI {oi:,.0f})"))
    return out


def foots_check(cik, year, ta):
    """Total assets ~= Liabilities + total equity, pulled fresh at the original accession."""
    if ta is None or ta <= 0: return None
    c = str(int(cik)).zfill(10)
    cf = _fetch(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{c}.json", CF_CACHE / f"CIK{c}.json")
    usg = cf.get("facts", {}).get("us-gaap", {})
    if not usg: return None
    orig, _, _ = original_filing_map(cik, ("10-K",))
    cand = [fy for fy in orig if keyed_year(fy) == int(year)]
    if not cand: return None
    fye, accn = cand[0], orig[cand[0]][0]
    liab,_,_ = asfiled(usg, ["Liabilities"], INSTANT, fye, accn)
    etot,_,_ = asfiled(usg, [EQUITY_TOTAL], INSTANT, fye, accn)
    if etot is None:
        par,_,_ = asfiled(usg, ["StockholdersEquity"], INSTANT, fye, accn)
        nci,_,_ = asfiled(usg, NCI_TAGS, INSTANT, fye, accn)
        if par is not None: etot = par + (nci or 0)
    # PRIMARY: Assets must equal the balance sheet's own grand total
    # (LiabilitiesAndStockholdersEquity), which already INCLUDES temporary/mezzanine equity
    # (redeemable preferred / redeemable NCI). This is temp-equity-proof and catches a
    # mis-scaled or wrong-tag total-assets directly. (Decomposing A = L + permanent-equity
    # falsely fails on the many small-cap-growth filers with redeemable instruments.)
    lase,_,_ = asfiled(usg, ["LiabilitiesAndStockholdersEquity"], INSTANT, fye, accn)
    if lase is not None:
        lhs, rhs = ta, lase
    elif liab is not None and etot is not None:        # fallback when no LASE total tagged
        lhs, rhs = ta, liab + etot
    else:
        return None
    denom = max(abs(lhs), abs(rhs), ABS_FLOOR)
    if abs(lhs - rhs) / denom > REL_TOL and abs(lhs - rhs) > ABS_FLOOR:
        return ("BS_FOOTS", "ERROR", f"TA {lhs:,.0f} vs L+E {rhs:,.0f} (L {liab:,.0f} + E {etot:,.0f})")
    return None


def main():
    if not IN.exists():
        print(f"!! {IN.name} not found. Run r2k_step2_asfiled.py first."); return
    rows = list(csv.DictReader(open(IN, encoding="utf-8-sig")))
    print(f"Checking {len(rows)} company-year rows...")
    # parse
    parsed, by_co = [], {}
    NUM = ["revenue","net_income","operating_income","gross_profit","tax_expense","pretax_income",
           "stockholders_equity","total_assets","cash","short_term_investments","long_term_investments",
           "restricted_cash","total_debt","operating_cash_flow","capex","free_cash_flow"]
    for r in rows:
        d = {k: f(r.get(k)) for k in NUM}
        d.update(cik=r["cik"], ticker=r.get("ticker",""), fiscal_year=r["fiscal_year"], sector=r.get("sector","general"))
        parsed.append(d); by_co.setdefault(r["cik"], []).append(d)

    findings = []
    for d in parsed:
        for chk, sev, detail in per_row_checks(d):
            findings.append([sev, chk, d["ticker"], d["cik"], d["fiscal_year"], detail])
    # within-company scale outlier
    for cik, drows in by_co.items():
        for metric in ("revenue","net_income","operating_income","total_assets","stockholders_equity"):
            vals = [(x["fiscal_year"], abs(x[metric])) for x in drows if x[metric] not in (None, 0)]
            if len(vals) < 4: continue
            med = median([v for _, v in vals])
            if med <= 0: continue
            for yr, av in vals:
                if av > OUTLIER_MULT * med:
                    findings.append(["WARN","CO_OUTLIER", drows[0]["ticker"], cik, yr,
                                     f"{metric} {av:,.0f} > {OUTLIER_MULT}x median {med:,.0f}"])
    # balance-sheet foot check (fetch-based)
    if DO_FOOTS:
        print("  running balance-sheet foot check (uses companyfacts cache)...")
        seen = 0
        for d in parsed:
            res = foots_check(d["cik"], d["fiscal_year"], d["total_assets"])
            if res: findings.append([res[1], res[0], d["ticker"], d["cik"], d["fiscal_year"], res[2]])
            seen += 1
            if seen % 2000 == 0: print(f"    footed {seen}/{len(parsed)}")

    findings.sort(key=lambda x: (x[0] != "ERROR", x[1], x[2]))
    with open(OUT, "w", newline="", encoding="utf-8") as fo:
        w = csv.writer(fo); w.writerow(["severity","check","ticker","cik","fiscal_year","detail"]); w.writerows(findings)

    from collections import Counter
    by_sev = Counter(x[0] for x in findings); by_chk = Counter((x[0], x[1]) for x in findings)
    print(f"\n  {len(findings)} findings -> {OUT.name}")
    print(f"  ERROR: {by_sev.get('ERROR',0)}   WARN: {by_sev.get('WARN',0)}")
    print("  by check:")
    for (sev, chk), n in sorted(by_chk.items(), key=lambda x: (x[0][0] != "ERROR", -x[1])):
        print(f"    [{sev}] {chk:<12} {n}")
    print("\n  Review ERROR rows first (definite extraction errors -> add to manual_value_overrides.csv).")
    print("  WARN rows are mostly benign (disc-ops/NCI, real outliers); scan if curious.")


if __name__ == "__main__":
    main()
