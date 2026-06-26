"""
============================================================
r2k_calibrate_tags.py  --  use Morningstar (and optional FactSet) to discover the CORRECT
XBRL tag for each field, then propose engine RULE fixes -- not one-off cell edits.
============================================================
THE IDEA (what you asked for)
  When our as-filed pipeline value disagrees with Morningstar, take Morningstar's NUMBER and
  search the company's own filed XBRL facts (the companyfacts cache we already downloaded) for
  the tag whose as-filed value REPRODUCES Morningstar's figure. That tag is "the one Morningstar
  effectively used" -- i.e. the tag our pipeline should have used.

  * If exactly one RECOGNIZED, statement-level tag reproduces Morningstar's number, and it isn't
    the tag we used, that's an OBVIOUS better tag -> proposed AUTO-FIX.
  * If several tags match, none match, or the match is an odd/extension tag -> QUEUE for your
    judgement (logged with everything we found).
  * The same our-tag -> better-tag swap recurring across many companies becomes a RULE to add to
    the engine's tag priority -- so it fixes all of them at once and benefits any FUTURE fund.

  Nothing is silently changed: this writes PROPOSALS + a plain-English report. You (or a follow-up
  step) decide what to promote into the engine.

WHY THIS GENERALIZES
  Calibrating tag rules on a known universe (R2000G) hardens the SEC pipeline so it is trustworthy
  on any new holdings file WITHOUT needing Morningstar/FactSet every time.

INPUTS  (R2KG_BASE)
  companyfacts_cache/CIK<n>.json      raw XBRL facts (already cached by the engine)
  asfiled_provenance.csv               our chosen tag + accession + value per field
  edgar_annual_fundamentals_ASFILED.csv our pipeline value + fye_date per company-year
  morningstar_long.csv                 Morningstar values (from r2k_morningstar_parse.py)
  [factset constituent export]         OPTIONAL extra confirmation (not required)
OUTPUT
  tag_calibration_report.txt           plain-English summary: rules to add, accuracy by field
  tag_rule_proposals.csv               our_tag -> better_tag swaps, with support counts (auto-fix)
  tag_review_queue.csv                 ambiguous company-years for your eyes
  tag_calibration_detail.csv           every checked company-year/field with all matching tags

RUN:  python r2k_calibrate_tags.py
SELFTEST (synthetic XBRL, no data needed):  python r2k_calibrate_tags.py --selftest
============================================================
"""
from pathlib import Path
from datetime import date, datetime, timedelta
import os, csv, sys, json
from collections import defaultdict, Counter

BASE = Path(os.environ.get("R2KG_BASE", "."))
CF_CACHE = BASE / "companyfacts_cache"
PROV_CSV = BASE / "asfiled_provenance.csv"
SEC_CSV = BASE / "edgar_annual_fundamentals_ASFILED.csv"
MSTAR_CSV = BASE / "morningstar_long.csv"
REPORT = BASE / "tag_calibration_report.txt"
PROPOSALS = BASE / "tag_rule_proposals.csv"
QUEUE = BASE / "tag_review_queue.csv"
DETAIL = BASE / "tag_calibration_detail.csv"

END_TOL = int(os.environ.get("R2KG_END_TOL", "7"))     # period-end match tolerance (days)
DUR_LO, DUR_HI = 300, 400                              # annual duration window (days)
MATCH_TOL = float(os.environ.get("CAL_MATCH_TOL", "0.005"))   # tag value within 0.5% of Morningstar = a match
CONFLICT_TOL = float(os.environ.get("CAL_CONFLICT_TOL", "0.05"))  # pipeline vs Morningstar gap to investigate

# ---- field -> Morningstar line item (primary, then alternates) + sign handling ----
# (mirrors r2k_reconcile_sources; kept local so this script stands alone)
MSTAR_FIELD = {
    "revenue": ("as_is", ["Total Revenue"]),
    "gross_profit": ("as_is", ["Gross Profit"]),
    "operating_income": ("as_is", ["Total Operating Profit Loss"]),
    "pretax_income": ("as_is", ["Pretax Income"]),
    "tax_expense": ("abs", ["Provision For Income Tax", "Current Tax"]),
    "net_income": ("as_is", ["Net Income After Non Controlling Minority Interests",
                             "Net Income Available To Common Stockholders"]),
    "total_assets": ("as_is", ["Total Assets"]),
    "stockholders_equity": ("as_is", ["Equity Attributable To Parent Stockholders", "Total Equity"]),
    "cash": ("as_is", ["Cash And Cash Equivalents"]),
    # total_debt is a SUM, not a single line -- compare our total to Morningstar's
    # (current + long-term, lease-inclusive). Components listed so load_mstar keeps them.
    "total_debt": ("abs", ["Current Debt And Capital Lease Obligation",
                           "Long Term Debt And Capital Lease Obligation"]),
    "operating_cash_flow": ("as_is", ["Cash Flow From Operating Activities Indirect",
                                      "Net Cash Flow From Continuing Operating Activities Indirect"]),
    "capex": ("abs", ["Purchase Of Property Plant And Equipment", "Capital Expenditure Reported"]),
}
DURATION_FIELDS = {"revenue", "gross_profit", "operating_income", "pretax_income", "tax_expense",
                   "net_income", "operating_cash_flow", "capex"}   # else INSTANT (balance sheet)
# the engine intentionally nulls these for financial-sector names (no meaningful gross profit /
# operating income for banks/insurers/REITs), so a Morningstar value there is NOT a pipeline bug.
SECTOR_NULLED = {"gross_profit": {"bank", "insurance", "broker_dealer", "reit", "financial"},
                 "operating_income": {"bank", "insurance"}}

# recognized, statement-level us-gaap concepts per field -> an obvious-better-tag is one of these
RECOGNIZED = {
    "revenue": {"RevenueFromContractsWithCustomersExcludingAssessedTax", "Revenues",
                "RevenueFromContractsWithCustomersIncludingAssessedTax", "SalesRevenueNet",
                "SalesRevenueGoodsNet", "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"},
    "gross_profit": {"GrossProfit"},
    "operating_income": {"OperatingIncomeLoss"},
    "pretax_income": {"IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"},
    "tax_expense": {"IncomeTaxExpenseBenefit", "CurrentIncomeTaxExpenseBenefit"},
    "net_income": {"NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"},
    "total_assets": {"Assets"},
    "stockholders_equity": {"StockholdersEquity",
                            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"},
    "cash": {"CashAndCashEquivalentsAtCarryingValue", "Cash",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"},
    "total_debt": {"LongTermDebt", "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations",
                   "DebtLongtermAndShorttermCombinedAmount", "DebtCurrent", "ShortTermBorrowings",
                   "ConvertibleDebtNoncurrent", "ConvertibleNotesPayable", "SeniorNotes",
                   "NotesPayable", "LongTermDebtAndCapitalLeaseObligationsCurrent"},
    "operating_cash_flow": {"NetCashProvidedByUsedInOperatingActivities",
                            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"},
    "capex": {"PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"},
}

CORE_FIELDS = list(MSTAR_FIELD)


def fnum(x):
    try: return float(x)
    except (TypeError, ValueError): return None


def days(a, b):
    try: return (date.fromisoformat(b[:10]) - date.fromisoformat(a[:10])).days
    except Exception: return None


# ---------------------------------------------------------------------------
# XBRL scanning: for a target annual period (fye), return {concept: as-filed value} across ALL
# us-gaap concepts -- mirroring the engine's period logic so values are on the same basis.
# ---------------------------------------------------------------------------
def concept_values(facts, fye, accn, duration):
    out = {}
    usg = facts.get("facts", {}).get("us-gaap", {})
    for concept, node in usg.items():
        units = node.get("units", {})
        series = units.get("USD")
        if not series:
            continue
        chosen = None
        for r in series:
            end = r.get("end")
            if not end:
                continue
            de = days(fye, end)
            if de is None or abs(de) > END_TOL:
                continue
            if duration:
                d = days(r.get("start", ""), r.get("end", ""))
                if d is None or not (DUR_LO <= d <= DUR_HI):
                    continue
            # prefer the fact from our target accession; else keep first qualifying
            if r.get("accn") == accn and r.get("val") is not None:
                chosen = r.get("val"); break
            if chosen is None and r.get("val") is not None:
                chosen = r.get("val")
        if chosen is not None:
            out[concept] = chosen
    return out


def find_matching_tags(cvals, target, norm):
    """concepts whose (sign-normalized) value reproduces Morningstar's target, ranked by closeness."""
    if target is None:
        return []
    t = abs(target) if norm == "abs" else target
    hits = []
    for concept, v in cvals.items():
        vv = abs(v) if norm == "abs" else v
        denom = max(abs(t), 1.0)
        rd = abs(vv - t) / denom
        if rd <= MATCH_TOL:
            hits.append((concept, v, rd))
    hits.sort(key=lambda h: h[2])
    return hits


# ---------------------------------------------------------------------------
def load_provenance():
    """(cik,fy,field) -> (chosen_tag, accession, value)"""
    prov = {}
    if not PROV_CSV.exists():
        return prov
    for r in csv.DictReader(open(PROV_CSV, encoding="utf-8-sig")):
        cik = str(int(r["cik"])) if str(r["cik"]).isdigit() else str(r["cik"])
        prov[(cik, int(r["fiscal_year"]), r["metric"])] = (
            r.get("chosen_tag", ""), r.get("accession", ""), fnum(r.get("value")))
    return prov


def load_mstar():
    by = defaultdict(dict)
    needed = {m for _, lst in MSTAR_FIELD.values() for m in lst}
    if not MSTAR_CSV.exists():
        raise SystemExit(f"!! {MSTAR_CSV.name} not found (run r2k_morningstar_parse.py first).")
    for r in csv.DictReader(open(MSTAR_CSV, encoding="utf-8")):
        if r["metric"] not in needed:
            continue
        cik = str(int(r["cik"])) if r["cik"].isdigit() else r["cik"]
        fy = int(r["fiscal_year"])
        slot = by[cik].setdefault(fy, {"period_end": None, "metrics": {}})
        slot["metrics"][r["metric"]] = fnum(r["value"])
        if r["period_end"] and not slot["period_end"]:
            try: slot["period_end"] = date.fromisoformat(r["period_end"])
            except ValueError: pass
    return by


def mstar_value(mrec, field):
    norm, names = MSTAR_FIELD[field]
    if field == "total_debt":   # total = current + long-term (both lease-inclusive)
        cur = mrec["metrics"].get("Current Debt And Capital Lease Obligation")
        lt = mrec["metrics"].get("Long Term Debt And Capital Lease Obligation")
        if cur is None and lt is None:
            return None, norm, ""
        return (cur or 0) + (lt or 0), norm, "CurDebt&Lease + LTDebt&Lease (sum)"
    for n in names:
        v = mrec["metrics"].get(n)
        if v is not None:
            return v, norm, n
    return None, norm, ""


def match_period(mcompany, fye):
    if fye:
        best, bd = None, 10**9
        for fy, slot in mcompany.items():
            pe = slot["period_end"]
            if pe and abs((pe - fye).days) < bd:
                best, bd = slot, abs((pe - fye).days)
        if best and bd <= 20:
            return best
    return None


def load_companyfacts(cik):
    p = CF_CACHE / f"CIK{int(cik):010d}.json"
    if not p.exists():
        p = CF_CACHE / f"CIK{cik}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8", errors="replace") or "{}")
    except Exception:
        return None


# ---------------------------------------------------------------------------
def calibrate(sec_rows, prov, mstar, get_facts):
    """get_facts(cik)->companyfacts dict (injectable for selftest). Returns detail list.

    Processes company-by-company (bounds memory, prints progress) and scans each company-year's
    XBRL concepts ONCE per basis (duration vs instant), reusing that scan across all 12 fields --
    instead of re-scanning ~1000 concepts per field."""
    detail = []
    # group SEC rows by CIK so each companyfacts file is loaded (and freed) exactly once
    by_cik = defaultdict(list)
    for r in sec_rows:
        cik = str(int(r["cik"])) if str(r["cik"]).isdigit() else str(r["cik"])
        by_cik[cik].append(r)
    # one representative accession per (cik, fy) from provenance (same 10-K across fields)
    accn_of = {}
    for (cik, fy, _field), (_t, accn, _v) in prov.items():
        if accn:
            accn_of.setdefault((cik, fy), accn)

    total = len(by_cik); done = 0; checked = 0
    progress_every = max(1, total // 50)
    for cik, rows in by_cik.items():
        done += 1
        if done % progress_every == 0 or done == total:
            print(f"  ...calibrating  {done}/{total} companies  ({checked} disagreements checked)",
                  flush=True)
        if cik not in mstar:
            continue
        facts = get_facts(cik)              # load once
        if not facts:
            continue
        for r in rows:
            fy = int(r["fiscal_year"])
            fye = None
            if r.get("fye_date"):
                try: fye = date.fromisoformat(r["fye_date"][:10])
                except ValueError: pass
            mrec = match_period(mstar.get(cik, {}), fye)
            if not mrec:
                continue
            accn = accn_of.get((cik, fy), "")
            fye_iso = fye.isoformat() if fye else ""
            scan = {}   # basis -> {concept: value}, computed at most twice per company-year
            for field in CORE_FIELDS:
                sec_val = fnum(r.get(field))
                mval, norm, mname = mstar_value(mrec, field)
                if mval is None:
                    continue
                # only investigate where pipeline and Morningstar materially disagree
                sn = abs(sec_val) if (norm == "abs" and sec_val is not None) else sec_val
                mn = abs(mval) if norm == "abs" else mval
                if sec_val is not None and abs(sn - mn) / max(abs(mn), 1.0) <= CONFLICT_TOL:
                    continue   # already agree -> nothing to calibrate
                checked += 1
                sector = r.get("sector", "")
                our_tag, our_accn, our_provval = prov.get((cik, fy, field), ("", "", None))
                dur = field in DURATION_FIELDS
                if dur not in scan:
                    scan[dur] = concept_values(facts, fye_iso, accn, dur)
                cvals = scan[dur]
                hits = find_matching_tags(cvals, mval, norm)
                recognized_hits = [h for h in hits if h[0] in RECOGNIZED.get(field, set())]
                blank = sec_val is None
                # verdict
                if sector in SECTOR_NULLED.get(field, set()) and blank:
                    verdict = "intentional_null"    # engine deliberately nulls this field for financials
                elif not hits:
                    verdict = "no_tag_matches"      # Morningstar's number isn't any single filed tag -> definitional/derived
                elif our_tag and any(h[0] == our_tag for h in hits):
                    verdict = "ours_already_matches" # tag fine; value/period nuance
                elif len(recognized_hits) == 1:
                    verdict = "auto_fix"            # exactly one recognized statement tag reproduces Morningstar
                elif len(recognized_hits) > 1:
                    verdict = "review_multi"        # several plausible tags match -> needs judgement
                else:
                    verdict = "review_unrecognized"  # only odd/extension tags match -> needs judgement
                # fix_type distinguishes a true MISS (we extracted nothing) from a TAG SWAP (we had a
                # value but a different tag matches Morningstar -> usually definitional, handle with care)
                fix_type = ("fill_missing" if blank else "tag_swap") if verdict == "auto_fix" else ""
                best = recognized_hits[0] if recognized_hits else (hits[0] if hits else (None, None, None))
                detail.append(dict(
                    cik=cik, ticker=r.get("ticker", ""), name=r.get("name", ""), fiscal_year=fy,
                    field=field, sector=sector, our_tag=our_tag, our_value=sec_val, mstar_value=mval,
                    mstar_line=mname, better_tag=best[0] or "", better_value=best[1],
                    match_reldiff=best[2], fix_type=fix_type, n_recognized_matches=len(recognized_hits),
                    n_total_matches=len(hits),
                    all_matches="; ".join(f"{c}={v:.0f}" for c, v, _ in hits[:6]), verdict=verdict))
    return detail


# ---------------------------------------------------------------------------
def write_outputs(detail):
    cols = ["cik", "ticker", "name", "fiscal_year", "field", "sector", "our_tag", "our_value",
            "mstar_value", "mstar_line", "better_tag", "better_value", "match_reldiff", "fix_type",
            "n_recognized_matches", "n_total_matches", "all_matches", "verdict"]
    with open(DETAIL, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(detail)

    autos = [d for d in detail if d["verdict"] == "auto_fix"]
    # aggregate into RULES: (field, fix_type, our_tag -> better_tag) with support counts. fill_missing
    # (we extracted nothing) is a higher-confidence engine win than tag_swap (often definitional).
    rule = Counter()
    for d in autos:
        rule[(d["field"], d["fix_type"], d["our_tag"], d["better_tag"])] += 1
    with open(PROPOSALS, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "fix_type", "our_tag", "better_tag", "n_companies_supporting", "example_tickers"])
        # fill_missing first, then by support
        for (field, ft, ot, bt), n in sorted(rule.items(), key=lambda x: (x[0][1] != "fill_missing", -x[1])):
            ex = ", ".join(sorted({d["ticker"] for d in autos if d["field"] == field and d["fix_type"] == ft
                                   and d["our_tag"] == ot and d["better_tag"] == bt})[:6])
            w.writerow([field, ft, ot or "(none/derived)", bt, n, ex])

    queue = [d for d in detail if d["verdict"] in ("review_multi", "review_unrecognized", "no_tag_matches")]
    with open(QUEUE, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader(); w.writerows(queue)
    return autos, rule, queue


def write_report(detail, autos, rule, queue):
    by_field_checked = Counter(d["field"] for d in detail)
    by_field_auto = Counter(d["field"] for d in autos)
    lines = []
    lines.append("TAG CALIBRATION REPORT  (pipeline vs Morningstar -> correct XBRL tag)\n")
    lines.append(f"Investigated {len(detail)} company-year/field disagreements "
                 f"(pipeline vs Morningstar > {int(CONFLICT_TOL*100)}%).\n")
    vc = Counter(d["verdict"] for d in detail)
    lines.append("Verdicts:")
    for k in ("auto_fix", "review_multi", "review_unrecognized", "no_tag_matches",
              "intentional_null", "ours_already_matches"):
        lines.append(f"   {k:<22} {vc.get(k,0)}")
    fills = [d for d in autos if d["fix_type"] == "fill_missing"]
    swaps = [d for d in autos if d["fix_type"] == "tag_swap"]
    lines.append(f"\n   of auto_fix: {len(fills)} FILL-MISSING (we extracted nothing -> high-confidence) "
                 f"| {len(swaps)} TAG-SWAP (we had a value; another tag matches -> often definitional)")
    lines.append("")
    lines.append("PROPOSED TAG RULES  [fix_type] use 'better_tag' instead of 'our_tag' -- N companies")
    lines.append("(FILL-MISSING listed first; these are the safe engine wins)")
    if not rule:
        lines.append("   (none -- run on real data; selftest shows the mechanism)")
    for (field, ft, ot, bt), n in sorted(rule.items(), key=lambda x: (x[0][1] != "fill_missing", -x[1])):
        if n < 5:   # keep the report readable; full list in tag_rule_proposals.csv
            continue
        lines.append(f"   [{field}/{ft}]  use '{bt}'  instead of '{ot or '(none)'}'   -- {n} companies")
    lines.append("")
    lines.append(f"REVIEW QUEUE: {len(queue)} company-years need your judgement "
                 f"(multiple tags match, only odd tags match, or Morningstar's figure is derived "
                 f"from no single tag -> see {QUEUE.name}).")
    lines.append("")
    lines.append("Per-field: checked / fill-missing / tag-swap / intentional-null:")
    by_fill = Counter(d["field"] for d in fills)
    by_swap = Counter(d["field"] for d in swaps)
    by_null = Counter(d["field"] for d in detail if d["verdict"] == "intentional_null")
    for field in CORE_FIELDS:
        lines.append(f"   {field:<22} checked {by_field_checked.get(field,0):>5}   "
                     f"fill {by_fill.get(field,0):>4}   swap {by_swap.get(field,0):>4}   "
                     f"null {by_null.get(field,0):>4}")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\n  -> {REPORT.name} / {PROPOSALS.name} / {QUEUE.name} / {DETAIL.name}")


# ---------------------------------------------------------------------------
def selftest():
    """Synthetic company: filed XBRL has the RIGHT operating-income tag the pipeline missed.
    Pipeline picked a wrong tag; Morningstar's value matches the right one -> expect auto_fix."""
    fye = "2020-12-31"; accn = "0000-acc"
    facts = {"facts": {"us-gaap": {
        # the correct operating income (matches Morningstar) -- a clean annual fact
        "OperatingIncomeLoss": {"units": {"USD": [
            {"start": "2020-01-01", "end": "2020-12-31", "val": -10000000, "accn": accn}]}},
        # a distractor the pipeline wrongly used (e.g. a positive 'operating income before items')
        "OperatingIncomeLossBeforeStuff": {"units": {"USD": [
            {"start": "2020-01-01", "end": "2020-12-31", "val": 5000000, "accn": accn}]}},
        # revenue agrees -> should NOT be investigated
        "Revenues": {"units": {"USD": [
            {"start": "2020-01-01", "end": "2020-12-31", "val": 200000000, "accn": accn}]}},
        # total debt: pipeline grabbed tiny DebtCurrent; real debt is in ConvertibleDebtNoncurrent
        "DebtCurrent": {"units": {"USD": [{"end": "2020-12-31", "val": 50000, "accn": accn}]}},
        "ConvertibleDebtNoncurrent": {"units": {"USD": [{"end": "2020-12-31", "val": 300000000, "accn": accn}]}},
    }}}
    sec_rows = [dict(cik="9999", ticker="TST", name="TestCo", fiscal_year=2020, fye_date=fye,
                     sector="general", revenue=200000000, operating_income=5000000, total_debt=50000)]
    prov = {("9999", 2020, "operating_income"): ("OperatingIncomeLossBeforeStuff", accn, 5000000),
            ("9999", 2020, "total_debt"): ("DebtCurrent", accn, 50000),
            ("9999", 2020, "revenue"): ("Revenues", accn, 200000000)}
    mstar = {"9999": {2020: {"period_end": date(2020, 12, 31), "metrics": {
        "Total Revenue": 200000000, "Total Operating Profit Loss": -10000000,
        "Long Term Debt And Capital Lease Obligation": 300000000}}}}
    detail = calibrate(sec_rows, prov, mstar, lambda c: facts)
    print("SELFTEST:")
    ok = True
    for d in detail:
        print(f"  {d['field']:<18} our={d['our_tag']}({d['our_value']})  mstar={d['mstar_value']}  "
              f"-> better={d['better_tag']}  verdict={d['verdict']}")
    want = {("operating_income", "auto_fix", "OperatingIncomeLoss"),
            ("total_debt", "auto_fix", "ConvertibleDebtNoncurrent")}
    got = {(d["field"], d["verdict"], d["better_tag"]) for d in detail}
    revenue_skipped = not any(d["field"] == "revenue" for d in detail)
    ok = want <= got and revenue_skipped
    print(f"\n  operating_income & total_debt auto-fixed to right tag: {want <= got}")
    print(f"  revenue (agreed) correctly NOT investigated: {revenue_skipped}")
    print(f"  ALL PASS: {ok}")


def main():
    if "--selftest" in sys.argv:
        selftest(); return
    for need in (SEC_CSV, MSTAR_CSV):
        if not need.exists():
            raise SystemExit(f"!! {need.name} not found.")
    if not CF_CACHE.exists():
        raise SystemExit(f"!! {CF_CACHE.name}/ not found -- run the SEC engine first so the XBRL "
                         f"facts are cached locally.")
    print("  loading SEC rows, provenance, and Morningstar...", flush=True)
    sec_rows = list(csv.DictReader(open(SEC_CSV, encoding="utf-8-sig")))
    prov = load_provenance(); mstar = load_mstar()
    print(f"  {len(sec_rows):,} SEC company-years | {len(mstar):,} Morningstar companies | "
          f"scanning XBRL for disagreements (>{int(CONFLICT_TOL*100)}%)...", flush=True)
    if not prov:
        print("  (warning: asfiled_provenance.csv missing -> 'our_tag' will be blank; "
              "matches still found, but we can't show what we used.)")
    detail = calibrate(sec_rows, prov, mstar, load_companyfacts)
    autos, rule, queue = write_outputs(detail)
    write_report(detail, autos, rule, queue)


if __name__ == "__main__":
    main()
