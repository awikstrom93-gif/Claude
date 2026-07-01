"""
r2k_metric_crosscheck.py  --  the OTHER half of data quality. r2k_metric_gaps.py finds BLANKS;
this finds PRESENT-BUT-WRONG values -- a figure that is filled and internally consistent (passes the
classifier's tie-out) but is still off (e.g. an understated revenue picked from a partial tag, with GP
derived from it so revenue-COGS=GP still holds). Internal identities CANNOT catch that -- only an
EXTERNAL reference can. This compares every present value to Morningstar, index-weighted.

For each metric it reports:
  * tested%     -- index weight where we AND Morningstar both have a value,
  * median ratio (ours / Morningstar) -- the BIAS detector: a median far from 1.00 means a SYSTEMATIC
                  under/overstatement (a mapping or selection problem), not noise,
  * off>10% / off>25% -- index weight where the two materially disagree (individual review candidates).
And it writes a worklist of the material disagreements, ranked by index weight x size of the gap.

A disagreement is NOT proof of error: as-filed vs Morningstar-adjusted, restatements, gross-vs-net, and
definitional differences (esp. operating income) all show up. Read the MEDIAN for systematic bias and
the worklist for names to eyeball. Diagnostic only -- it never changes the data.

INPUTS   r2k_panel.csv (via get_panel) ; fundamentals_dera[_resolved].csv ; morningstar_long.csv
OUTPUTS  metric_crosscheck_report.txt ; metric_crosscheck.csv (per-name material disagreements)
RUN      python r2k_metric_crosscheck.py
"""
import csv
import statistics
from collections import defaultdict

from r2k_universe import get_panel, BASE

MS = BASE / "morningstar_long.csv"
FUND = (BASE / "fundamentals_dera_resolved.csv") if (BASE / "fundamentals_dera_resolved.csv").exists() \
    else (BASE / "fundamentals_dera.csv")
OUT = BASE / "metric_crosscheck_report.txt"
OUT_CSV = BASE / "metric_crosscheck.csv"

# panel field -> (fundamentals_dera column, Morningstar metric name(s)) for the VALUE comparison
MMAP = {
    "revenue":          ("revenue", ["Total Revenue"]),
    "net_income":       ("net_income", ["Net Income Available To Common Stockholders",
                                        "Net Income After Non Controlling Minority Interests"]),
    "operating_income": ("operating_income", ["Total Operating Profit Loss"]),
    "gross_profit":     ("gross_profit", ["Gross Profit"]),
    "assets":           ("total_assets", ["Total Assets"]),
    "equity":           ("total_equity", ["Total Equity"]),
    "cash":             ("cash", ["Cash And Cash Equivalents"]),
}


def _ck(c):
    s = str(c).strip()
    return str(int(float(s))) if s.replace(".", "").isdigit() else s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _fy(s):
    s = str(s).strip()
    return s[:-2] if s.endswith(".0") else s


def load_ms_values():
    rev = {n: fld for fld, (_c, names) in MMAP.items() for n in names}
    out = defaultdict(dict)
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            fld = rev.get(row.get("metric", ""))
            if fld is None:
                continue
            v = _num(row.get("value"))
            k = (_ck(row.get("cik", "")), str(row.get("fiscal_year", "")).strip())
            if v is not None and k[1] and k not in out[fld]:   # first (annual) wins
                out[fld][k] = v
    return out


def main():
    # our present values by (cik, fy)
    fund = {}
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        fund[(_ck(r.get("cik", "")), _fy(r.get("fiscal_year", "")))] = r
    # index weight by (cik, fy0), + ticker
    panel = [r for r in get_panel(index="R2KG") if r.get("covered")]
    mem, tick = defaultdict(float), {}
    for r in panel:
        k = (_ck(r.get("cik", "")), _fy(r.get("fy0", "")))
        mem[k] += r.get("weight") or 0
        tick[k] = r.get("ticker", "")
    totw = sum(mem.values()) or 1e-9
    msv = load_ms_values()

    L = ["PRESENT-VALUE CROSS-CHECK  --  our as-filed value vs Morningstar, R2000G index-weighted",
         "  median ratio (ours/MS) = BIAS: far from 1.00 => systematic under/overstatement.",
         "  off>10% / off>25% = index weight where they materially disagree (review candidates).",
         "  A gap is not proof of error (restatements / adjusted / gross-vs-net / definitional).",
         "",
         f"  {'metric':>16} {'tested%':>8} {'median ratio':>13} {'off>10% wt':>11} {'off>25% wt':>11}   flag"]
    work = []
    for fld, (col, _names) in MMAP.items():
        tw = o10 = o25 = 0.0
        ratios = []
        for k, w in mem.items():
            ours = _num((fund.get(k) or {}).get(col))
            ms = msv.get(fld, {}).get(k)
            if ours is None or ms is None or abs(ms) < 1e6:
                continue
            tw += w
            rel = abs(ours - ms) / max(abs(ours), abs(ms))
            if ours > 0 and ms > 0:
                ratios.append(ours / ms)
            if rel > 0.10:
                o10 += w
            if rel > 0.25:
                o25 += w
                work.append({"metric": fld, "cik": k[0], "fy0": k[1], "ticker": tick.get(k, ""),
                             "ours": f"{ours:.0f}", "morningstar": f"{ms:.0f}",
                             "pct_off": f"{100*(ours-ms)/ms:+.0f}", "index_wt": round(w, 4)})
        med = statistics.median(ratios) if ratios else float("nan")
        flag = ("SYSTEMATIC BIAS -- investigate mapping/selection" if (med == med and abs(med - 1) > 0.05)
                else "high dispersion -- eyeball worklist" if 100 * o10 / totw > 10
                else "clean")
        L.append(f"  {fld:>16} {100*tw/totw:>8.1f} {med:>13.3f} {100*o10/totw:>11.1f} {100*o25/totw:>11.1f}   {flag}")
    OUT.write_text("\n".join(L), encoding="utf-8")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        if work:
            w = csv.DictWriter(f, fieldnames=list(work[0].keys()))
            w.writeheader()
            w.writerows(sorted(work, key=lambda x: -x["index_wt"] * abs(_num(x["pct_off"]) or 0)))
    print("\n".join(L))
    print(f"\n  -> {OUT.name} ; {OUT_CSV.name} ({len(work):,} material disagreements to review, by index weight x gap)")


if __name__ == "__main__":
    main()
