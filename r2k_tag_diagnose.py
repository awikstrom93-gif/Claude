"""
============================================================
r2k_tag_diagnose.py  --  DIAGNOSE, don't plug. For the company-years where our rebuild disagrees
with Morningstar, reverse-engineer Morningstar's number against OUR as-filed DERA facts to find
WHICH tag (or combination) explains it -- so we learn what Morningstar is effectively using and
fix r2k_dera_classify to capture the right value from the filing. The output is a set of proposed
ENGINE fixes (tags to add / swap / exclude), not a substituted value.

For each (cik, fy, field) where our value differs from Morningstar's, it searches our facts:
  EXACT          a single as-filed tag == Morningstar's value      -> we should USE that tag
  MISSING(+tag)  Morningstar - ours == a single tag                -> we are MISSING that component
  OVERCOUNT(-tag) ours - Morningstar == a single tag               -> we are DOUBLE-COUNTING it
  PAIR           two tags sum to Morningstar's value               -> the components MS combined
  NONE           nothing in our facts explains it                  -> definitional (MS standardized)
                                                                       or the data isn't in the filing
Then it AGGREGATES the matches per field: the tags that repeatedly explain Morningstar across many
filings are the concrete additions/changes to make in the classifier's role lists.

INPUTS
  fundamentals_dera.csv   our rebuilt values (+ sector)              [r2k_dera_classify]
  plausibility_flags.csv  which (cik,fy) are review-tier             [r2k_plausibility]
  morningstar_long.csv    Morningstar values (the targets)          [r2k_morningstar_parse]
  dera_facts.csv          our as-filed tags (what we search)        [r2k_dera_extract]
OUTPUTS
  tag_diagnosis.csv       per (cik,fy,field): our value, MS value, match type, the explaining tag(s)
  tag_diagnosis_summary.txt  per field: the tags that most often explain MS -> proposed engine fixes

RUN:  python r2k_tag_diagnose.py
SELFTEST: python r2k_tag_diagnose.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict, Counter

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FLAGS = BASE / "plausibility_flags.csv"
MS = BASE / "morningstar_long.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "tag_diagnosis.csv"
SUMMARY = BASE / "tag_diagnosis_summary.txt"

TOL_REL, TOL_ABS = 0.01, 1_000_000.0

# field (fundamentals_dera) -> (statement to search, Morningstar metric that is the target value)
FIELD_MAP = {
    "revenue": ("IS", "Total Revenue"),
    "gross_profit": ("IS", "Gross Profit"),
    "operating_income": ("IS", "Total Operating Profit Loss"),
    "pretax_income": ("IS", "Pretax Income"),
    "net_income": ("IS", "Net Income After Non Controlling Minority Interests"),
    "total_assets": ("BS", "Total Assets"),
    "total_liabilities": ("BS", "Total Liabilities"),
    "total_equity": ("BS", "Total Equity"),
    "cash": ("BS", "Cash And Cash Equivalents"),
}


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b):
    return abs(a - b) <= max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def diagnose(our_val, ms_val, facts):
    """facts = {tag: value} for the relevant statement of one filing. Find how MS's value is
    explained by our as-filed tags. Returns (match_type, tag_or_tags)."""
    items = [(t, v) for t, v in facts.items() if v is not None]
    # 1. EXACT single tag == MS value (the tag MS effectively used)
    for t, v in items:
        if close(v, ms_val):
            return ("EXACT", t)
    if our_val is not None:
        gap = ms_val - our_val
        if abs(gap) > TOL_ABS:
            # 2. MISSING: MS - ours == a single tag (a component we left out)
            for t, v in items:
                if close(v, gap):
                    return ("MISSING(+)", t)
            # 3. OVERCOUNT: ours - MS == a single tag (something we included that MS doesn't)
            for t, v in items:
                if close(v, -gap):
                    return ("OVERCOUNT(-)", t)
    # 4. PAIR: two of the largest tags sum to MS value
    big = sorted(items, key=lambda kv: -abs(kv[1]))[:15]
    for i in range(len(big)):
        for j in range(i + 1, len(big)):
            if close(big[i][1] + big[j][1], ms_val):
                return ("PAIR", f"{big[i][0]}+{big[j][0]}")
    return ("NONE", "")


def load_targets():
    """review-tier (cik, fy) keys + our field values + sector."""
    review = set()
    if FLAGS.exists():
        for r in csv.DictReader(open(FLAGS, encoding="utf-8")):
            if r["tier"] == "review":
                review.add((r["cik"], r["fiscal_year"]))
    ours = {}
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        k = (r["cik"], r["fiscal_year"])
        if k in review or not FLAGS.exists():
            ours[k] = {f: fnum(r.get(f)) for f in FIELD_MAP}
            ours[k]["_sector"] = r.get("sector", "")
    return review, ours


def load_ms_targets(keys):
    """(cik, fy) -> {field: ms_value} for the FIELD_MAP metrics (latest period_end per metric)."""
    want = {m for _, m in FIELD_MAP.values()}
    metric_to_fields = defaultdict(list)
    for f, (_, m) in FIELD_MAP.items():
        metric_to_fields[m].append(f)
    ciks = {c for c, _ in keys}
    vals, ped = defaultdict(dict), defaultdict(dict)
    if not MS.exists():
        return vals
    with open(MS, newline="", encoding="utf-8", errors="replace") as fh:
        r = csv.reader(fh)
        h = next(r)
        ci, fi, pi, mi, vi = (h.index("cik"), h.index("fiscal_year"), h.index("period_end"),
                              h.index("metric"), h.index("value"))
        for row in r:
            if len(row) <= vi or row[ci] not in ciks or row[mi] not in want:
                continue
            v = fnum(row[vi])
            if v is None:
                continue
            k, pe = (row[ci], row[fi]), row[pi]
            for f in metric_to_fields[row[mi]]:
                if ped[k].get(f) is None or pe >= ped[k][f]:
                    vals[k][f] = v
                    ped[k][f] = pe
    return vals


def load_facts(keys):
    """(cik, fy) -> {'IS': {tag:val}, 'BS': {tag:val}} for target filings."""
    ciks = {c for c, _ in keys}
    out = defaultdict(lambda: {"IS": {}, "BS": {}})
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    with open(FACTS, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        h = next(r)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in r:
            if len(row) <= vi or row[ci] not in ciks or row[si] not in ("IS", "BS") or row[ui] != "USD":
                continue
            v = fnum(row[vi])
            if v is not None:
                out[(row[ci], row[fi])][row[si]][row[ti]] = v
    return out


def run(review, ours, ms, facts):
    rows = []
    for k in (review or ours):
        ov, mv, ff = ours.get(k, {}), ms.get(k, {}), None
        for field, (stmt, _) in FIELD_MAP.items():
            o, m = ov.get(field), mv.get(field)
            if m is None or (o is not None and close(o, m)):
                continue                                   # no MS target, or we already agree
            mt, tag = diagnose(o, m, facts.get(k, {}).get(stmt, {}))
            rows.append(dict(cik=k[0], fiscal_year=k[1], field=field,
                             our_value=("" if o is None else o), ms_value=m,
                             match_type=mt, explaining_tag=tag))
    return rows


def main():
    review, ours = load_targets()
    keys = review or set(ours)
    ms = load_ms_targets(keys)
    facts = load_facts(keys)
    rows = run(review, ours, ms, facts)

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "field", "our_value", "ms_value",
                                          "match_type", "explaining_tag"])
        w.writeheader(); w.writerows(rows)

    # aggregate: per field, the tags that most often EXPLAIN Morningstar -> proposed engine fixes
    L = ["TAG DIAGNOSIS  --  how Morningstar's number maps to OUR as-filed tags (fix the engine, don't plug)",
         f"{len(rows):,} (cik,fy,field) discrepancies diagnosed", ""]
    by_field = defaultdict(list)
    for r in rows:
        by_field[r["field"]].append(r)
    for field, rs in sorted(by_field.items(), key=lambda kv: -len(kv[1])):
        mt = Counter(r["match_type"] for r in rs)
        L.append(f"{field}  ({len(rs)} discrepancies):  " +
                 ", ".join(f"{k} {v}" for k, v in mt.most_common()))
        # the explaining tags we should ADD/SWAP/EXCLUDE (exclude NONE/PAIR aggregation noise)
        tags = Counter((r["match_type"], r["explaining_tag"]) for r in rs
                       if r["match_type"] in ("EXACT", "MISSING(+)", "OVERCOUNT(-)"))
        for (kind, tag), n in tags.most_common(8):
            action = {"EXACT": "USE tag", "MISSING(+)": "ADD component", "OVERCOUNT(-)": "EXCLUDE"}[kind]
            L.append(f"     {action:<16}{tag:<60}{n:>5}")
        L.append("")
    SUMMARY.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:30]))
    print(f"\n  -> {OUT.name} ; {SUMMARY.name}")


def selftest():
    M = 1_000_000   # realistic dollar magnitudes so the $1M absolute tolerance behaves as it will live
    # field=revenue, ours=800 (we used a partial tag), MS=1000. Our facts hold the right total tag
    # (1000) and the missing component (200). Diagnosis should find EXACT on the total tag.
    facts = {"IS": {"SalesRevenueNet": 800*M, "Revenues": 1000*M, "ExciseTaxes": 200*M,
                    "CostOfRevenue": 600*M}}
    mt, tag = diagnose(800*M, 1000*M, facts["IS"])
    c1 = (mt == "EXACT" and tag == "Revenues")
    # remove the exact tag -> should find MISSING(+) ExciseTaxes (1000-800=200)
    f2 = {"SalesRevenueNet": 800*M, "ExciseTaxes": 200*M, "CostOfRevenue": 600*M}
    mt2, tag2 = diagnose(800*M, 1000*M, f2)
    c2 = (mt2 == "MISSING(+)" and tag2 == "ExciseTaxes")
    # OVERCOUNT: ours=1200, MS=1000, a 200 tag we included that MS excludes
    f3 = {"GrossRevenue": 1200*M, "AgencyRevenue": 200*M}
    mt3, tag3 = diagnose(1200*M, 1000*M, f3)
    c3 = (mt3 == "OVERCOUNT(-)" and tag3 == "AgencyRevenue")
    # NONE: nothing explains it (definitional)
    mt4, _ = diagnose(800*M, 1000*M, {"Unrelated": 42*M})
    c4 = (mt4 == "NONE")
    for n, ok in [("EXACT total tag", c1), ("MISSING component", c2), ("OVERCOUNT", c3), ("NONE", c4)]:
        print(f"   {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n  SELFTEST: {'PASS' if all([c1, c2, c3, c4]) else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
