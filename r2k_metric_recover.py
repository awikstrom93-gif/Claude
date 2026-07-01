"""
r2k_metric_recover.py  --  the as-filed recovery, generalized to every metric with a real extraction
gap (see r2k_metric_gaps.py), IDENTITY-FIRST, and COMPONENT-COMPLETE. Supersedes r2k_revenue_recover.py.

Holes cluster: gross_profit is blank because COST OF REVENUE is blank; free_cash_flow is blank because
CAPEX is blank. So we recover the COMPONENTS as their own as-filed columns first, then derive the
dependent metric from the stored values -- filling cost_of_revenue / capex too, and keeping the row
internally consistent (revenue - cost_of_revenue = gross_profit; cfo - capex = free_cash_flow).

Per blank metric, in order:
  TIER 1  IDENTITY   -- compute from other STORED as-filed values via an accounting identity
                        (total_equity = assets - liabilities - redeemable_NCI; gross_profit =
                        revenue - cost_of_revenue; free_cash_flow = cfo - capex). No vendor; adopted
                        even when Morningstar lacks the value. Because components are recovered first,
                        this fires once cost_of_revenue / capex are filled.
  TIER 2  AS-FILED TAG -- locate the metric's OWN as-filed tag (non-standard/industry) in dera_facts,
                        verified against the Morningstar target within tolerance; original filing only.
  TIER 3  MORNINGSTAR FALLBACK -- last resort, stamped `morningstar:fallback`.

Metric order matters: revenue, cost_of_revenue, gross_profit, operating_income, total_equity, cash,
capex, free_cash_flow -- so each dependent metric sees its just-recovered components.

INPUTS   fundamentals_dera_resolved.csv (if present) else fundamentals_dera.csv ; dera_facts.csv ;
         morningstar_long.csv
OUTPUTS  fundamentals_dera_resolved.csv (+ <metric>_src cols) ; metric_recovery_audit.csv
RUN      python r2k_metric_recover.py            (then dera_to_fundamentals -> plausibility -> report)
"""
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
DERA = BASE / "fundamentals_dera.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
FACTS = BASE / "dera_facts.csv"
MS = BASE / "morningstar_long.csv"
AUDIT = BASE / "metric_recovery_audit.csv"

TOL = 0.08
_EXCL_REV = ("cost", "gain", "loss", "deferred", "unearned", "receivable", "expense", "pershare",
             "growth", "percent")


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _g(r, f):
    return _num(r.get(f))


def _sub(*xs):
    """minuend - subtrahend(s). The FIRST TWO terms are required (None -> whole identity is None, so a
    reverse identity like COGS=revenue-GP doesn't silently return revenue when GP is blank); any
    further terms are optional (treated as 0 -- e.g. redeemable-NCI in equity=assets-liab-NCI)."""
    if len(xs) < 2 or xs[0] is None or xs[1] is None:
        return None
    tot = xs[0] - xs[1]
    for x in xs[2:]:
        tot -= (x or 0.0)
    return tot


def _pos(x):
    """guard: a value that must be non-negative (cost of revenue, capex) -- else the identity is off."""
    return x if (x is not None and x >= 0) else None


def _ck(c):
    s = str(c).strip()
    return str(int(float(s))) if s.replace(".", "").isdigit() else s


def _match(tag, pats, excls):
    tl = tag.lower()
    return any(p in tl for p in pats) and not any(e in tl for e in excls)


# ---- per-metric spec ------------------------------------------------------------------------------
# field    : fundamentals_dera column to fill
# ms       : Morningstar metric name(s) -> target / fallback
# identity : (row) -> value from STORED components (tier 1), or None
# tag_pat  : substrings identifying the metric's own as-filed tag family (tier 2)
# tag_excl : disqualifying substrings
# solo     : if True, may adopt a single matching curated tag even without a Morningstar target
#            (only for unambiguous single-tag metrics: capex, cash)
METRICS = [
    {"field": "revenue", "ms": ["Total Revenue"], "identity": None,
     "tag_pat": ["revenue", "sales"], "tag_excl": list(_EXCL_REV), "solo": False},

    {"field": "cost_of_revenue", "ms": ["Cost Of Revenue"],
     # if GP is already present, COGS = revenue - gross_profit (keeps the row consistent with the
     # existing as-filed GP instead of pulling a separate COGS tag that might disagree with it)
     "identity": lambda r: _pos(_sub(_g(r, "revenue"), _g(r, "gross_profit"))),
     "tag_pat": ["costofrevenue", "costofgoodsandservices", "costofgoodssold", "costofsales",
                 "costofservices"],
     "tag_excl": ["gross", "depreciation", "amortization", "excludingdepreciation"], "solo": False},

    {"field": "gross_profit", "ms": ["Gross Profit"],
     "identity": lambda r: _sub(_g(r, "revenue"), _g(r, "cost_of_revenue")),
     "tag_pat": ["grossprofit"], "tag_excl": [], "solo": False},

    {"field": "operating_income", "ms": ["Operating Income Expenses"], "identity": None,
     "tag_pat": ["operatingincomeloss", "operatingincome"],
     "tag_excl": ["nonoperating", "beforeincometax"], "solo": False},

    {"field": "total_equity", "ms": ["Total Equity"],
     "identity": lambda r: _sub(_g(r, "total_assets"), _g(r, "total_liabilities"), _g(r, "redeemable_nci")),
     "tag_pat": ["stockholdersequity", "partnerscapital", "membersequity", "totalequity"],
     "tag_excl": ["accumulated", "othercomprehensive", "pershare"], "solo": False},

    {"field": "cash", "ms": ["Cash And Cash Equivalents"], "identity": None,
     "tag_pat": ["cashandcashequivalents", "cashcashequivalents", "cashanddue"],
     "tag_excl": ["restricted", "financing", "investing", "operating", "period", "increase", "decrease"],
     "solo": True},

    {"field": "capex", "ms": ["Capital Expenditure Reported"],
     # if FCF is already present, capex = cfo - free_cash_flow (consistent with the existing FCF)
     "identity": lambda r: _pos(_sub(_g(r, "cfo"), _g(r, "free_cash_flow"))),
     "tag_pat": ["paymentstoacquirepropertyplant", "paymentsforcapitalimprovements",
                 "paymentstoacquireproductiveassets", "paymentstoacquireoilandgasproperty"],
     "tag_excl": ["proceeds"], "solo": True},

    {"field": "free_cash_flow", "ms": ["Free Cash Flow to Firm", "Free Cash Flow to Equity Holders"],
     "identity": lambda r: _sub(_g(r, "cfo"), _g(r, "capex")),
     "tag_pat": [], "tag_excl": [], "solo": False},
]


def load_ms(names_by_field):
    want = {n: fld for fld, names in names_by_field.items() for n in names}
    out = defaultdict(dict)
    if not MS.exists():
        sys.exit(f"!! {MS.name} not found.")
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            fld = want.get(row.get("metric", ""))
            if fld is None:
                continue
            v = _num(row.get("value"))
            k = (_ck(row.get("cik", "")), str(row.get("fiscal_year", "")).strip())
            if v is not None and k[1]:
                out[fld][k] = v
    return out


def load_facts(need):
    """{(cik,fy): {tag: value}} original-filing annual USD facts for the needed (cik,fy)."""
    if not FACTS.exists():
        sys.exit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    need_ciks = {c for c, _ in need}
    kept, acc_year = [], {}
    with open(FACTS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = _ck(r.get("cik", ""))
            if c not in need_ciks or (r.get("uom") or "USD") != "USD":
                continue
            fy = str(r.get("fiscal_year", "")).strip()
            v = _num(r.get("value"))
            if v is None or not fy.isdigit():
                continue
            acc = r.get("version", "")
            kept.append((c, fy, acc, r.get("tag", ""), v))
            if acc not in acc_year or int(fy) > acc_year[acc]:
                acc_year[acc] = int(fy)
    best = {}
    for c, fy, acc, tag, v in kept:
        pr = 0 if acc_year.get(acc) == int(fy) else 1
        key = (c, fy, tag)
        if key not in best or pr < best[key][0]:
            best[key] = (pr, v)
    out = defaultdict(dict)
    for (c, fy, tag), (_pr, v) in best.items():
        out[(c, fy)][tag] = v
    return out


def recover(spec, r, facts_row, target):
    """(value, method, provenance) -- identity from stored values, else own as-filed tag."""
    if spec["identity"] is not None:                       # TIER 1
        val = spec["identity"](r)
        # trust the accounting identity unconditionally: its components are already-stored, as-filed,
        # and tie-out-validated (the classifier gates BS-foot / IS-cascade), so the result is exact and
        # internally consistent. A disagreement with Morningstar is a RESTATEMENT -> keep the as-filed
        # identity, never override it with the vendor number.
        if val is not None:
            return val, "identity", "identity"
    if spec["tag_pat"]:                                    # TIER 2
        cands = [(t, v) for t, v in facts_row.items()
                 if _match(t, spec["tag_pat"], spec["tag_excl"]) and v is not None]
        if target:
            ok = [(t, v) for t, v in cands if abs(v - target) / max(abs(target), 1) <= TOL]
            if ok:
                t, v = min(ok, key=lambda kv: abs(kv[1] - target))
                return v, "asfiled", "asfiled:" + t
        elif spec["solo"] and len(cands) == 1:             # unambiguous single tag, no target needed
            t, v = cands[0]
            return v, "asfiled", "asfiled:" + t + "(no-target)"
    return None, None, None


def main():
    base_file = RESOLVED if RESOLVED.exists() else DERA
    if not base_file.exists():
        sys.exit(f"!! {base_file.name} not found.")
    print(f"  base fundamentals: {base_file.name}")
    with open(base_file, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
        fields = list(rows[0].keys()) if rows else []
    for m in METRICS:
        if m["field"] + "_src" not in fields:
            fields.append(m["field"] + "_src")
    for r in rows:
        for m in METRICS:
            r.setdefault(m["field"] + "_src", "")

    need = set()
    for r in rows:
        if any(_num(r.get(m["field"])) is None for m in METRICS):
            need.add((_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip()))
    print(f"  {len(need):,} company-years have >=1 target metric blank -- loading targets + as-filed facts")
    ms = load_ms({m["field"]: m["ms"] for m in METRICS})
    facts = load_facts(need)

    counts = defaultdict(lambda: defaultdict(int))
    audit = []
    for r in rows:
        key = (_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip())
        frow = facts.get(key, {})
        for m in METRICS:                                  # IN ORDER -> components before dependents
            fld = m["field"]
            if _num(r.get(fld)) is not None:
                continue
            tgt = ms.get(fld, {}).get(key)
            val, method, prov = recover(m, r, frow, tgt)
            if val is None and tgt is not None:
                val, method, prov = tgt, "ms-fallback", "morningstar:fallback"
            if val is None:
                counts[fld]["no-source"] += 1
                continue
            r[fld] = f"{val}"
            r[fld + "_src"] = prov
            counts[fld][method] += 1
            audit.append({"cik": key[0], "fiscal_year": key[1], "metric": fld, "adopted": f"{val:.0f}",
                          "ms_target": f"{tgt:.0f}" if tgt else "", "method": method,
                          "provenance": prov, "sector": r.get("sector", "")})

    with open(RESOLVED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "metric", "adopted", "ms_target",
                                          "method", "provenance", "sector"])
        w.writeheader()
        w.writerows(sorted(audit, key=lambda x: (x["metric"], -abs(_num(x["adopted"]) or 0))))

    print("\n  recovered per metric  (identity = as-filed reconstruction; asfiled = own tag; ms = plugged):")
    print(f"    {'metric':>16} {'identity':>9} {'asfiled':>8} {'ms-fallback':>12} {'no-source':>10}")
    for m in METRICS:
        c = counts[m["field"]]
        print(f"    {m['field']:>16} {c['identity']:>9} {c['asfiled']:>8} {c['ms-fallback']:>12} {c['no-source']:>10}")
    print(f"\n  -> {RESOLVED.name} ; {AUDIT.name} ({len(audit):,} recoveries)")
    print("  NEXT: python r2k_dera_to_fundamentals.py ; python r2k_plausibility.py ; python r2k_report.py")


if __name__ == "__main__":
    main()
