"""
============================================================
r2k_resolve.py  --  CROSS-SOURCE RESOLUTION (the 2x2, applied). For the company-years our
reconstruction can't tie -- the plausibility REVIEW tier (P&L/BS identity breaks + critical flags)
and the debt forks/misses -- adopt Morningstar's value WHERE MORNINGSTAR IS INTERNALLY CONSISTENT,
with full provenance. This is the framework's "x ours / check MS -> MS leads" row, applied only to
the names that need it. Everything that already ties is left untouched.

  Ours ties? | MS ties? | -> here we act only when OURS does NOT tie:
     x       |   v       -> adopt MS value (provenance = Morningstar), per line
     x       |   x       -> leave ours, mark 'manual' (neither source is reliable)

NOTHING is plugged blindly: MS is adopted only when its rebuilt statement foots, and only for the
lines at issue, and every adoption is logged.

INPUTS
  fundamentals_dera.csv      our rebuilt values + breaks + debt columns   [r2k_dera_classify]
  plausibility_flags.csv     per-name reliability tier                    [r2k_plausibility]
  morningstar_long.csv       Morningstar standardized statements          [r2k_morningstar_parse]
  debt_reconcile_detail.csv  lease-adjusted debt forks/misses (optional)  [r2k_debt_reconcile]

OUTPUTS
  edgar_annual_fundamentals_RESOLVED.csv   fundamentals with adopted values + a `source_*` trail
  resolution_audit.csv                     every adoption: cik, fy, field, dera, morningstar, reason

RUN:  python r2k_resolve.py
SELFTEST: python r2k_resolve.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

from r2k_dual_reconstruct_pilot import load_ms, ms_statement, compute_identities, ties_clean

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
FLAGS = BASE / "plausibility_flags.csv"
DEBT = BASE / "debt_reconcile_detail.csv"
OUT = BASE / "fundamentals_dera_resolved.csv"   # same schema as fundamentals_dera + provenance
AUDIT = BASE / "resolution_audit.csv"

TOL_REL, TOL_ABS = 0.01, 1_000_000.0
SANITY_CAP = 2.0e11

# Morningstar standardized line (ms_statement key) -> fundamentals_dera field
PL_BS_MAP = {"revenue": "revenue", "gross_profit": "gross_profit",
             "operating_income": "operating_income", "pretax": "pretax_income",
             "ni_parent": "net_income", "total_assets": "total_assets",
             "total_liabilities": "total_liabilities", "total_equity": "total_equity",
             "bs_cash": "cash"}


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def differs(a, b):
    if a is None:
        return b is not None
    if b is None:
        return False
    return abs(a - b) > max(TOL_ABS, TOL_REL * max(abs(a), abs(b)))


def load_flags():
    tier = {}
    if FLAGS.exists():
        for r in csv.DictReader(open(FLAGS, encoding="utf-8")):
            tier[(r["cik"], r["fiscal_year"])] = r["tier"]
    return tier


def load_debt():
    """(cik, fy) -> (ms_incl, ms_funded) for names whose lease-adjusted debt forks or is missing
    vs Morningstar (and MS isn't a bad-lease row)."""
    out = {}
    if not DEBT.exists():
        return out
    for r in csv.DictReader(open(DEBT, encoding="utf-8")):
        v = r.get("incl_verdict", "")
        mi, ml, mf = fnum(r.get("ms_incl")), fnum(r.get("ms_lease")), fnum(r.get("ms_funded"))
        if v in ("DIFF", "MS_only") and mi is not None and abs(mi) < SANITY_CAP \
                and not (ml is not None and ml > mi * 1.05):     # skip bad MS lease rows
            out[(r["cik"], r["fiscal_year"])] = (mi, mf)
    return out


def resolve(rows, tier, ms, debt):
    """rows = list of fundamentals dicts (mutated copies returned). Returns (resolved_rows, audit)."""
    SECTOR_FIN = {"bank", "insurer", "insurance"}
    audit = []
    out = []
    for r in rows:
        r = dict(r)
        key = (r["cik"], r["fiscal_year"])
        sector = r.get("sector", "")
        srcs = []

        # ---- P&L / BS resolution: only for review-tier, non-financial, where MS foots ----
        if tier.get(key) == "review" and sector not in SECTOR_FIN:
            m = ms.get(key)
            if m:
                S = ms_statement(m)
                msec = "commercial" if sector in ("commercial", "general", "") else sector
                if ties_clean(compute_identities(S, msec)):
                    for msf, ff in PL_BS_MAP.items():
                        mv, ov = S.get(msf), fnum(r.get(ff))
                        if mv is not None and differs(ov, mv):
                            audit.append(dict(cik=key[0], fiscal_year=key[1], field=ff,
                                              dera=("" if ov is None else ov), morningstar=mv,
                                              reason="P&L/BS: ours broke, MS ties"))
                            r[ff] = mv; srcs.append(ff)
                else:
                    srcs.append("PL_BS:manual")   # MS doesn't foot either -> leave ours, flag

        # ---- debt resolution (independent of the P&L/BS tier; skip financials) ----
        if key in debt and sector not in SECTOR_FIN:
            mi, mf = debt[key]
            ov = fnum(r.get("total_debt_incl_leases"))
            if differs(ov, mi):
                audit.append(dict(cik=key[0], fiscal_year=key[1], field="total_debt_incl_leases",
                                  dera=("" if ov is None else ov), morningstar=mi,
                                  reason="debt: lease-adjusted fork/miss, MS leads"))
                r["total_debt_incl_leases"] = mi; srcs.append("total_debt_incl_leases")
                if mf is not None and mf >= 0:
                    r["total_debt"] = mf; srcs.append("total_debt")

        r["resolved_fields"] = ";".join(srcs)
        r["source"] = "Morningstar-resolved" if any(s and "manual" not in s for s in srcs) else \
                      ("manual-review" if srcs else "DERA")
        out.append(r)
    return out, audit


def main():
    if not FUND.exists():
        raise SystemExit(f"!! {FUND.name} not found -- run r2k_dera_classify.py first.")
    rows = list(csv.DictReader(open(FUND, encoding="utf-8")))
    tier = load_flags()
    debt = load_debt()
    # MS only needs the names we might touch (review-tier + debt issues)
    target_ciks = {k[0] for k, t in tier.items() if t == "review"} | {k[0] for k in debt}
    ms = load_ms(target_ciks)

    resolved, audit = resolve(rows, tier, ms, debt)

    fields = list(rows[0].keys()) + ["resolved_fields", "source"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(resolved)
    with open(AUDIT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["cik", "fiscal_year", "field", "dera", "morningstar", "reason"])
        w.writeheader(); w.writerows(audit)

    from collections import Counter
    n_res = sum(1 for r in resolved if r["source"] == "Morningstar-resolved")
    n_man = sum(1 for r in resolved if r["source"] == "manual-review")
    byfield = Counter(a["field"] for a in audit)
    print(f"  -> {OUT.name}  ({len(resolved):,} rows)")
    print(f"  -> {AUDIT.name}  ({len(audit):,} field adoptions)")
    print(f"  resolved from Morningstar: {n_res:,} company-years   still manual (MS doesn't foot): {n_man:,}")
    print(f"  adoptions by field: {dict(byfield.most_common())}")


def selftest():
    # one review name whose net income broke; MS foots and disagrees on NI -> adopt MS NI.
    # values in realistic dollars (millions) so the $1M absolute tolerance behaves as it will live.
    M = 1_000_000
    rows = [dict(cik="1", fiscal_year="2024", sector="general", revenue=str(1000*M), gross_profit=str(400*M),
                 operating_income=str(100*M), pretax_income=str(90*M), net_income=str(999*M),  # wrong NI
                 total_assets=str(5000*M), total_liabilities=str(3000*M), total_equity=str(2000*M), cash=str(300*M),
                 total_debt=str(800*M), total_debt_incl_leases=str(800*M)),
            dict(cik="2", fiscal_year="2024", sector="general", revenue=str(500*M), gross_profit=str(200*M),
                 operating_income=str(50*M), pretax_income=str(40*M), net_income=str(30*M), total_assets=str(900*M),
                 total_liabilities=str(400*M), total_equity=str(500*M), cash=str(100*M),
                 total_debt=str(100*M), total_debt_incl_leases=str(100*M))]   # clean, untouched
    tier = {("1", "2024"): "review", ("2", "2024"): "clean"}
    # MS for cik 1: a footing statement with NI=65M
    ms = {("1", "2024"): {"Total Revenue": 1000*M, "Cost Of Revenue": -600*M, "Gross Profit": 400*M,
                          "Operating Income Expenses": -300*M, "Total Operating Profit Loss": 100*M,
                          "Pretax Income": 90*M, "Provision For Income Tax": -25*M,
                          "Net Income From Continuing Operations": 65*M,
                          "Net Income After Non Controlling Minority Interests": 65*M,
                          "Total Assets": 5000*M, "Total Liabilities": 3000*M, "Total Equity": 2000*M,
                          "Cash And Cash Equivalents": 300*M}}
    debt = {("1", "2024"): (1200.0*M, 1000.0*M)}   # lease-adjusted debt fork -> adopt 1200M / funded 1000M
    resolved, audit = resolve(rows, tier, ms, debt)
    by = {r["cik"]: r for r in resolved}
    M = 1_000_000
    checks = [("NI adopted 999->65", float(by["1"]["net_income"]) == 65*M),
              ("debt adopted ->1200", float(by["1"]["total_debt_incl_leases"]) == 1200*M),
              ("funded adopted ->1000", float(by["1"]["total_debt"]) == 1000*M),
              ("source tagged", by["1"]["source"] == "Morningstar-resolved"),
              ("clean name untouched", float(by["2"]["net_income"]) == 30*M and by["2"]["source"] == "DERA"),
              ("audit has NI row", any(a["field"] == "net_income" for a in audit))]
    for n, ok in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n  SELFTEST: {'PASS' if all(o for _, o in checks) else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
