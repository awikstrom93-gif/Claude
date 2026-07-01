"""
r2k_metric_gaps.py  --  the revenue-gap audit, generalized to EVERY fundamental. The revenue hole
(classifier extracted net income but not the top line, for filers using non-standard revenue tags)
was a class of bug, not a one-off: any metric can be BLANK for covered names if the filer's tag isn't
in that metric's selection list. This finds them.

For each panel metric it reports, weighted by R2000G index weight and by year:
  * blank%      -- share of covered index weight where the metric is MISSING, and
  * recoverable% -- of that, how much Morningstar HAS a value for (a real extraction gap we could
                    recover, like revenue), vs genuinely-absent (Morningstar also lacks it -- e.g.
                    gross profit for a financial, interest expense for a debt-free company).
The gap between blank% and recoverable% tells you which "holes" are real bugs vs legitimately empty.

Uses Morningstar only to TEST presence (is there a value to recover?), never to plug -- the actual
recovery, if warranted, is done as-filed by a metric-specific version of r2k_revenue_recover.py.

INPUTS   r2k_panel.csv (via get_panel) ; morningstar_long.csv
OUTPUTS  metric_gaps_report.txt ; metric_gaps.csv (per cik-year recoverable gaps, for follow-up)
RUN      python r2k_metric_gaps.py
"""
import csv
import os
from collections import defaultdict
from pathlib import Path

from r2k_universe import get_panel, BASE

MS = BASE / "morningstar_long.csv"
OUT = BASE / "metric_gaps_report.txt"
OUT_CSV = BASE / "metric_gaps.csv"

# panel metric field -> Morningstar metric name(s) that represent the same concept (presence test only)
MMAP = {
    "revenue":          ["Total Revenue"],
    "net_income":       ["Net Income Available To Common Stockholders",
                         "Net Income After Non Controlling Minority Interests"],
    "operating_income": ["Total Operating Profit Loss"],
    "gross_profit":     ["Gross Profit"],
    "assets":           ["Total Assets"],
    "equity":           ["Total Equity"],
    "cash":             ["Cash And Cash Equivalents"],
    "cfo":              ["Cash Flow From Operating Activities Indirect",
                         "Cash Generated From Operating Activities"],
    "fcf":              ["Free Cash Flow to Firm", "Free Cash Flow to Equity Holders"],
}


def _ck(c):
    s = str(c).strip()
    return str(int(s)) if s.replace(".", "").isdigit() else s


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_ms_presence():
    """{(cik, fy): set(panel_fields Morningstar has a numeric value for)} -- presence only."""
    rev = {name: fld for fld, names in MMAP.items() for name in names}
    has = defaultdict(set)
    if not MS.exists():
        raise SystemExit(f"!! {MS.name} not found.")
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            fld = rev.get(row.get("metric", ""))
            if fld is None:
                continue
            if _num(row.get("value")) is None:
                continue
            has[(_ck(row.get("cik", "")), str(row.get("fiscal_year", "")).strip())].add(fld)
    return has


def main():
    panel = [r for r in get_panel(index="R2KG") if r.get("covered")]
    years = sorted({int(r["year"]) for r in panel})
    has = load_ms_presence()

    def fy0_key(r):
        fy = str(r.get("fy0", "")).strip()
        if fy.endswith(".0"):
            fy = fy[:-2]
        return (_ck(r.get("cik", "")), fy)

    # per field, per year: index-weighted blank% and recoverable%
    rows_out = []
    summary = {}
    csv_rows = []
    for fld in MMAP:
        by_year = {}
        for y in years:
            yr = [r for r in panel if int(r["year"]) == y]
            tw = sum((r.get("weight") or 0) for r in yr) or 1e-9
            blank_w = recov_w = 0.0
            for r in yr:
                if r.get(fld) is None:
                    w = r.get("weight") or 0
                    blank_w += w
                    if fld in has.get(fy0_key(r), set()):
                        recov_w += w
                        csv_rows.append({"field": fld, "year": y, "cik": fy0_key(r)[0],
                                         "fy0": fy0_key(r)[1], "ticker": r.get("ticker", ""),
                                         "weight": round(r.get("weight") or 0, 4)})
            by_year[y] = (100 * blank_w / tw, 100 * recov_w / tw)
        avg_blank = sum(b for b, _ in by_year.values()) / len(years)
        avg_recov = sum(rc for _, rc in by_year.values()) / len(years)
        summary[fld] = (avg_blank, avg_recov, by_year)

    order = sorted(MMAP, key=lambda f: -summary[f][1])   # rank by recoverable hole
    L = ["METRIC EXTRACTION-GAP AUDIT  --  R2000G covered names, index-weighted",
         "  blank% = missing among covered index weight ; recoverable% = of that, Morningstar has a value",
         "  (blank - recoverable = genuinely absent: financials' gross profit, debt-free interest, etc.)",
         "",
         f"  {'metric':>16} {'blank% avg':>11} {'recoverable% avg':>17} {'recov% 2016':>12} {'recov% 2026':>12}   verdict"]
    for fld in order:
        ab, ar, by = summary[fld]
        r16 = by.get(2016, (0, 0))[1]
        r26 = by.get(max(years), (0, 0))[1]
        verdict = ("REAL GAP -- recover" if ar >= 1.0 else
                   "minor" if ar >= 0.3 else "mostly legitimate" if ab >= 3 else "clean")
        L.append(f"  {fld:>16} {ab:>11.1f} {ar:>17.1f} {r16:>12.1f} {r26:>12.1f}   {verdict}")
    L += ["",
          "  REAL GAP = a revenue-style hole worth an as-filed recovery pass (blank but Morningstar has it).",
          "  'mostly legitimate' = blank but Morningstar also lacks it (the value genuinely isn't reported)."]
    OUT.write_text("\n".join(L), encoding="utf-8")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader(); w.writerows(csv_rows)
    print("\n".join(L))
    print(f"\n  -> {OUT.name} ; {OUT_CSV.name} ({len(csv_rows):,} recoverable cik-year gaps)")


if __name__ == "__main__":
    main()
