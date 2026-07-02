"""
r2k_cash_probe.py  --  dump every as-filed balance-sheet CASH tag for covered names whose `cash`
disagrees with Morningstar (or is blank while Morningstar has a value), so the cash-selection fix can
be built from the real tags rather than guessed. Sibling of r2k_bankcash_probe.py, but for the
general cash column (commercial names): it surfaces cases where we picked a NARROW `Cash` (physical
only) over `CashAndCashEquivalents` (the total), a combined line that folds in RESTRICTED cash, or a
dimensioned/partial value.

For each target (cik, fy) it lists every BS cash-related tag with value and % of the Morningstar cash
target, and marks the one whose value == our current pick -- so the miss (and the right tag to prefer)
is visible at a glance.

Runs locally against the full dera_facts.csv (streamed once). Writes a small cash_probe.csv to hand back.

INPUTS   r2k_panel.csv, fundamentals_dera_resolved.csv (or fundamentals_dera.csv), morningstar_long.csv,
         dera_facts.csv
OUTPUT   cash_probe.csv
RUN      python r2k_cash_probe.py
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
OUT = BASE / "cash_probe.csv"

MAX_NAMES = 60          # top targets by index weight (keeps the output small)
OFF = 0.40              # target names where our cash is >40% off Morningstar (or blank)
# balance-sheet cash & cash-equivalent tags (what the cash pick chooses among)
COMPONENT = re.compile(r"cash", re.I)
EXCL = re.compile(r"period|increasedecrease|proceedsfrom|paymentsto|paymentsfor|netcashprovided|"
                  r"effectofexchange|noncash|paidincash|incometaxespaid|interestpaid|"
                  r"cashflow|acquiredcash|disposal(group)?cash", re.I)


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

    # targets: covered, Morningstar has cash, and our cash is blank OR >OFF from it
    targets = []
    for k, w in mem.items():
        r = fund.get(k)
        if r is None:
            continue
        t = ms_cash.get(k)
        if t is None or abs(t) < 1e6:
            continue
        ours = _num(r.get("cash"))
        if ours is not None and abs(ours - t) / max(abs(ours), abs(t)) <= OFF:
            continue
        targets.append((w, k, ours, t))
    targets.sort(reverse=True)
    targets = targets[:MAX_NAMES]
    need = {k for _w, k, _o, _t in targets}
    need_ciks = {c for c, _f in need}

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
    for w, k, ours, t in targets:
        comps = sorted(facts.get(k, {}).items(), key=lambda kv: -abs(kv[1]))
        tk = tick.get(k, "")
        if not comps:
            out_rows.append({"ticker": tk, "cik": k[0], "fy": k[1], "index_wt_bp": round(1e4 * w / totw, 2),
                             "our_cash": "" if ours is None else f"{ours:.0f}", "ms_cash": f"{t:.0f}",
                             "tag": "(no cash BS facts found)", "value": "", "pct_of_ms": "", "our_pick": ""})
            continue
        for tag, v in comps:
            is_pick = (ours is not None and abs(v - ours) <= max(1.0, 0.005 * abs(ours)))
            out_rows.append({"ticker": tk, "cik": k[0], "fy": k[1], "index_wt_bp": round(1e4 * w / totw, 2),
                             "our_cash": "" if ours is None else f"{ours:.0f}", "ms_cash": f"{t:.0f}",
                             "tag": tag, "value": f"{v:.0f}", "pct_of_ms": f"{100*v/t:.0f}",
                             "our_pick": "<== OURS" if is_pick else ""})

    cols = ["ticker", "cik", "fy", "index_wt_bp", "our_cash", "ms_cash", "tag", "value", "pct_of_ms", "our_pick"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out_rows)
    print(f"  scanned {FACTS.name} for {len(need):,} cash-mismatch names")
    print(f"  -> {OUT.name} ({len(out_rows):,} tag rows) -- small, safe to upload")
    print("     each name lists its BS cash tags + which one we picked, so the right preference is visible.")


if __name__ == "__main__":
    main()
