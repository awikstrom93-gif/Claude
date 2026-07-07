"""
audit_probe.py -- pin the root cause of two panel-audit findings by dumping the RAW DERA facts (with the
segment dimension the extract drops) for the affected filings. Reuses r2k_dera_extract's own helpers, so
it sees exactly what the extract sees. RUN (from the Benchmark Analysis folder, DERA zips present):
    python audit_probe.py

FINDING #2 -- Griffon FY2018 revenue = $700K (should be ~$1.9B; GP/NI are fine). Prints every
    revenue/sales fact with ddate / qtrs / segment / value. Hypothesis: the real consolidated total is
    segment-dimensioned (SEG-DROP) and a tiny undimensioned line was picked instead of left blank.

FINDING #3 -- equity > assets (impossible) for NuScale / RE/MAX / Zevia / Dakota Gold. Prints the
    balance-sheet facts (assets / equity / NCI / liabilities) with ddate / qtrs / segment / value, so we
    can see WHICH equity tag was adopted (does it fold in a redeemable/NCI component?) and whether a
    total-assets line is missing or segment-only.
"""
import re
from collections import defaultdict

from r2k_dera_extract import open_member, header_ix, load_targets

REVENUE = [("50725", "2018")]                                   # Griffon -- revenue mis-capture
BALANCE = [("1822966", "2024"), ("1581091", "2015"),           # NuScale, RE/MAX
           ("1854139", "2022"), ("1852353", "2022")]           # Zevia, Dakota Gold
REVRE = re.compile(r"(revenu|sales)", re.I)
BSRE = re.compile(r"(assets$|^assets|stockholdersequity|noncontrolling|minorityinterest|"
                  r"liabilities$|totalliabilities|temporaryequity|redeemable|membersequity)", re.I)

by_q, _ = load_targets()
want = {k for k in REVENUE + BALANCE}
meta_by = {}
for q, targets in by_q.items():
    for adsh, m in targets.items():
        key = (str(int(m["cik"])) if str(m["cik"]).isdigit() else m["cik"], m["period"][:4])
        if key in want:
            meta_by[key] = (q, adsh, m["period"])
print(f"  resolved {len(meta_by)}/{len(want)} target filings\n")

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
    seg_i = ix.get("segments")
    facts = defaultdict(list)
    with fh:
        for line in fh:
            cut = line.find("\t")
            if cut < 0 or line[:cut] not in adsh_set:
                continue
            p = line.rstrip("\n").split("\t")
            tag = p[tag_i] if tag_i < len(p) else ""
            if not (REVRE.search(tag) or BSRE.search(tag)):
                continue
            seg = p[seg_i] if seg_i is not None and seg_i < len(p) else ""
            facts[p[0]].append((tag, p[dd_i] if dd_i < len(p) else "",
                                p[q_i] if q_i < len(p) else "", seg,
                                p[val_i] if val_i < len(p) else ""))
    for key, adsh, per in by_quarter[q]:
        which = "REVENUE (Griffon)" if key in REVENUE else "BALANCE SHEET"
        print(f"  === cik {key[0]} FY{key[1]} [{which}] period={per} adsh={adsh} ===")
        rx = REVRE if key in REVENUE else BSRE
        rows = [f for f in facts.get(adsh, []) if rx.search(f[0])]
        # keep the current-period facts (ddate==period), show undimensioned first then segmented
        cur = [f for f in rows if f[1] == per]
        undim = sorted({(t, v) for (t, dd, qt, sg, v) in cur if not sg and qt in ("0", "4")})
        segd = sorted({(t, sg[:34], v) for (t, dd, qt, sg, v) in cur if sg and qt in ("0", "4")})
        print("    KEPT (undimensioned, current period, qtrs 0/4) -- what the extract adopts:")
        for t, v in undim:
            print(f"        {t:52s} = {v}")
        if not undim:
            print("        (none)")
        if segd:
            print("    SEGMENT-dimensioned (dropped by the extract):")
            for t, sg, v in segd[:14]:
                print(f"        {t:44s} [{sg}] = {v}")
        print()
