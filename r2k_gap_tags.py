"""
r2k_gap_tags.py  --  extract JUST the slice of dera_facts.csv needed to fix the remaining material
blanks, so you don't have to move the whole (500 MB+) facts file. Runs locally against the full file
and writes a small gap_tag_queue.csv (a few hundred rows) to hand back for tag-family review.

For every COVERED R2000G company-year where a target metric is BLANK but Morningstar has a material
value (a real number that should exist), it pulls that filing's as-filed facts FROM THE RIGHT
STATEMENT and lists the candidate tags whose value sits near the Morningstar target -- i.e. the
non-standard tag the classifier's list is missing. Ranked by index weight so the material names lead.

This is diagnose-don't-plug: the queue only NAMES the as-filed tag to adopt; Morningstar is the locator,
never the value. Reviewing the queue tells us which tag families to add to the classifier / recovery.

INPUTS   r2k_panel.csv, fundamentals_dera_resolved.csv (or fundamentals_dera.csv), morningstar_long.csv,
         dera_facts.csv   (all in the working folder; dera_facts is streamed once, never loaded whole)
OUTPUT   gap_tag_queue.csv   (small -- safe to upload)
RUN      python r2k_gap_tags.py
"""
import csv
import os
from collections import defaultdict
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
PANEL = BASE / "r2k_panel.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
DERA = BASE / "fundamentals_dera.csv"
MS = BASE / "morningstar_long.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "gap_tag_queue.csv"

# metric -> (resolved column, Morningstar name, statement the tag must come from). MS COGS is stored
# negative, so we compare on absolute value for cost_of_revenue.
TARGETS = {
    "revenue":          ("revenue", "Total Revenue", "IS"),
    "gross_profit":     ("gross_profit", "Gross Profit", "IS"),
    "operating_income": ("operating_income", "Total Operating Profit Loss", "IS"),
    "cost_of_revenue":  ("cost_of_revenue", "Cost Of Revenue", "IS"),
    "cash":             ("cash", "Cash And Cash Equivalents", "BS"),
}
NEAR = 0.25            # list candidate tags within +/-25% of the target (the missing tag is usually <8%)
MAX_NAMES_PER_METRIC = 120   # cap so the queue stays small; names are taken highest-index-weight first
MAX_CAND_PER_NAME = 6


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ck(c):
    s = str(c).strip()
    return str(int(float(s))) if s.replace(".", "").isdigit() else s


def _fy(s):
    s = str(s).strip()
    return s[:-2] if s.endswith(".0") else s


def main():
    for p in (PANEL, MS, FACTS):
        if not p.exists():
            raise SystemExit(f"!! {p.name} not found in {BASE.resolve()} -- run from the project folder.")
    resolved = RESOLVED if RESOLVED.exists() else DERA

    # covered R2000G weight + ticker
    mem, tick = defaultdict(float), {}
    for r in csv.DictReader(open(PANEL, encoding="utf-8")):
        if r.get("index") != "R2KG" or r.get("covered") not in ("1", "True", "true"):
            continue
        k = (_ck(r.get("cik", "")), _fy(r.get("fy0", "")))
        mem[k] += _num(r.get("weight")) or 0
        tick[k] = r.get("ticker", "")

    # our present values
    fund = {}
    for r in csv.DictReader(open(resolved, encoding="utf-8")):
        fund[(_ck(r.get("cik", "")), _fy(r.get("fiscal_year", "")))] = r

    # Morningstar targets for the metrics we're chasing
    want = {name: fld for fld, (_c, name, _s) in TARGETS.items()}
    msv = defaultdict(dict)
    for row in csv.DictReader(open(MS, newline="", encoding="utf-8", errors="replace")):
        fld = want.get(row.get("metric", ""))
        if fld is None:
            continue
        v = _num(row.get("value"))
        k = (_ck(row.get("cik", "")), _fy(row.get("fiscal_year", "")))
        if v is not None and k[1] and k not in msv[fld]:
            msv[fld][k] = v

    # the blanks worth chasing: covered, blank, Morningstar has a MATERIAL value, top by index weight
    need_rows = defaultdict(list)      # metric -> [(weight, cik, fy, ms_target)]
    need_ciks = set()
    for fld, (col, _name, _stmt) in TARGETS.items():
        cand = []
        for k, w in mem.items():
            row = fund.get(k)
            if row is None or _num(row.get(col)) is not None:
                continue
            t = msv[fld].get(k)
            if t is None or abs(t) < 1e6:
                continue
            cand.append((w, k[0], k[1], t))
        cand.sort(reverse=True)
        need_rows[fld] = cand[:MAX_NAMES_PER_METRIC]
        need_ciks |= {(c, fy) for _w, c, fy, _t in need_rows[fld]}

    # ONE streaming pass over the big facts file: keep only facts for the needed (cik, fy)
    ck_needed = {c for c, _fy in need_ciks}
    facts = defaultdict(list)          # (cik, fy) -> [(tag, value, stmt)]
    with open(FACTS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = _ck(r.get("cik", ""))
            if c not in ck_needed or (r.get("uom") or "USD") != "USD":
                continue
            fy = _fy(r.get("fiscal_year", ""))
            if (c, fy) not in need_ciks:
                continue
            v = _num(r.get("value"))
            if v is not None:
                facts[(c, fy)].append((r.get("tag", ""), v, r.get("stmt", "")))

    # build the queue: for each blank, the candidate as-filed tags near the target, right statement
    out_rows = []
    for fld, (col, _name, stmt) in TARGETS.items():
        for w, c, fy, t in need_rows[fld]:
            fr = facts.get((c, fy), [])
            cands = []
            for tag, v, st in fr:
                if st and stmt and st != stmt:
                    continue
                base = max(abs(t), 1.0)
                rel = abs(abs(v) - abs(t)) / base if fld == "cost_of_revenue" else abs(v - t) / base
                if rel <= NEAR:
                    cands.append((rel, tag, v, st))
            cands.sort()
            if not cands:
                out_rows.append({"metric": fld, "ticker": tick.get((c, fy), ""), "cik": c, "fy": fy,
                                 "index_wt_bp": round(1e4 * w / (sum(mem.values()) or 1), 2),
                                 "ms_target": f"{t:.0f}", "candidate_tag": "(no as-filed fact within 25%)",
                                 "candidate_value": "", "pct_off": "", "stmt": ""})
                continue
            for rel, tag, v, st in cands[:MAX_CAND_PER_NAME]:
                out_rows.append({"metric": fld, "ticker": tick.get((c, fy), ""), "cik": c, "fy": fy,
                                 "index_wt_bp": round(1e4 * w / (sum(mem.values()) or 1), 2),
                                 "ms_target": f"{t:.0f}", "candidate_tag": tag,
                                 "candidate_value": f"{v:.0f}", "pct_off": f"{100*(v-t)/t:+.1f}", "stmt": st})

    cols = ["metric", "ticker", "cik", "fy", "index_wt_bp", "ms_target", "candidate_tag",
            "candidate_value", "pct_off", "stmt"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)

    n_names = sum(len(need_rows[f]) for f in TARGETS)
    print(f"  scanned {FACTS.name} for {len(need_ciks):,} blank company-years across {len(ck_needed):,} CIKs")
    for fld in TARGETS:
        got = len({(r["cik"], r["fy"]) for r in out_rows
                   if r["metric"] == fld and not r["candidate_tag"].startswith("(no")})
        print(f"    {fld:>16}: {len(need_rows[fld]):>3} material blanks, a candidate tag found for {got}")
    print(f"  -> {OUT.name} ({len(out_rows):,} rows) -- small, safe to upload for tag-family review")


if __name__ == "__main__":
    main()
