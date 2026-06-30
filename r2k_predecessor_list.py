"""
r2k_predecessor_list.py  --  list every CIK the engine flagged with PREDECESSOR (reverse-merger) years,
showing the boundary: registrant NAME and total ASSETS just before vs just after, so we can eyeball
that each is a genuine entity change (a different, usually larger, company occupied the CIK) and not a
false positive (a transformative acquisition with a rebrand, or a DERA name quirk).

Reads fundamentals_dera.csv (entity_flag, total_assets) + dera_filing_index.csv (registrant name).
Writes predecessor_list.txt.   RUN:  python r2k_predecessor_list.py
"""
from pathlib import Path
import os, csv, re
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
INDEX = BASE / "dera_filing_index.csv"
OUT = BASE / "predecessor_list.txt"

_SUF = ("incorporated", "corporation", "company", "holdings", "holding", "group", "industries",
        "international", "enterprises", "inc", "corp", "co", "ltd", "llc", "lp", "plc", "the",
        "sa", "nv", "ag", "trust", "partners")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def norm(s):
    s = re.sub(r"[^a-z0-9 ]", "", (s or "").lower())
    for suf in _SUF:
        s = re.sub(rf"\b{suf}\b", "", s)
    return re.sub(r"\s+", "", s)


def main():
    flag, assets = {}, {}
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        k = (r["cik"], r["fiscal_year"])
        flag[k] = r.get("entity_flag", "")
        assets[k] = fnum(r.get("total_assets"))
    name = {}
    for r in csv.DictReader(open(INDEX, encoding="utf-8")):
        fy = r.get("fy", "")
        if str(fy).isdigit():
            name[(r.get("cik", ""), str(fy))] = r.get("name", "")

    # per cik: the set of predecessor years and the rest
    by = defaultdict(lambda: {"pred": [], "cur": []})
    for (cik, fy), fl in flag.items():
        (by[cik]["pred"] if fl == "PREDECESSOR" else by[cik]["cur"]).append(fy)
    flagged = {c: d for c, d in by.items() if d["pred"]}

    L = [f"PREDECESSOR (reverse-merger) FLAG LIST  --  {len(flagged)} CIKs, "
         f"{sum(len(d['pred']) for d in flagged.values())} company-years excluded", "",
         "boundary = last predecessor year -> first current year  (name & assets each side)", ""]
    suspect = []
    for cik in sorted(flagged, key=lambda c: -(max((assets.get((c, y)) or 0) for y in flagged[c]["pred"]))):
        d = flagged[cik]
        ly = max(d["pred"]); ny = min(d["cur"]) if d["cur"] else None
        nb, na = name.get((cik, ly), "?"), (name.get((cik, ny), "?") if ny else "?")
        ab, aa = assets.get((cik, ly)), (assets.get((cik, ny)) if ny else None)
        step = (max(ab, aa) / min(ab, aa)) if (ab and aa) else None
        L.append(f"  cik {cik}  pred {min(d['pred'])}-{ly} ({len(d['pred'])}y) -> cur {ny}")
        L.append(f"      name:  '{nb}'  ->  '{na}'")
        L.append(f"      assets: {ab and f'{ab:,.0f}'}  ->  {aa and f'{aa:,.0f}'}"
                 + (f"   ({step:.1f}x)" if step else ""))
        # heuristic false-positive hint: names share a meaningful root
        if nb != "?" and na != "?":
            a, b = norm(nb), norm(na)
            if a and b and (a[:5] == b[:5] or a in b or b in a):
                suspect.append(cik); L.append("      ^^ NOTE: names look related -- verify this is a real entity change")
        L.append("")
    if suspect:
        L.insert(1, f"  ** {len(suspect)} CIKs have related-looking names (possible false positives): "
                    + ", ".join(suspect[:30]))
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:120]))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
