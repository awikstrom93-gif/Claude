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

NO VENDOR PLUGS BY DEFAULT ("diagnose, don't plug"). If neither tier fills the metric, it stays BLANK --
the honest "not reported as-filed" state -- because plugging a vendor's number (often a RESTATED or a
vendor-DERIVED figure, e.g. a computed gross profit for a filer that never reported one) both violates
the as-filed principle and CONTAMINATES the identities: a plugged cost_of_revenue would make the later
gross_profit = revenue - COGS identity fire on a plug and stamp the result `identity`, laundering the
vendor number into what looks like a clean reconstruction. Leaving the component blank prevents that --
the dependent identity simply doesn't fire. A Morningstar last-resort plug is available ONLY when
explicitly enabled (env R2KG_MS_FALLBACK=1); when on it is stamped `morningstar:fallback` AND any
identity that consumes a fallback component is stamped `identity(from-ms)` so it is never laundered.

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

import r2k_validated_tags as vt

BASE = Path(os.environ.get("R2KG_BASE", "."))
DERA = BASE / "fundamentals_dera.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
FACTS = BASE / "dera_facts.csv"
MS = BASE / "morningstar_long.csv"
AUDIT = BASE / "metric_recovery_audit.csv"

TOL = 0.08
# Morningstar last-resort plug is OFF by default (diagnose, don't plug). Set R2KG_MS_FALLBACK=1 only for
# a deliberate, fully-flagged vendor backfill -- otherwise unfilled metrics stay blank (as-filed truth).
MS_FALLBACK = os.environ.get("R2KG_MS_FALLBACK", "0") == "1"
# STATEMENT FILTER: a role's as-filed tag must come from the RIGHT financial statement. Without this a
# balance-sheet "AvailableForSale-SALES-ecurities" or a cash-flow "Proceeds-FROM-SALES" line matched the
# revenue 'sales' substring and, reconciling to the vendor target by chance, was adopted as REVENUE.
# (The classifier and revenue_recover both filter by statement -- this brings metric_recover in line.)
STMT_OF = {"revenue": {"IS"}, "cost_of_revenue": {"IS"}, "gross_profit": {"IS"}, "operating_income": {"IS"},
           "total_equity": {"BS"}, "cash": {"BS"}, "capex": {"CF"}, "free_cash_flow": {"CF"}}
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
# ms       : Morningstar metric name(s) -> target (tier-2 locator) / opt-in fallback
# identity : (row) -> value from STORED components (tier 1), or None
# deps     : the component fields the identity reads -- so a plug consumed by an identity can be stamped
#            (never laundered) when the opt-in fallback is enabled
# tag_pat  : substrings identifying the metric's own as-filed tag family (tier 2)
# tag_excl : disqualifying substrings
# solo     : if True, may adopt a single matching curated tag even without a Morningstar target
#            (only for unambiguous single-tag metrics: capex, cash)
METRICS = [
    {"field": "revenue", "ms": ["Total Revenue"], "identity": None, "deps": [],
     "tag_pat": ["revenue", "sales"], "tag_excl": list(_EXCL_REV), "solo": False},

    {"field": "cost_of_revenue", "ms": ["Cost Of Revenue"],
     # if GP is already present, COGS = revenue - gross_profit (keeps the row consistent with the
     # existing as-filed GP instead of pulling a separate COGS tag that might disagree with it)
     "identity": lambda r: _pos(_sub(_g(r, "revenue"), _g(r, "gross_profit"))),
     "deps": ["revenue", "gross_profit"],
     "tag_pat": ["costofrevenue", "costofgoodsandservices", "costofgoodssold", "costofsales",
                 "costofservices"],
     "tag_excl": ["gross", "depreciation", "amortization", "excludingdepreciation"], "solo": False},

    {"field": "gross_profit", "ms": ["Gross Profit"],
     "identity": lambda r: _sub(_g(r, "revenue"), _g(r, "cost_of_revenue")),
     "deps": ["revenue", "cost_of_revenue"],
     "tag_pat": ["grossprofit"], "tag_excl": [], "solo": False},

    {"field": "operating_income", "ms": ["Total Operating Profit Loss"], "identity": None, "deps": [],
     "tag_pat": ["operatingincomeloss", "operatingincome"],
     "tag_excl": ["nonoperating", "beforeincometax"], "solo": False},

    {"field": "total_equity", "ms": ["Total Equity"],
     "identity": lambda r: _sub(_g(r, "total_assets"), _g(r, "total_liabilities"), _g(r, "redeemable_nci")),
     "deps": ["total_assets", "total_liabilities", "redeemable_nci"],
     "tag_pat": ["stockholdersequity", "partnerscapital", "membersequity", "totalequity"],
     "tag_excl": ["accumulated", "othercomprehensive", "pershare"], "solo": False},

    {"field": "cash", "ms": ["Cash And Cash Equivalents"], "identity": None, "deps": [],
     "tag_pat": ["cashandcashequivalents", "cashcashequivalents", "cashanddue"],
     "tag_excl": ["restricted", "financing", "investing", "operating", "period", "increase", "decrease"],
     "solo": True},

    {"field": "capex", "ms": ["Capital Expenditure Reported"],
     # if FCF is already present, capex = cfo - free_cash_flow (consistent with the existing FCF)
     "identity": lambda r: _pos(_sub(_g(r, "cfo"), _g(r, "free_cash_flow"))),
     "deps": ["cfo", "free_cash_flow"],
     "tag_pat": ["paymentstoacquirepropertyplant", "paymentsforcapitalimprovements",
                 "paymentstoacquireproductiveassets", "paymentstoacquireoilandgasproperty"],
     "tag_excl": ["proceeds"], "solo": True},

    {"field": "free_cash_flow", "ms": ["Free Cash Flow to Firm", "Free Cash Flow to Equity Holders"],
     "identity": lambda r: _sub(_g(r, "cfo"), _g(r, "capex")), "deps": ["cfo", "capex"],
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
            kept.append((c, fy, acc, r.get("tag", ""), v, r.get("stmt", "")))   # keep statement
            if acc not in acc_year or int(fy) > acc_year[acc]:
                acc_year[acc] = int(fy)
    best = {}
    for c, fy, acc, tag, v, st in kept:
        pr = 0 if acc_year.get(acc) == int(fy) else 1
        key = (c, fy, tag)
        if key not in best or pr < best[key][0]:
            best[key] = (pr, v, st)
    out = defaultdict(dict)
    for (c, fy, tag), (_pr, v, st) in best.items():
        out[(c, fy)][tag] = (v, st)                        # (value, statement) per tag
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
        allowed_stmt = STMT_OF.get(spec["field"], set())
        cands = []
        for t, (v, st) in facts_row.items():
            if v is None or not _match(t, spec["tag_pat"], spec["tag_excl"]):
                continue
            if not vt.is_allowed(spec["field"], t):        # cross-statement / component look-alike
                continue
            if allowed_stmt and st and st not in allowed_stmt:   # tag from the wrong statement
                continue
            cands.append((t, v))
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

    # SELF-HEAL: purge stale recoveries a PRIOR run left, so a re-run reflects the CURRENT rules
    # (recovery is otherwise fill-only). In METRIC order so a purged component orphans its dependent
    # identity in the SAME sweep. Three cases:
    #   (a) DISALLOWED as-filed look-alike (e.g. an AvailableForSale/Proceeds tag adopted as revenue by
    #       a coincidental target match) -> purged ALWAYS; it's a wrong tag, not a plug;
    #   (b) Morningstar plug / laundered identity(from-ms) -> purged when the vendor plug is OFF;
    #   (c) a plain `identity` that no longer holds because a required component was purged in (a)/(b).
    # Genuine as-filed tags (right statement, allowed family) and real-component identities survive.
    healed = 0
    for r in rows:
        for m in METRICS:
            fld = m["field"]; src = r.get(fld + "_src", "")
            drop = False
            if src.startswith("asfiled:"):                 # (a)
                tag = src[len("asfiled:"):]
                if tag.endswith("(no-target)"):
                    tag = tag[:-len("(no-target)")]
                drop = not vt.is_allowed(fld, tag)
            if not drop and not MS_FALLBACK and (src.startswith("morningstar")   # (b)
                                                 or src == "identity(from-ms)"):
                drop = True
            if not drop and src == "identity" and m["identity"] is not None:      # (c)
                drop = m["identity"](r) is None
            if drop:
                r[fld] = ""; r[fld + "_src"] = ""; healed += 1
    if healed:
        print(f"  self-heal: purged {healed:,} stale value(s) from a prior run (vendor plugs and/or "
              f"disallowed look-alike tags) -> re-deriving under current rules.")

    need = set()
    for r in rows:
        if any(_num(r.get(m["field"])) is None for m in METRICS):
            need.add((_ck(r.get("cik", "")), str(r.get("fiscal_year", "")).strip()))
    print(f"  {len(need):,} company-years have >=1 target metric blank -- loading targets + as-filed facts")
    ms = load_ms({m["field"]: m["ms"] for m in METRICS})
    facts = load_facts(need)

    counts = defaultdict(lambda: defaultdict(int))
    # distinct company-years per (field, tag) validated as-filed -> fed back to the classifier so the
    # tag is learned and picked at the source next build (see r2k_validated_tags.py). [0]=vs Morningstar
    # target, [1]=solo/no-target.
    validated = defaultdict(lambda: [set(), set()])
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
            # anti-laundering: an identity built on a component that was itself a Morningstar plug is
            # NOT a clean as-filed reconstruction -- stamp it so it can never masquerade as `identity`.
            if method == "identity" and any(
                    str(r.get(d + "_src", "")).startswith("morningstar") for d in m.get("deps", [])):
                prov = "identity(from-ms)"
            if val is None and tgt is not None and MS_FALLBACK:   # opt-in vendor plug (default OFF)
                val, method, prov = tgt, "ms-fallback", "morningstar:fallback"
            if val is None:
                counts[fld]["no-source"] += 1
                continue
            r[fld] = f"{val}"
            r[fld + "_src"] = prov
            counts[fld][method] += 1
            if method == "asfiled" and prov.startswith("asfiled:"):
                no_target = prov.endswith("(no-target)")
                tag = prov[len("asfiled:"):]
                if no_target:
                    tag = tag[:-len("(no-target)")]
                validated[(fld, tag)][1 if no_target else 0].add(key)
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

    print(f"\n  Morningstar vendor plug: {'ON (R2KG_MS_FALLBACK=1)' if MS_FALLBACK else 'OFF (as-filed only -- unfilled metrics stay blank)'}")
    print("  recovered per metric  (identity = as-filed reconstruction; asfiled = own tag; ms = plugged):")
    print(f"    {'metric':>16} {'identity':>9} {'asfiled':>8} {'ms-fallback':>12} {'blank(left)':>12}")
    for m in METRICS:
        c = counts[m["field"]]
        print(f"    {m['field']:>16} {c['identity']:>9} {c['asfiled']:>8} {c['ms-fallback']:>12} {c['no-source']:>12}")
    print(f"\n  -> {RESOLVED.name} ; {AUDIT.name} ({len(audit):,} recoveries)")

    # close the loop: hand the validated as-filed tags back to the classifier so it learns them and
    # picks them at the source next build (r2k_dera_classify.py reads validated_tags.csv). This is what
    # makes this residual SHRINK over time instead of re-recovering the same tags every run.
    runcounts = {kt: (len(s[0]), len(s[1])) for kt, s in validated.items()}
    if runcounts:
        total, new = vt.record_run(runcounts)
        promo = vt.load_promotions()
        n_promo = sum(len(v) for v in promo.values())
        print(f"  -> {vt.PATH.name}: {len(runcounts):,} as-filed tag(s) validated this run "
              f"({new:,} new) ; {n_promo:,} now promotable into the classifier's role lists.")
        print("     RE-RUN r2k_dera_classify.py to fold them in -> those roles fill at the source and "
              "this recovery shrinks.")
    print("  NEXT: python r2k_dera_to_fundamentals.py ; python r2k_plausibility.py ; python r2k_report.py")


if __name__ == "__main__":
    main()
