"""
r2k_metric_recover.py  --  the as-filed recovery, generalized to every metric with a real extraction
gap (see r2k_metric_gaps.py), and made IDENTITY-FIRST. Supersedes r2k_revenue_recover.py.

For each blank metric on a covered company-year it tries, in order:
  TIER 1  IDENTITY  -- reconstruct from as-filed COMPONENT tags via an accounting identity, e.g.
                       total_equity = total_assets - total_liabilities - redeemable_NCI
                       gross_profit = revenue - cost_of_revenue (an as-filed COGS tag)
                       free_cash_flow = CFO - capex (an as-filed capex tag)
                       No vendor, fully as-filed, and self-checking. This is preferred.
  TIER 2  AS-FILED TAG -- locate the metric's OWN as-filed tag (a non-standard/industry tag the
                       classifier's list doesn't name), verified against the Morningstar target
                       within tolerance (the r2k_revenue_recover approach). Adopts the original
                       filing's value (point-in-time; dedups multi-filing years to the original).
  TIER 3  MORNINGSTAR FALLBACK -- only if neither works; stamped `morningstar:fallback` in the audit.

Morningstar is used ONLY to test/verify presence and as a last resort -- never plugged when an
as-filed value (own tag or identity reconstruction) reconciles.

INPUTS   fundamentals_dera_resolved.csv (if present) else fundamentals_dera.csv ; dera_facts.csv ;
         morningstar_long.csv
OUTPUTS  fundamentals_dera_resolved.csv (+ <metric>_src cols) ; metric_recovery_audit.csv
RUN      python r2k_metric_recover.py           (then dera_to_fundamentals -> plausibility -> report)
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
_EXCL = ("cost", "gain", "loss", "deferred", "unearned", "receivable", "expense", "pershare",
         "growth", "percent", "pertax")

# ---- per-metric recovery spec ---------------------------------------------------------------------
# field         : fundamentals_dera column to fill
# ms            : Morningstar metric name(s) used as the target / fallback
# tag_pat       : substrings that identify the metric's OWN as-filed tag family (tier 2)
# tag_excl      : substrings that DISQUALIFY a tag (avoid components / gross variants)
# identity      : (component_field_or_tags...) -> value, using as-filed COMPONENT facts (tier 1)
METRICS = [
    {"field": "revenue", "ms": ["Total Revenue"],
     "tag_pat": ["revenue", "sales"], "tag_excl": list(_EXCL), "identity": None},
    {"field": "operating_income", "ms": ["Operating Income Expenses"],
     "tag_pat": ["operatingincomeloss", "operatingincome"], "tag_excl": ["nonoperating", "beforeincometax"],
     "identity": None},
    {"field": "total_equity", "ms": ["Total Equity"],
     "tag_pat": ["stockholdersequity", "partnerscapital", "membersequity", "totalequity"],
     "tag_excl": ["accumulated", "othercomprehensive", "pershare"],
     # identity: assets - liabilities - redeemable NCI
     "identity": lambda d: _sub(d.get("_asset"), d.get("_liab"), d.get("_mezz"))},
    {"field": "cash", "ms": ["Cash And Cash Equivalents"],
     "tag_pat": ["cashandcashequivalents", "cashcashequivalents", "cashanddue"],
     "tag_excl": ["restricted", "financing", "investing", "operating", "period", "increase", "decrease"],
     "identity": None},
    {"field": "gross_profit", "ms": ["Gross Profit"],
     "tag_pat": ["grossprofit"], "tag_excl": [],
     # identity: revenue - cost_of_revenue (both as-filed)
     "identity": lambda d: _sub(d.get("_rev"), d.get("_cogs"))},
    {"field": "free_cash_flow", "ms": ["Free Cash Flow to Firm", "Free Cash Flow to Equity Holders"],
     "tag_pat": [], "tag_excl": [],
     # identity: CFO - capex (both as-filed)
     "identity": lambda d: _sub(d.get("_cfo"), d.get("_capex"))},
]

# as-filed COMPONENT tag families used by the identities (tier 1), matched on lowercased tag
COMPONENTS = {
    "_asset": (["assets"], ["current", "noncurrent", "intangible", "goodwill", "held", "deferred", "net"]),
    "_liab":  (["liabilities"], ["current", "noncurrent", "deferred", "contingencies", "andstockholders"]),
    "_mezz":  (["redeemable", "temporaryequity", "mezzanine"], ["stockholders"]),
    "_rev":   (["revenues", "revenuefromcontract", "salesrevenuenet"], list(_EXCL)),
    "_cogs":  (["costofrevenue", "costofgoodssold", "costofgoodsandservices", "costofsales"], ["gross"]),
    "_cfo":   (["netcashprovidedbyusedinoperatingactivities"], ["financing", "investing", "discontinued"]),
    "_capex": (["paymentstoacquirepropertyplant", "paymentsforcapitalimprovements",
                "paymentstoacquireproductiveassets"], []),
}


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _sub(*xs):
    if xs[0] is None:
        return None
    tot = xs[0]
    for x in xs[1:]:
        tot -= (x or 0.0)
    return tot


def _ck(c):
    s = str(c).strip()
    return str(int(float(s))) if s.replace(".", "").isdigit() else s


def _match(tag, pats, excls):
    tl = tag.lower()
    return any(p in tl for p in pats) and not any(e in tl for e in excls)


def load_ms(names_by_field):
    """{field: {(cik,fy): value}} for the Morningstar target of each metric."""
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
    """{(cik,fy): {tag: value}} of ORIGINAL-filing annual facts for the needed (cik,fy). Keeps every
    tag (so both metric-own tags and identity component tags are available); dedups multi-filing years
    to the accession whose own latest year == fy."""
    if not FACTS.exists():
        sys.exit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    need_ciks = {c for c, _ in need}
    kept = []
    acc_year = {}
    with open(FACTS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = _ck(r.get("cik", ""))
            if c not in need_ciks:
                continue
            if (r.get("uom") or "USD") != "USD":
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


def component(facts_row, pats, excls):
    """Sum the as-filed facts whose tag matches a component family (first match wins per family)."""
    for tag, v in facts_row.items():
        if _match(tag, pats, excls):
            return v
    return None


def recover_metric(spec, facts_row, target):
    """(value, method, provenance) for one metric on one company-year, identity-first."""
    # TIER 1 -- identity reconstruction from as-filed components
    if spec["identity"] is not None:
        comp = {}
        for cvar, (pats, excls) in COMPONENTS.items():
            comp[cvar] = component(facts_row, pats, excls)
        val = spec["identity"](comp)
        if val is not None and (target is None or abs(val - target) / max(abs(target), 1) <= TOL):
            used = [c for c in comp if comp[c] is not None]
            return val, "identity", "identity(" + "+".join(used) + ")"
    # TIER 2 -- the metric's own as-filed tag, verified against the Morningstar target
    if target and spec["tag_pat"]:
        cands = [(t, v) for t, v in facts_row.items()
                 if _match(t, spec["tag_pat"], spec["tag_excl"]) and v is not None
                 and abs(v - target) / max(abs(target), 1) <= TOL]
        if cands:
            t, v = min(cands, key=lambda kv: abs(kv[1] - target))
            return v, "asfiled", "asfiled:" + t
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
        c = m["field"] + "_src"
        if c not in fields:
            fields.append(c)
    for r in rows:
        for m in METRICS:
            r.setdefault(m["field"] + "_src", "")

    need = set()
    for r in rows:
        key = (_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip())
        if any(_num(r.get(m["field"])) is None for m in METRICS):
            need.add(key)
    print(f"  {len(need):,} company-years have >=1 target metric blank -- loading targets + as-filed facts")
    ms = load_ms({m["field"]: m["ms"] for m in METRICS})
    facts = load_facts(need)

    counts = defaultdict(lambda: defaultdict(int))
    audit = []
    for r in rows:
        key = (_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip())
        frow = facts.get(key, {})
        for m in METRICS:
            fld = m["field"]
            if _num(r.get(fld)) is not None:
                continue
            tgt = ms.get(fld, {}).get(key)
            val, method, prov = recover_metric(m, frow, tgt)
            if val is None and tgt is not None:
                val, method, prov = tgt, "ms-fallback", "morningstar:fallback"
            if val is None:
                counts[fld]["no-source"] += 1
                continue
            r[fld] = f"{val}"
            r[fld + "_src"] = prov
            counts[fld][method] += 1
            audit.append({"cik": key[0], "fiscal_year": key[1], "metric": fld,
                          "adopted": f"{val:.0f}", "ms_target": f"{tgt:.0f}" if tgt else "",
                          "method": method, "provenance": prov, "sector": r.get("sector", "")})

    with open(RESOLVED, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "metric", "adopted", "ms_target",
                                          "method", "provenance", "sector"])
        w.writeheader()
        w.writerows(sorted(audit, key=lambda x: (x["metric"], -abs(_num(x["adopted"]) or 0))))

    print("\n  recovered per metric  (identity = as-filed reconstruction; asfiled = own tag; ms-fallback = plugged):")
    print(f"    {'metric':>16} {'identity':>9} {'asfiled':>8} {'ms-fallback':>12} {'no-source':>10}")
    for m in METRICS:
        c = counts[m["field"]]
        print(f"    {m['field']:>16} {c['identity']:>9} {c['asfiled']:>8} {c['ms-fallback']:>12} {c['no-source']:>10}")
    print(f"\n  -> {RESOLVED.name} ; {AUDIT.name} ({len(audit):,} recoveries)")
    print("  NEXT: python r2k_dera_to_fundamentals.py ; python r2k_plausibility.py ; python r2k_report.py")


if __name__ == "__main__":
    main()
