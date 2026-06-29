"""
r2k_identity_probe.py  --  general "what tag equals the gap" probe for ANY identity. For each
filing where the given identity BREAKS, take its residual (from the tie-out) and find the as-filed
tag(s) on the relevant statement whose value equals that residual (either sign) -- the exact
component we fail to capture -- then aggregate the tag names so the engine fix is precise, not guessed.

USAGE
  python r2k_identity_probe.py IS_NI IS      # income-statement leg (default)
  python r2k_identity_probe.py IS_NCI IS
  python r2k_identity_probe.py IS_GP IS

Reads tieout_report.csv (the identity's breaks + residual) and dera_facts.csv (as-filed tags on the
named statement). Writes <identity>_probe.txt.
"""
from pathlib import Path
import os, csv, sys
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FACTS = BASE / "dera_facts.csv"
TOL_REL, TOL_ABS = 0.01, 1_000_000.0

IDENT = sys.argv[1] if len(sys.argv) > 1 else "IS_NI"
STMT = sys.argv[2] if len(sys.argv) > 2 else "IS"
OUT = BASE / f"{IDENT}_probe.txt"


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b):
    return a is not None and b is not None and abs(a - b) <= max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def main():
    gaps = {}
    for r in csv.DictReader(open(TIE, encoding="utf-8")):
        if r["identity"].startswith(IDENT) and r["result"] == "BREAK":
            g = fnum(r["residual"])
            if g is not None and abs(g) > TOL_ABS:
                gaps[(r["cik"], r["fiscal_year"])] = g
    ciks = {c for c, _ in gaps}
    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == STMT and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v
    matched = Counter()
    explained = 0
    for k, g in gaps.items():
        # the missing item could be added or subtracted -> match the tag to +gap or -gap
        hits = [t for t, v in facts.get(k, {}).items() if close(v, g) or close(v, -g)]
        if hits:
            explained += 1
            for t in hits:
                matched[t] += 1
    L = [f"{IDENT} PROBE  --  {len(gaps):,} breaks; {explained:,} have an as-filed {STMT} tag equal to the gap",
         "", "TAGS THAT EQUAL THE GAP (candidate components to add/handle in the cascade):"]
    for tag, n in matched.most_common(40):
        L.append(f"   {n:>5}  {tag}")
    L.append(f"\n{len(gaps) - explained:,} breaks have NO single tag == gap (multi-component / different cause).")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:50]))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
