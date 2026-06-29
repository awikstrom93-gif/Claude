"""
r2k_mezz_probe.py  --  pinpoint the missing MEZZANINE (temporary / redeemable equity) behind the
BS_FOOTS "OTHER" breaks, where assets, liabilities and equity each match the filer's reported total
yet A != L + E (so a layer BETWEEN liabilities and permanent equity is uncaptured).

For each BS_FOOTS break it computes the gap = A - (L + E + mezz_we_have) and asks, among the as-filed
BS tags that look like temporary/redeemable equity or preferred stock (broad name pattern, per-share
values excluded):
   - does a SINGLE such tag equal the gap?            -> single-line mezzanine we should name/pattern
   - does the SUM of such tags equal the gap?         -> multi-series mezzanine we should AGGREGATE
   - neither                                          -> not mezzanine / deeper cause
It then reports which tag NAMES recur (so we extend the capture precisely) and how many breaks each
mode would explain.

Reads tieout_report.csv, fundamentals_dera.csv, dera_facts.csv. Writes mezz_probe.txt.
RUN:  python r2k_mezz_probe.py
"""
from pathlib import Path
import os, csv, re
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
TIE = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "mezz_probe.txt"

# broad "could be mezzanine" name pattern (wider than the engine's TEMP_EQ_PAT, to DISCOVER what we
# miss): temporary/redeemable equity, redeemable/convertible preferred, preferred subject to
# redemption, SPAC shares subject to possible redemption, mandatorily redeemable instruments.
MEZZ_PAT = re.compile(
    r"temporaryequity|mezzanine|subjecttopossibleredemption|subjecttoredemption|redemption|"
    r"redeemable|preferredstock|convertiblepreferred|warrant", re.I)
# never count per-share / share-count / fair-value-disclosure lines as a dollar balance
EXCL_PAT = re.compile(r"pershare|persharedata|shares|numberof|sharesoutstanding|sharesissued|"
                      r"parorstatedvalue|fairvaluedisclosure|liquidationpreferencepershare", re.I)


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def close(a, b, rel=0.01, ab=1_000_000.0):
    return a is not None and b is not None and abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def main():
    gaps = {}
    for r in csv.DictReader(open(TIE, encoding="utf-8")):
        if r["identity"].startswith("BS_FOOTS") and r["result"] == "BREAK":
            g = fnum(r["residual"])
            if g is not None and abs(g) > 0:
                gaps[(r["cik"], r["fiscal_year"])] = g
    fund = {(r["cik"], r["fiscal_year"]): r for r in csv.DictReader(open(FUND, encoding="utf-8"))}

    ciks = {c for c, _ in gaps}
    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as fh:
        rd = csv.reader(fh)
        h = next(rd)
        ci, fi, si, ti, ui, vi = (h.index("cik"), h.index("fiscal_year"), h.index("stmt"),
                                  h.index("tag"), h.index("uom"), h.index("value"))
        for row in rd:
            if len(row) > vi and row[ci] in ciks and row[si] == "BS" and row[ui] == "USD":
                v = fnum(row[vi])
                if v is not None:
                    facts[(row[ci], row[fi])][row[ti]] = v

    single = sum_mode = neither = 0
    single_tags = Counter()       # tag names that single-handedly equal the gap
    sum_tags = Counter()          # tag names appearing in a summed match
    examples = defaultdict(list)
    for k, g in gaps.items():
        d = facts.get(k, {})
        # candidate mezzanine tags: name matches MEZZ_PAT, not an excluded per-share/share line
        cand = {t: v for t, v in d.items() if MEZZ_PAT.search(t) and not EXCL_PAT.search(t)}
        # 1) single tag == gap (either sign)
        hit1 = [t for t, v in cand.items() if close(v, g) or close(v, -g)]
        if hit1:
            single += 1
            for t in hit1:
                single_tags[t] += 1
            if len(examples["single"]) < 8:
                examples["single"].append(f"{k[0]} {k[1]} gap={g:,.0f}  <- {hit1[0]}")
            continue
        # 2) sum of all candidate tags == gap (multi-series mezzanine)
        s = sum(cand.values())
        if cand and (close(s, g) or close(s, -g)):
            sum_mode += 1
            for t in cand:
                sum_tags[t] += 1
            if len(examples["sum"]) < 8:
                nm = "+".join(sorted(cand)[:4]) + ("+..." if len(cand) > 4 else "")
                examples["sum"].append(f"{k[0]} {k[1]} gap={g:,.0f} sum={s:,.0f}  <- {nm}")
            continue
        neither += 1
        if len(examples["neither"]) < 10:
            # show what mezz-ish tags exist (if any) to judge whether the pattern is just too narrow
            nm = ", ".join(f"{t}={v:,.0f}" for t, v in list(cand.items())[:4]) or "(no mezz-pattern tag present)"
            examples["neither"].append(f"{k[0]} {k[1]} gap={g:,.0f}  | {nm}")

    L = [f"MEZZANINE PROBE  --  {len(gaps):,} BS_FOOTS breaks", "",
         f"   {single:>5}  SINGLE mezz tag == gap   (name/pattern it)",
         f"   {sum_mode:>5}  SUM of mezz tags == gap  (AGGREGATE multi-series)",
         f"   {neither:>5}  neither                  (pattern too narrow, or not mezzanine)", "",
         "TOP TAGS -- single-line matches (extend the named/pattern capture):"]
    for t, n in single_tags.most_common(25):
        L.append(f"   {n:>4}  {t}")
    L.append("\nTOP TAGS -- appear in a summed (multi-series) match (aggregate these):")
    for t, n in sum_tags.most_common(25):
        L.append(f"   {n:>4}  {t}")
    for mode in ("single", "sum", "neither"):
        L.append(f"\n--- {mode} examples ---")
        L += ["     " + e for e in examples[mode]]
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
