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
    ("773141", "2015"),   # MDC -- SEG-DROP homebuilder (total only under BusinessSegments)
    ("833079", "2019"),   # Meritage -- homebuilder, blank 2018+ (was captured 2011-17)
    ("1320414", "2021"),  # Select Medical -- CI fix caught 2014-19 but NOT 2020-23: what changed?
    ("1838406", "2025"),  # BKV -- nat-gas E&P, in the 2026 snapshot (touches the current headline)
    ("1856236", "2023"),  # European Wax -- franchise, blank 2021-24
    ("1856031", "2023"),  # Vivid Seats -- blank 2022-24
    ("1581091", "2015"),  # RE/MAX -- franchise
    ("1076195", "2013"),  # Life Time Fitness
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
    # ---- pre.txt pass: the PRESENTATION join is where a kept num value can still be dropped. Record,
    # per adsh, every revenue/NI presentation line's STMT + (tag,version). extract.py:138 keeps only
    # stmt in {IS,BS,CF}; a revenue line filed under a combined 'CI' (comprehensive income) statement or
    # left uncategorized is dropped here even though its value survived num.txt -- while NI still comes
    # through because it reappears as the top line of the CF statement (stmt=CF). This pass proves it.
    pfh = open_member(q, "pre.txt")
    pre = defaultdict(list)   # adsh -> [(stmt, tag, ver)]
    if pfh is not None:
        with pfh:
            pix = header_ix(pfh)
            pstmt, ptag, pver = pix.get("stmt"), pix["tag"], pix["version"]
            for line in pfh:
                cut = line.find("\t")
                if cut < 0 or line[:cut] not in adsh_set:
                    continue
                p = line.rstrip("\n").split("\t")
                tag = p[ptag] if ptag < len(p) else ""
                if not (REVRE.search(tag) or NIRE.search(tag)):
                    continue
                pre[p[0]].append((p[pstmt] if pstmt is not None and pstmt < len(p) else "",
                                  tag, p[pver] if pver < len(p) else ""))
    for key, adsh, per in by_quarter[q]:
        print(f"  === cik {key[0]}  FY{key[1]}  (period={per}, adsh={adsh}, {q}) ===")
        # NUM: is the undimensioned current-period total present & KEPT for any revenue tag?
        rev = [f for f in facts.get(adsh, []) if REVRE.search(f[0])]
        kept = sorted({(t, val) for (t, dd, qt, seg, val) in rev
                       if not seg and dd == per and qt in ("0", "4")})
        segonly = sorted({t for (t, dd, qt, seg, val) in rev if seg and dd == per and qt in ("0", "4")}
                         - {t for (t, _v) in kept})
        print("    NUM kept undimensioned current-period totals:",
              (", ".join(f"{t}={v}" for t, v in kept) if kept else "(none)"))
        if segonly:
            print(f"    NUM segment-ONLY totals (no undimensioned version -> SEG-DROP): {segonly}")
        # PRE: what statement are the revenue lines presented under? (the join gate)
        prev = [x for x in pre.get(adsh, []) if REVRE.search(x[1])]
        preni = [x for x in pre.get(adsh, []) if NIRE.search(x[1])]
        if not prev:
            print("    PRE revenue lines: (NONE presented) -> no row emitted at all")
        for stmt, tag, ver in sorted(set(prev)):
            gate = "KEPT(IS/BS/CF)" if stmt in ("IS", "BS", "CF") else f"DROPPED(stmt={stmt or 'blank'})"
            joins = "joins-num" if (adsh in facts and any(t == tag and not sg and dd == per and qt in ("0", "4")
                     for (t, dd, qt, sg, vv) in facts[adsh])) else "no-num-join"
            print(f"    PRE  {tag:46s} stmt={stmt or 'blank':5s} ver={ver:14s} -> {gate}; {joins}")
        print(f"    PRE  NetIncomeLoss statements present: {sorted({s for s, t, v in preni}) or '(none)'}")
        print()
