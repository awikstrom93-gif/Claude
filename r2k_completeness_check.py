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

    # which (company, metric) pairs are reported in SOME year -> a blank elsewhere is a likely
    # extraction miss (recoverable); a metric a company NEVER reports is structural (e.g. banks
    # have no gross profit), not a bug.
    reports = defaultdict(set)
    for r in rows:
        c = r.get("cik", "")
        for m in DURATION_METRICS:
            if not blank(r.get(m)):
                try: reports[(c, m)].add(int(float(r["fiscal_year"])))
                except (TypeError, ValueError): pass
    def is_recoverable(cik, m):           # company reports this metric in at least one year
        return bool(reports.get((cik, m)))

    # per-metric blank rate, split structural vs recoverable
    bs_rows = [r for r in rows if not blank(r.get("total_assets"))]
    print(f"  blanks among the {len(bs_rows)} rows WITH a balance sheet  (recoverable = company reports it"
          f" in another year; structural = it never does):")
    print(f"    {'metric':<22}{'blank%':>8}{'recoverable':>13}{'structural':>12}")
    for m in DURATION_METRICS + BALANCE_METRICS:
        nb = [r for r in bs_rows if blank(r.get(m))]
        rec = sum(1 for r in nb if m in DURATION_METRICS and is_recoverable(r.get("cik",""), m))
        struct = len(nb) - rec
        print(f"    {m:<22}{100*len(nb)/max(len(bs_rows),1):>7.1f}%{rec:>13}{struct:>12}")

    # income-statement-incomplete rows + by year, split by whether ANY missing metric is recoverable
    suspects = []
    by_year = defaultdict(int)
    n_rec_rows = 0
    for r in bs_rows:
        if any(blank(r.get(m)) for m in CORE):
            miss = [m for m in DURATION_METRICS if blank(r.get(m))]
            rec_miss = [m for m in miss if is_recoverable(r.get("cik",""), m)]
            suspects.append((r.get("cik",""), r.get("ticker",""), r.get("fiscal_year",""), miss, rec_miss))
            if rec_miss: n_rec_rows += 1
            try: by_year[int(float(r["fiscal_year"]))] += 1
            except (TypeError, ValueError): pass

    print(f"\n  income-statement-incomplete rows (balance sheet present, a core metric blank): {len(suspects)}")
    print(f"    of which {n_rec_rows} have a RECOVERABLE missing metric (the rest are structural "
          f"non-reporting, not a bug)")
    print(f"    by fiscal year:")
    for y in sorted(by_year):
        print(f"      {y}: {by_year[y]}")

    # worst companies that have RECOVERABLE misses (the ones worth fixing)
    perco = defaultdict(int)
    for cik, tk, fy, miss, rec_miss in suspects:
        if rec_miss: perco[(cik, tk)] += 1
    worst = sorted(perco.items(), key=lambda x: -x[1])[:20]
    print(f"\n  companies with the most RECOVERABLE-incomplete years (top 20):")
    for (cik, tk), c in worst:
        print(f"    {tk:<8}{c} years")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["cik", "ticker", "fiscal_year", "missing_duration_metrics", "recoverable_missing"])
        for cik, tk, fy, miss, rec_miss in suspects:
            w.writerow([cik, tk, fy, ";".join(miss), ";".join(rec_miss)])
    print(f"\n  -> {OUT.name}  (feeds r2k_recover_missing.py)")
    print("  Structural blanks (a metric a company never reports, e.g. banks have no gross profit)")
    print("  are left alone by the recovery -- it only fills values that actually exist in the filing.")
    print("  Run r2k_recover_missing.py --dry-run to see the real per-metric recovery counts.")


if __name__ == "__main__":
    main()
