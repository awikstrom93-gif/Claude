"""
============================================================
r2k_completeness_check.py  --  audit metric completeness across the as-filed dataset.
============================================================
The same period-end mismatch that blanked net income also blanks the rest of the
income statement / cash-flow statement for those filings (all DURATION items), while
the balance sheet (INSTANT items) survives. This read-only audit surfaces that:

  - per-metric blank rate, overall and by fiscal year;
  - "income-statement-incomplete" rows = balance sheet present (total_assets) but a CORE
    duration metric (revenue / net_income / operating_income) blank -- the signature of
    an end-match miss, and the recovery candidates;
  - the companies with the most such years.

OUTPUT
    fundamentals_completeness.csv   one row per income-statement-incomplete company-year
                                    with which metrics are blank (feeds r2k_recover_missing.py)

RUN: python r2k_completeness_check.py
============================================================
"""
from pathlib import Path
import os, csv
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "edgar_annual_fundamentals_ASFILED.csv"
OUT = BASE / "fundamentals_completeness.csv"

DURATION_METRICS = ["revenue", "net_income", "operating_income", "gross_profit",
                    "pretax_income", "tax_expense", "operating_cash_flow", "capex", "free_cash_flow"]
BALANCE_METRICS = ["stockholders_equity", "total_assets", "cash", "total_debt"]
CORE = ["revenue", "net_income", "operating_income"]      # if any of these blank w/ a balance sheet -> suspect


def blank(v): return v is None or str(v).strip() == ""


def main():
    if not FUND.exists():
        raise SystemExit(f"!! {FUND.name} not found.")
    rows = list(csv.DictReader(open(FUND, encoding="utf-8-sig")))
    n = len(rows)
    print(f"  {n} company-year rows\n")

    # per-metric blank rate (overall) -- only counts rows that have a balance sheet, so genuinely
    # empty shell years don't dominate
    bs_rows = [r for r in rows if not blank(r.get("total_assets"))]
    print(f"  blank rate among the {len(bs_rows)} rows WITH a balance sheet:")
    for m in DURATION_METRICS + BALANCE_METRICS:
        nb = sum(1 for r in bs_rows if blank(r.get(m)))
        bar = "#" * int(40 * nb / max(len(bs_rows), 1))
        print(f"    {m:<22}{100*nb/max(len(bs_rows),1):>6.1f}%  {bar}")

    # income-statement-incomplete rows + by year
    suspects = []
    by_year = defaultdict(int)
    for r in bs_rows:
        if any(blank(r.get(m)) for m in CORE):
            miss = [m for m in DURATION_METRICS if blank(r.get(m))]
            suspects.append((r.get("cik",""), r.get("ticker",""), r.get("fiscal_year",""), miss))
            try: by_year[int(float(r["fiscal_year"]))] += 1
            except (TypeError, ValueError): pass

    print(f"\n  income-statement-incomplete rows (balance sheet present, a core metric blank): {len(suspects)}")
    print(f"    by fiscal year:")
    for y in sorted(by_year):
        print(f"      {y}: {by_year[y]}")

    # worst companies
    perco = defaultdict(int)
    for cik, tk, fy, miss in suspects: perco[(cik, tk)] += 1
    worst = sorted(perco.items(), key=lambda x: -x[1])[:20]
    print(f"\n  companies with the most incomplete years (top 20):")
    for (cik, tk), c in worst:
        print(f"    {tk:<8}{c} years")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cik", "ticker", "fiscal_year", "missing_duration_metrics"])
        for cik, tk, fy, miss in suspects:
            w.writerow([cik, tk, fy, ";".join(miss)])
    print(f"\n  -> {OUT.name}  (feeds r2k_recover_missing.py)")
    print("  These are recoverable from cached SEC data with a relaxed period-end match; run")
    print("  r2k_recover_missing.py --dry-run to see how many of ALL these metrics come back.")


if __name__ == "__main__":
    main()
