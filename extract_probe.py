"""
extract_probe.py -- pin WHY the revenue fact is missing from dera_facts for specific filing-years,
while net income from the SAME 10-K is captured. Reads the RAW DERA num.txt for the target filings
(reusing r2k_dera_extract's own helpers, so it sees exactly what the extract sees) and prints every
revenue/sales fact with its ddate, qtrs, segment dimension, and value -- next to the filing's period.

That single view separates the three fact-level drop mechanisms in r2k_dera_extract.process_quarter:
  * SEGMENT   -> the total revenue fact carries a `segments` dimension  => dropped at extract.py:107-108
                 (net income is usually undimensioned, so it survives -- exactly this asymmetry).
  * DDATE!=PER-> the revenue fact's ddate != the filing period (52/53-week or period rounding)
                 => dropped at extract.py:110 (but then NI, same ddate, would drop too -- so unlikely).
  * NOT-TAGGED-> no us-gaap Revenues/Sales/RevenueFromContract fact at all that year (a genuine
                 as-filed gap: revenue only under an industry/custom tag, or not tagged on the face).

RUN (from the Benchmark Analysis folder, DERA zips present):  python extract_probe.py
"""
import re
from collections import defaultdict

from r2k_dera_extract import open_member, header_ix, load_targets

# (cik, fiscal_year) pairs whose revenue is BLANK in the panel though NI is captured -- the extract-gap
# suspects (contiguous blank runs with a recognized tag in neighboring years). Edit freely.
TARGETS = [
    ("107687", "2016"),   # Winnebago (52/53-wk Aug FYE) -- blank 2014-2020, SalesRevenueNet before/after
    ("107687", "2018"),   # Winnebago, another blank year
    ("773141", "2015"),   # MDC (calendar) -- blank 2012-2017, Revenues/RevenueFromContract before/after
    ("1364099", "2014"),  # Innophos (calendar) -- blank 2012-2016, SalesRevenueNet before/after
    ("809248", "2016"),   # Carrols (52/53-wk) -- blank 2013-2019
    ("1320414", "2016"),  # Select Medical (calendar) -- blank 2014+
]
REVRE = re.compile(r"(revenu|sales|netinterestincome|homebuilding|realestaterevenue)", re.I)
NIRE = re.compile(r"(netincomeloss|profitloss)$", re.I)

# map (cik, fy) -> the filing meta (adsh, period, quarter) from the ORIGINAL-filing index
by_q, _ = load_targets()
want = {(c, y) for c, y in TARGETS}
meta_by = {}   # (cik, fy) -> (quarter, adsh, period)
for q, targets in by_q.items():
    for adsh, m in targets.items():
        key = (str(int(m["cik"])) if str(m["cik"]).isdigit() else m["cik"], m["period"][:4])
        if key in want:
            meta_by[key] = (q, adsh, m["period"])

print(f"  resolved {len(meta_by)}/{len(want)} target filings from the original-filing index\n")
# group targets by quarter so each num.txt is streamed once
by_quarter = defaultdict(list)
for key, (q, adsh, per) in meta_by.items():
    by_quarter[q].append((key, adsh, per))

for q in sorted(by_quarter):
    fh = open_member(q, "num.txt")
    if fh is None:
        print(f"  [{q}] num.txt missing"); continue
    adsh_set = {a for _, a, _ in by_quarter[q]}
    ix = header_ix(fh)
    tag_i, dd_i, q_i, val_i = ix["tag"], ix["ddate"], ix["qtrs"], ix["value"]
    seg_i, uom_i = ix.get("segments"), ix.get("uom")
    facts = defaultdict(list)     # adsh -> [(tag, ddate, qtrs, seg, value)]
    with fh:
        for line in fh:
            cut = line.find("\t")
            if cut < 0 or line[:cut] not in adsh_set:
                continue
            p = line.rstrip("\n").split("\t")
            tag = p[tag_i] if tag_i < len(p) else ""
            if not (REVRE.search(tag) or NIRE.search(tag)):
                continue
            seg = (p[seg_i] if seg_i is not None and seg_i < len(p) else "")
            facts[p[0]].append((tag, p[dd_i] if dd_i < len(p) else "",
                                p[q_i] if q_i < len(p) else "", seg,
                                p[val_i] if val_i < len(p) else ""))
    for key, adsh, per in by_quarter[q]:
        print(f"  === cik {key[0]}  FY{key[1]}  (period={per}, adsh={adsh}, {q}) ===")
        rev = [f for f in facts.get(adsh, []) if REVRE.search(f[0])]
        ni = [f for f in facts.get(adsh, []) if NIRE.search(f[0])]
        if not rev:
            print("     REVENUE: (no revenue/sales fact of any kind in num.txt)")
        for tag, dd, qt, seg, val in sorted(rev):
            drop = []
            if seg: drop.append("SEG-DROP")
            if dd != per: drop.append(f"DDATE!=PER({dd})")
            if qt not in ("0", "4"): drop.append(f"QTRS={qt}")
            flag = ("  <-- " + ",".join(drop)) if drop else "  <-- KEPT"
            print(f"     REV  {tag:52s} ddate={dd} qtrs={qt} seg={'Y:'+seg[:30] if seg else '-'} val={val}{flag}")
        for tag, dd, qt, seg, val in sorted(ni)[:3]:
            drop = "SEG" if seg else ("DDATE!=PER" if dd != per else ("QTRS" if qt not in ("0","4") else "KEPT"))
            print(f"     NI   {tag:52s} ddate={dd} qtrs={qt} seg={'Y' if seg else '-'} val={val}  <-- {drop}")
        print()
