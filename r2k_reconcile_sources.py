"""
============================================================
r2k_reconcile_sources.py  --  forensic three-way reconciliation of our SEC as-filed pull
against Morningstar (and, where present, FactSet snapshots).
============================================================
For every company-year and every field in our schema this answers the IC's question:
"when the numbers disagree, who is right and why?"  It does NOT silently overwrite anything.

For each (cik, fiscal_year, field) it lines up:
  * our as-filed value + the exact XBRL tag / accession we chose (from asfiled_provenance.csv)
  * Morningstar's standardized value + which line item it came from
  * ALTERNATE Morningstar line items and COMPONENT SUMS (e.g. Gross = Revenue - Cost Of
    Revenue; Operating = Gross + Operating Income/Expenses; Tax = Pretax - Net Income), so you
    can see whether a different tag or a sum of tags reconciles the gap.

Then it classifies each pair (match / blank-sec / blank-mstar / sign-flip / 1000x-scale /
magnitude) and writes a diagnosis: the best-matching Morningstar source for our value, or a
note that nothing Morningstar reconciles -- which points the finger at our extraction.

Period matching is by REPORT PERIOD-END DATE (within tolerance) so 52/53-week fiscal years
and vendor FY-label drift don't cause false mismatches; it falls back to the FY label.

INPUTS  (R2KG_BASE)
    edgar_annual_fundamentals_ASFILED.csv     our as-filed pull (run the engine first)
    asfiled_provenance.csv                     per-field chosen tag/accession (optional but ideal)
    morningstar_long.csv                       from r2k_morningstar_parse.py
OUTPUT
    reconciliation_detail.csv     every field/year with values, sources, class, diagnosis
    reconciliation_conflicts.csv  only material disagreements, ranked by index relevance
    reconciliation_fills.csv       SEC blank + confident Morningstar value -> step2 override feed
    prints a per-field scorecard

RUN: python r2k_reconcile_sources.py
SELFTEST (no SEC data needed): python r2k_reconcile_sources.py --selftest
============================================================
"""
from pathlib import Path
from datetime import date, datetime
import os, re, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
SEC_CSV = BASE / "edgar_annual_fundamentals_ASFILED.csv"
PROV_CSV = BASE / "asfiled_provenance.csv"
MSTAR_CSV = BASE / "morningstar_long.csv"
DETAIL = BASE / "reconciliation_detail.csv"
CONFLICTS = BASE / "reconciliation_conflicts.csv"
FILLS = BASE / "reconciliation_fills.csv"

PERIOD_TOL_DAYS = int(os.environ.get("RECON_PERIOD_TOL", "20"))
REL_OK = float(os.environ.get("RECON_REL_OK", "0.01"))     # within 1% = match
ABS_OK = float(os.environ.get("RECON_ABS_OK", "1000"))     # within $1k = match (rounding)


def f(x):
    try:
        v = float(x)
        return v
    except (TypeError, ValueError):
        return None


def g(metrics, name):
    """fetch a Morningstar line item (already float) or None"""
    return metrics.get(name)


def _sum(*vals):
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


# ----------------------------------------------------------------------------
# field map: our schema field -> Morningstar candidates, sign normalization, and the
# component reconciliations to try when the primary line item disagrees.
# norm: "as_is" compares signed values; "abs" compares magnitudes (sign conventions differ).
# candidates: tried in order; first non-null is the Morningstar "primary" for the pair.
# components: list of (label, fn(metrics)->value) alternative derivations from Morningstar tags.
# ----------------------------------------------------------------------------
def C(*names):  # first-non-null candidate helper builder
    def fn(m):
        for n in names:
            if g(m, n) is not None:
                return g(m, n)
        return None
    return fn

FIELD_MAP = {
    "revenue": dict(stmt="IS", norm="as_is",
                    candidates=["Total Revenue", "Business Revenue", "Unadjusted Revenue"],
                    components=[]),
    "gross_profit": dict(stmt="IS", norm="as_is",
                    candidates=["Gross Profit"],
                    components=[("Revenue + CostOfRevenue(neg)",
                                lambda m: _sum(g(m, "Total Revenue"), g(m, "Cost Of Revenue")))]),
    "operating_income": dict(stmt="IS", norm="as_is",
                    candidates=["Total Operating Profit Loss"],
                    components=[("Gross + OperatingIncomeExpenses",
                                lambda m: _sum(g(m, "Gross Profit"), g(m, "Operating Income Expenses"))),
                               ("OperatingIncomeExpenses line",
                                lambda m: g(m, "Operating Income Expenses"))]),
    "pretax_income": dict(stmt="IS", norm="as_is",
                    candidates=["Pretax Income"],
                    components=[("Operating + NonOperating",
                                lambda m: _sum(g(m, "Total Operating Profit Loss"),
                                               g(m, "Non Operating Income Expenses Total")))]),
    "tax_expense": dict(stmt="IS", norm="abs",
                    candidates=["Provision For Income Tax", "Current Tax"],
                    components=[("Pretax - NetIncomeContinuing",
                                lambda m: (None if g(m, "Pretax Income") is None
                                           or g(m, "Net Income From Continuing Operations") is None
                                           else g(m, "Pretax Income") - g(m, "Net Income From Continuing Operations")))]),
    "net_income": dict(stmt="IS", norm="as_is",
                    candidates=["Net Income After Non Controlling Minority Interests",
                                "Net Income Available To Common Stockholders",
                                "Net Income After Extraordinary Items And Discontinued Operations",
                                "Net Income From Continuing Operations"],
                    components=[("Pretax - Tax",
                                lambda m: (None if g(m, "Pretax Income") is None else
                                           _sum(g(m, "Pretax Income"), g(m, "Provision For Income Tax"))))]),
    "total_assets": dict(stmt="BS", norm="as_is", candidates=["Total Assets"], components=[]),
    "stockholders_equity": dict(stmt="BS", norm="as_is",
                    candidates=["Equity Attributable To Parent Stockholders", "Total Equity"],
                    components=[("Total Equity - NonControlling",
                                lambda m: (None if g(m, "Total Equity") is None else
                                           g(m, "Total Equity") - (g(m, "Non Controlling Minority Interests") or 0)))]),
    "cash": dict(stmt="BS", norm="as_is",
                    candidates=["Cash And Cash Equivalents", "Cash&Cash Equivalents And Short Term Investments"],
                    components=[("Cash + CashEquivalents",
                                lambda m: _sum(g(m, "Cash"), g(m, "Cash Equivalents")))]),
    "short_term_investments": dict(stmt="BS", norm="as_is",
                    candidates=["Short Term Investments"], components=[]),
    "long_term_investments": dict(stmt="BS", norm="as_is",
                    candidates=["Investment In Financial Assets"], components=[]),
    "restricted_cash": dict(stmt="BS", norm="as_is",
                    candidates=["Cash Restricted Or Pledged Current"], components=[]),
    "total_debt": dict(stmt="BS", norm="abs",
                    candidates=["Long Term Debt And Capital Lease Obligation"],
                    components=[("CurrentDebt&Lease + LTDebt&Lease",
                                lambda m: _sum(g(m, "Current Debt And Capital Lease Obligation"),
                                               g(m, "Long Term Debt And Capital Lease Obligation"))),
                               ("CurrentDebt + LongTermDebt",
                                lambda m: _sum(g(m, "Current Debt"), g(m, "Long Term Debt")))]),
    "operating_cash_flow": dict(stmt="CF", norm="as_is",
                    candidates=["Cash Flow From Operating Activities Indirect",
                                "Net Cash Flow From Continuing Operating Activities Indirect",
                                "Cash Flows From Used In Operating Activities Direct"],
                    components=[]),
    "capex": dict(stmt="CF", norm="abs",
                    candidates=["Capital Expenditure Reported", "Purchase Of Property Plant And Equipment"],
                    components=[]),
    "free_cash_flow": dict(stmt="CF", norm="as_is",
                    candidates=[],   # our FCF is defined as CFO - capex; compare to that, not MS's FCF line
                    components=[("CFO - |Capex|",
                                lambda m: (None if g(m, "Cash Flow From Operating Activities Indirect") is None
                                           else g(m, "Cash Flow From Operating Activities Indirect")
                                                - abs(g(m, "Purchase Of Property Plant And Equipment") or
                                                      g(m, "Capital Expenditure Reported") or 0)))]),
}


# every Morningstar line item referenced by a candidate or a component above -- so the loader
# can stream the (large) long file and keep only what reconciliation needs, sparing memory.
NEEDED_METRICS = {
    "Total Revenue", "Business Revenue", "Unadjusted Revenue", "Gross Profit", "Cost Of Revenue",
    "Total Operating Profit Loss", "Operating Income Expenses", "Pretax Income",
    "Non Operating Income Expenses Total", "Provision For Income Tax", "Current Tax",
    "Net Income From Continuing Operations", "Net Income After Non Controlling Minority Interests",
    "Net Income Available To Common Stockholders",
    "Net Income After Extraordinary Items And Discontinued Operations",
    "Total Assets", "Equity Attributable To Parent Stockholders", "Total Equity",
    "Non Controlling Minority Interests", "Cash And Cash Equivalents",
    "Cash&Cash Equivalents And Short Term Investments", "Cash", "Cash Equivalents",
    "Short Term Investments", "Investment In Financial Assets", "Cash Restricted Or Pledged Current",
    "Current Debt And Capital Lease Obligation", "Long Term Debt And Capital Lease Obligation",
    "Current Debt", "Long Term Debt", "Cash Flow From Operating Activities Indirect",
    "Net Cash Flow From Continuing Operating Activities Indirect",
    "Cash Flows From Used In Operating Activities Direct", "Capital Expenditure Reported",
    "Purchase Of Property Plant And Equipment",
}


def normval(v, norm):
    if v is None: return None
    return abs(v) if norm == "abs" else v


def classify(sec, ms):
    if sec is None and ms is None: return "both_blank"
    if sec is None: return "blank_sec"
    if ms is None: return "blank_mstar"
    a, b = sec, ms
    if abs(a - b) <= ABS_OK: return "match"
    denom = max(abs(a), abs(b), 1.0)
    if abs(a - b) / denom <= REL_OK: return "match"
    if abs(a + b) / denom <= 0.02: return "sign_flip"
    if b not in (0,) and 990 <= abs(a / b) <= 1010: return "scale_1000x"
    if a not in (0,) and 990 <= abs(b / a) <= 1010: return "scale_1000x"
    return "magnitude"


def reldiff(a, b):
    if a is None or b is None: return None
    return abs(a - b) / max(abs(a), abs(b), 1.0)


def best_reconciler(sec_raw, norm, prim_fn, components, metrics):
    """Among the primary candidate and every component derivation, find the one whose
    (sign-normalized) value best matches our SEC value. Returns (label, value, reldiff)."""
    if sec_raw is None: return (None, None, None)
    sec = normval(sec_raw, norm)
    trials = [("Morningstar primary", prim_fn(metrics))]
    trials += [(lbl, fn(metrics)) for lbl, fn in components]
    best = (None, None, None)
    for lbl, val in trials:
        nv = normval(val, norm)
        rd = reldiff(sec, nv)
        if rd is not None and (best[2] is None or rd < best[2]):
            best = (lbl, val, rd)
    return best


# ----------------------------------------------------------------------------
def load_mstar():
    """cik -> list of {period_end(date), fy(int), report_date, metrics{name:val}}"""
    by = defaultdict(lambda: defaultdict(lambda: {"period_end": None, "report_date": "", "metrics": {}}))
    if not MSTAR_CSV.exists():
        raise SystemExit(f"!! {MSTAR_CSV.name} not found (run r2k_morningstar_parse.py first).")
    for r in csv.DictReader(open(MSTAR_CSV, encoding="utf-8")):
        if r["metric"] not in NEEDED_METRICS:      # stream-filter: keep only what we reconcile
            continue
        cik = str(int(r["cik"])) if r["cik"].isdigit() else r["cik"]
        fy = int(r["fiscal_year"])
        slot = by[cik][fy]
        slot["metrics"][r["metric"]] = f(r["value"])
        if r["period_end"] and not slot["period_end"]:
            try: slot["period_end"] = datetime.strptime(r["period_end"], "%Y-%m-%d").date()
            except ValueError: pass
        if r["report_date"] and not slot["report_date"]:
            slot["report_date"] = r["report_date"]
        slot["ticker"] = r.get("ticker", ""); slot["name"] = r.get("name", "")
    return by


def match_period(mrec, sec_fye, sec_fy):
    """choose the Morningstar fiscal-year record for a SEC row, by period-end proximity."""
    if sec_fye:
        best, bestd = None, 10**9
        for fy, slot in mrec.items():
            pe = slot["period_end"]
            if pe is None: continue
            d = abs((pe - sec_fye).days)
            if d < bestd: best, bestd = (fy, slot), d
        if best and bestd <= PERIOD_TOL_DAYS:
            return best[1]
    return mrec.get(sec_fy)


def load_prov():
    prov = {}
    if PROV_CSV.exists():
        for r in csv.DictReader(open(PROV_CSV, encoding="utf-8")):
            cik = str(int(r["cik"])) if r["cik"].isdigit() else r["cik"]
            prov[(cik, int(r["fiscal_year"]), r["metric"])] = (r.get("chosen_tag", ""),
                                                               r.get("basis", ""), r.get("accession", ""))
    return prov


def run(sec_rows, mstar, prov):
    detail = []
    for r in sec_rows:
        cik = str(int(r["cik"])) if str(r["cik"]).isdigit() else str(r["cik"])
        fy = int(r["fiscal_year"])
        tk, nm = r.get("ticker", ""), r.get("name", "")
        sec_fye = None
        if r.get("fye_date"):
            try: sec_fye = datetime.strptime(r["fye_date"][:10], "%Y-%m-%d").date()
            except ValueError: pass
        mrec = match_period(mstar.get(cik, {}), sec_fye, fy) if cik in mstar else None
        metrics = mrec["metrics"] if mrec else {}
        ms_period = mrec["period_end"].isoformat() if (mrec and mrec["period_end"]) else ""
        for field, spec in FIELD_MAP.items():
            sec_raw = f(r.get(field))
            prim_fn = C(*spec["candidates"]) if spec["candidates"] else (lambda m: None)
            ms_raw = prim_fn(metrics)
            # which named candidate produced the primary?
            ms_metric = ""
            for c in spec["candidates"]:
                if g(metrics, c) is not None:
                    ms_metric = c; break
            sec_n, ms_n = normval(sec_raw, spec["norm"]), normval(ms_raw, spec["norm"])
            cls = classify(sec_n, ms_n)
            blbl, bval, brd = best_reconciler(sec_raw, spec["norm"], prim_fn, spec["components"], metrics)
            # diagnosis
            if cls in ("match", "both_blank"):
                diag = ""
            elif cls == "blank_sec":
                diag = f"SEC blank; Morningstar has {ms_metric or '(component)'}={ms_raw}"
            elif cls == "blank_mstar":
                diag = "Morningstar has no value for this field/period"
            else:
                if brd is not None and brd <= REL_OK:
                    diag = f"RESOLVED: SEC matches Morningstar '{blbl}' (diff {brd*100:.2f}%)"
                else:
                    diag = (f"UNRECONCILED: closest Morningstar source '{blbl}' still differs "
                            f"{('%.1f%%'%(brd*100)) if brd is not None else 'n/a'} -> check our tag")
            tag, basis, accn = prov.get((cik, fy, field), ("", "", ""))
            detail.append(dict(cik=cik, ticker=tk, name=nm, fiscal_year=fy, sec_fye=r.get("fye_date", ""),
                               mstar_period=ms_period, field=field, sec_value=sec_raw, sec_tag=tag,
                               sec_accession=accn, mstar_value=ms_raw, mstar_metric=ms_metric,
                               norm=spec["norm"], rel_diff=reldiff(sec_n, ms_n), classification=cls,
                               best_source=blbl, best_value=bval, best_reldiff=brd, diagnosis=diag))
    return detail


def write_outputs(detail):
    cols = ["cik", "ticker", "name", "fiscal_year", "sec_fye", "mstar_period", "field",
            "sec_value", "sec_tag", "sec_accession", "mstar_value", "mstar_metric", "norm",
            "rel_diff", "classification", "best_source", "best_value", "best_reldiff", "diagnosis"]
    with open(DETAIL, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(detail)
    conflicts = [d for d in detail if d["classification"] in ("sign_flip", "scale_1000x", "magnitude")]
    conflicts.sort(key=lambda d: (d["best_reldiff"] is not None, d["best_reldiff"] or 0), reverse=True)
    with open(CONFLICTS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(conflicts)
    fills = [d for d in detail if d["classification"] == "blank_sec" and d["mstar_value"] is not None]
    with open(FILLS, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh); w.writerow(["cik", "fiscal_year", "metric", "value", "source_line_item"])
        for d in fills:
            w.writerow([d["cik"], d["fiscal_year"], d["field"], d["mstar_value"], d["mstar_metric"]])
    return conflicts, fills


def scorecard(detail):
    by_field = defaultdict(lambda: defaultdict(int))
    for d in detail:
        by_field[d["field"]][d["classification"]] += 1
    order = ["match", "blank_sec", "blank_mstar", "sign_flip", "scale_1000x", "magnitude", "both_blank"]
    print(f"\n  {'field':<22}" + "".join(f"{k[:9]:>11}" for k in order))
    for field in FIELD_MAP:
        c = by_field[field]
        print(f"  {field:<22}" + "".join(f"{c.get(k,0):>11}" for k in order))
    nconf = sum(by_field[f].get(k, 0) for f in FIELD_MAP for k in ("sign_flip", "scale_1000x", "magnitude"))
    resolved = sum(1 for d in detail if d["classification"] in ("sign_flip", "scale_1000x", "magnitude")
                   and d["best_reldiff"] is not None and d["best_reldiff"] <= REL_OK)
    print(f"\n  conflicts: {nconf}  (of which {resolved} reconcile to a Morningstar tag/sum -> likely OUR tag choice;")
    print(f"            {nconf-resolved} reconcile to nothing -> needs manual look or is a genuine as-filed/restated gap)")


def selftest():
    """Fabricate SEC rows from Morningstar with deliberately injected errors and confirm the
    classifier + reconciler catch them. Needs only morningstar_long.csv."""
    mstar = load_mstar()
    cik = "1408710"  # Fabrinet
    slot = mstar[cik][2011]; m = slot["metrics"]
    pe = slot["period_end"].isoformat()
    rev = m["Total Revenue"]; gp = m["Gross Profit"]; oi = m["Total Operating Profit Loss"]
    ni = m["Net Income After Non Controlling Minority Interests"]
    rows = [
        # correct, should be 'match'
        dict(cik=cik, ticker="FN", name="Fabrinet", fiscal_year=2011, fye_date=pe,
             revenue=rev, net_income=ni, operating_income=oi, gross_profit=gp),
        # injected: operating_income taken from the WRONG line (Operating Income Expenses)
        dict(cik=cik, ticker="FN", name="Fabrinet-bad-oi", fiscal_year=2011, fye_date=pe,
             operating_income=m["Operating Income Expenses"]),
        # injected: revenue 1000x scale error
        dict(cik=cik, ticker="FN", name="Fabrinet-scale", fiscal_year=2011, fye_date=pe,
             revenue=rev * 1000),
        # injected: net_income sign flip
        dict(cik=cik, ticker="FN", name="Fabrinet-sign", fiscal_year=2011, fye_date=pe,
             net_income=-ni),
        # injected: revenue blank -> fill candidate
        dict(cik=cik, ticker="FN", name="Fabrinet-blank", fiscal_year=2011, fye_date=pe, revenue=""),
    ]
    detail = run(rows, mstar, {})
    print("SELFTEST (Fabrinet FY2011 with injected errors):")
    for d in detail:
        if d["sec_value"] is None and d["classification"] == "blank_mstar":  # skip noise
            continue
        if d["field"] in ("revenue", "operating_income", "net_income") and d["name"] != "Fabrinet":
            print(f"  {d['name']:<18} {d['field']:<18} sec={d['sec_value']!s:<16} "
                  f"class={d['classification']:<12} {d['diagnosis']}")
    # the clean row should be all-match for the 4 provided
    clean = [d for d in detail if d["name"] == "Fabrinet" and d["field"] in
             ("revenue", "operating_income", "net_income", "gross_profit")]
    ok = all(d["classification"] == "match" for d in clean)
    print(f"  clean row all-match: {ok}")


def main():
    if "--selftest" in sys.argv:
        selftest(); return
    if not SEC_CSV.exists():
        raise SystemExit(f"!! {SEC_CSV.name} not found (run the SEC engine first), "
                         f"or use --selftest to validate the logic on Morningstar alone.")
    sec_rows = list(csv.DictReader(open(SEC_CSV, encoding="utf-8-sig")))
    mstar = load_mstar(); prov = load_prov()
    detail = run(sec_rows, mstar, prov)
    conflicts, fills = write_outputs(detail)
    print(f"  SEC rows: {len(sec_rows):,}   Morningstar companies: {len(mstar):,}   "
          f"provenance: {'yes' if prov else 'MISSING (tags blank)'}")
    print(f"  -> {DETAIL.name}  ({len(detail):,} field-checks)")
    print(f"  -> {CONFLICTS.name}  ({len(conflicts):,} material disagreements)")
    print(f"  -> {FILLS.name}  ({len(fills):,} blank-SEC fill candidates)")
    scorecard(detail)


if __name__ == "__main__":
    main()
