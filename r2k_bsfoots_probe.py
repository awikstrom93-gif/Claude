"""
r2k_bsfoots_probe.py  --  focused probe for the remaining BS_FOOTS breaks. For each filing where
A != L + E + mezz, it computes the gap and finds the as-filed BALANCE-SHEET tag(s) whose value
equals that gap -- i.e. the exact component we're failing to capture -- then AGGREGATES those tag
names so we know precisely what to add to the engine (no guessing).

Reads: tieout_report.csv (BS_FOOTS breaks), fundamentals_dera.csv (A/L/E/mezz -> gap),
       dera_facts.csv (the as-filed BS tags to search).
Writes: bsfoots_probe.txt  (the tags that most often equal the gap = the components to capture).

RUN:  python r2k_bsfoots_probe.py
"""
from pathlib import Path
import os, csv
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "bsfoots_probe.txt"
TOL_REL, TOL_ABS = 0.01, 2_000_000.0


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def main():
    breaks = {(r["cik"], r["fiscal_year"]) for r in csv.DictReader(open(TIE, encoding="utf-8"))
              if r["identity"].startswith("BS_FOOTS") and r["result"] == "BREAK"}
    fund = {(r["cik"], r["fiscal_year"]): r for r in csv.DictReader(open(FUND, encoding="utf-8"))}
    # gap per break
    gap = {}
    for k in breaks:
        r = fund.get(k)
        if not r:
            continue
        A, L, E = fnum(r.get("total_assets")), fnum(r.get("total_liabilities")), fnum(r.get("total_equity"))
        mz = fnum(r.get("redeemable_nci")) or 0
        if A is not None and L is not None and E is not None:
            gap[k] = A - (L + E + mz)

    # search the as-filed BS facts for a tag whose value == the gap (the missing component)
    ciks = {c for c, _ in gap}
    matched = Counter()       # tag -> how many breaks it explains
    examples = defaultdict(list)
    explained = 0
    with open(FACTS, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh)
        h = next(r)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        facts = defaultdict(dict)
        for row in r:
            if len(row) > vi and row[ci] in ciks and row[si] == "BS" and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v
    for k, g in gap.items():
        hits = [t for t, v in facts.get(k, {}).items() if close(v, g)]
        if hits:
            explained += 1
            for t in hits:
                matched[t] += 1
                if len(examples[t]) < 3:
                    examples[t].append(k)

    L = [f"BS_FOOTS PROBE  --  {len(gap):,} breaks with a computable gap; "
         f"{explained:,} have an as-filed tag equal to the gap (the component we miss)", "",
         "TAGS THAT EQUAL THE GAP (add these to the temporary-equity / mezzanine capture):"]
    for tag, n in matched.most_common(40):
        L.append(f"   {n:>5}  {tag}")
    unexplained = len(gap) - explained
    L.append(f"\n{unexplained:,} breaks have NO single as-filed tag equal to the gap "
             f"(multi-component, or a major line mis-selected -- next diagnosis).")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:50]))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
