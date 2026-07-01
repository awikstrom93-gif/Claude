"""
r2k_bankcash_probe.py  --  dump the as-filed cash COMPONENT tags for bank-sector names whose `cash` is
blank but Morningstar has a value, so the proper bank-cash reconstruction can be built from the real
tags (not guessed). Bank "cash and cash equivalents" = cash/due-from-banks + interest-bearing deposits
(Fed reserves) + fed funds sold; a single narrow tag understates a large bank (TCBI $181M vs $7.9B).

Unlike r2k_gap_tags.py (which lists single tags NEAR the target), this lists EVERY balance-sheet
cash-component tag for the name -- because the answer is usually a SUM whose parts are each far from the
target. Output shows, per name, the components and their running sum so the sum formula is verifiable.

Runs locally against the full dera_facts.csv (streamed once, never loaded whole). Writes a small
bankcash_probe.csv to hand back.

INPUTS   r2k_panel.csv, fundamentals_dera_resolved.csv (or fundamentals_dera.csv), morningstar_long.csv,
         dera_facts.csv
OUTPUT   bankcash_probe.csv
RUN      python r2k_bankcash_probe.py
"""
import csv
import os
import re
from collections import defaultdict
from pathlib import Path

BASE = Path(os.environ.get("R2KG_BASE", "."))
PANEL = BASE / "r2k_panel.csv"
RESOLVED = BASE / "fundamentals_dera_resolved.csv"
DERA = BASE / "fundamentals_dera.csv"
MS = BASE / "morningstar_long.csv"
FACTS = BASE / "dera_facts.csv"
OUT = BASE / "bankcash_probe.csv"

MAX_NAMES = 60            # top bank-cash-blank names by index weight (keeps the output small)
# balance-sheet cash & cash-equivalent components for a bank (what sums to "cash and cash equivalents")
COMPONENT = re.compile(r"cashandcashequivalents|cashandduefrombanks|cashanddue|duefrombanks|"
                       r"interestbearingdeposit|noninterestbearingdeposit|"
                       r"federalfundssold|securitiespurchasedunderagreementstoresell|"
                       r"moneymarket|cashcashequivalents", re.I)
# exclude cash-FLOW and non-balance items that share the words
EXCL = re.compile(r"periodincrease|periodendingbalance|increasedecrease|proceedsfrom|paymentsto|"
                  r"restricted|pledged|fairvalue|interestrate|maturit|deposits(liabilit|held)|"
                  r"depositliabilit|timedeposit|savingsdeposit|demanddeposit|depositsdomestic|"
                  r"depositoryinstitution", re.I)


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

    mem, tick = defaultdict(float), {}
    for r in csv.DictReader(open(PANEL, encoding="utf-8")):
        if r.get("index") != "R2KG" or r.get("covered") not in ("1", "True", "true"):
            continue
        k = (_ck(r.get("cik", "")), _fy(r.get("fy0", "")))
        mem[k] += _num(r.get("weight")) or 0
        tick[k] = r.get("ticker", "")
    totw = sum(mem.values()) or 1e-9

    fund = {}
    for r in csv.DictReader(open(resolved, encoding="utf-8")):
        fund[(_ck(r.get("cik", "")), _fy(r.get("fiscal_year", "")))] = r

    ms_cash = {}
    for row in csv.DictReader(open(MS, newline="", encoding="utf-8", errors="replace")):
        if row.get("metric") != "Cash And Cash Equivalents":
            continue
        v = _num(row.get("value"))
        k = (_ck(row.get("cik", "")), _fy(row.get("fiscal_year", "")))
        if v is not None and k[1] and k not in ms_cash:
            ms_cash[k] = v

    # target names: BANK sector, cash BLANK, Morningstar has cash, top by index weight
    targets = []
    for k, w in mem.items():
        r = fund.get(k)
        if r is None or (r.get("sector") != "bank"):
            continue
        if _num(r.get("cash")) is not None:
            continue
        t = ms_cash.get(k)
        if t is None or abs(t) < 1e6:
            continue
        targets.append((w, k, t))
    targets.sort(reverse=True)
    targets = targets[:MAX_NAMES]
    need = {k for _w, k, _t in targets}
    need_ciks = {c for c, _fy in need}

    # one streaming pass over the big facts file: keep BS cash-component facts for the needed names
    facts = defaultdict(dict)
    with open(FACTS, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            c = _ck(r.get("cik", ""))
            if c not in need_ciks or (r.get("uom") or "USD") != "USD":
                continue
            key = (c, _fy(r.get("fiscal_year", "")))
            if key not in need:
                continue
            tag = r.get("tag", "")
            if (r.get("stmt", "") in ("BS", "")) and COMPONENT.search(tag) and not EXCL.search(tag):
                v = _num(r.get("value"))
                if v is not None:
                    facts[key][tag] = v

    out_rows = []
    for w, k, t in targets:
        comps = sorted(facts.get(k, {}).items(), key=lambda kv: -abs(kv[1]))
        tk = tick.get(k, "")
        if not comps:
            out_rows.append({"ticker": tk, "cik": k[0], "fy": k[1], "index_wt_bp": round(1e4 * w / totw, 2),
                             "ms_cash_target": f"{t:.0f}", "component_tag": "(no cash-component facts found)",
                             "value": "", "pct_of_target": ""})
            continue
        for tag, v in comps:
            out_rows.append({"ticker": tk, "cik": k[0], "fy": k[1], "index_wt_bp": round(1e4 * w / totw, 2),
                             "ms_cash_target": f"{t:.0f}", "component_tag": tag,
                             "value": f"{v:.0f}", "pct_of_target": f"{100*v/t:.0f}"})

    cols = ["ticker", "cik", "fy", "index_wt_bp", "ms_cash_target", "component_tag", "value", "pct_of_target"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    print(f"  scanned {FACTS.name} for {len(need):,} bank-cash-blank names")
    print(f"  -> {OUT.name} ({len(out_rows):,} component rows) -- small, safe to upload")
    print("     each name lists its balance-sheet cash components + % of the Morningstar target, so the")
    print("     correct sum (cash + interest-bearing deposits + fed funds sold) can be built from real tags.")


if __name__ == "__main__":
    main()
