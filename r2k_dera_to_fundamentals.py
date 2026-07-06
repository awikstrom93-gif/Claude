"""
============================================================
r2k_dera_to_fundamentals.py  --  adapter: map the identity-validated fundamentals_dera.csv into
the column schema steps 3-9 already read (edgar_annual_fundamentals_ASFILED.csv), so the whole
analytics pipeline runs on the DERA foundation with NO changes to those scripts.
============================================================
Joins:
  fundamentals_dera.csv      the standardized cascade per (cik, fiscal_year)   [r2k_dera_classify]
  dera_filing_index.csv      fye_date (period) + filed_date + name             [r2k_dera_index]
  securities_crosswalk.csv   ticker per cik (optional)                          [r2k_morningstar_parse]

Column mapping (DERA -> pipeline):
  parent_equity -> stockholders_equity   cfo -> operating_cash_flow   sector insurer->insurance,
  commercial->general (so the financial-sector handling in the analytics still fires). The full
  cascade (ebitda, interest_expense, net_income_to_common, total_equity, ...) is carried as extra
  columns for future analytics; existing steps ignore what they don't read. Blank stays blank.

OUTPUT  edgar_annual_fundamentals_ASFILED.csv  (override with R2KG_FUND_OUT; back up the old one)
RUN:  python r2k_dera_to_fundamentals.py
SELFTEST: python r2k_dera_to_fundamentals.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
# prefer the cross-source-RESOLVED fundamentals if r2k_resolve.py has produced them, so the
# analytics run on the verified/adopted values; fall back to the raw DERA rebuild otherwise.
FUND_DERA = (BASE / "fundamentals_dera_resolved.csv") if (BASE / "fundamentals_dera_resolved.csv").exists() \
    else (BASE / "fundamentals_dera.csv")
INDEX = BASE / "dera_filing_index.csv"
XWALK = BASE / "securities_crosswalk.csv"
OUT = BASE / os.environ.get("R2KG_FUND_OUT", "edgar_annual_fundamentals_ASFILED.csv")
OVERRIDES = BASE / "manual_value_overrides.csv"   # confirmed one-off corrections (audited, in git)

SECTOR_MAP = {"insurer": "insurance", "bank": "bank", "commercial": "general"}
# DERA field -> pipeline field
DIRECT = {"revenue": "revenue", "net_income": "net_income", "operating_income": "operating_income",
          "gross_profit": "gross_profit", "tax_expense": "tax_expense", "pretax_income": "pretax_income",
          "total_assets": "total_assets", "cash": "cash",
          "short_term_investments": "short_term_investments", "total_debt": "total_debt",
          "capex": "capex", "free_cash_flow": "free_cash_flow",
          "parent_equity": "stockholders_equity", "cfo": "operating_cash_flow"}
EXTRA = ["ebitda", "depreciation_amortization", "interest_expense", "net_income_consolidated",
         "minority_interest", "discontinued_operations", "net_income_to_common",
         "total_current_assets", "total_current_liabilities", "total_liabilities", "total_equity",
         "redeemable_nci", "total_debt_incl_leases", "operating_lease_liability", "debt_flag",
         "restricted_cash", "cash_total", "retained_earnings", "dividends_paid", "share_based_comp",
         "ppe_net", "cfi", "cff", "confidence", "breaks", "source"]
PIPE_FIELDS = ["cik", "ticker", "name", "fiscal_year", "fye_date", "filed_date",
               "revenue", "net_income", "operating_income", "gross_profit", "tax_expense",
               "pretax_income", "stockholders_equity", "total_assets", "cash",
               "short_term_investments", "long_term_investments", "restricted_cash", "total_debt",
               "operating_cash_flow", "capex", "free_cash_flow", "reporting_basis", "currency",
               "sector"] + EXTRA


def iso(yyyymmdd):
    s = (yyyymmdd or "").strip()
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def load_index():
    """(cik, fiscal_year) -> (fye_date, filed_date, name). Prefer original; latest filed if dup."""
    idx = {}
    if not INDEX.exists():
        print(f"  (warning: {INDEX.name} missing -> fye_date/filed_date/name will be blank)")
        return idx
    for r in csv.DictReader(open(INDEX, encoding="utf-8")):
        cik = str(int(r["cik"])) if r["cik"].isdigit() else r["cik"]
        per = r.get("period", ""); yr = per[:4]
        if not yr.isdigit():
            continue
        key = (cik, yr)
        cand = (iso(per), iso(r.get("filed", "")), r.get("name", ""), r.get("is_original") == "Y", r.get("filed", ""))
        cur = idx.get(key)
        # prefer is_original; among same, latest filed
        if cur is None or (cand[3] and not cur[3]) or (cand[3] == cur[3] and cand[4] > cur[4]):
            idx[key] = cand
    return {k: v[:3] for k, v in idx.items()}


def load_tickers():
    t = {}
    if XWALK.exists():
        for r in csv.DictReader(open(XWALK, encoding="utf-8")):
            c = (r.get("cik") or "").strip()
            if c.isdigit():
                t.setdefault(str(int(c)), r.get("ticker", ""))
    return t


def run(dera_rows, idx, tickers):
    out = []
    for r in dera_rows:
        cik = str(int(r["cik"])) if r["cik"].isdigit() else r["cik"]
        fy = r["fiscal_year"]
        fye, filed, name = idx.get((cik, fy), ("", "", ""))
        rec = {k: "" for k in PIPE_FIELDS}
        rec.update(cik=cik, ticker=tickers.get(cik, ""), name=name, fiscal_year=fy,
                   fye_date=fye, filed_date=filed,
                   reporting_basis=r.get("taxonomy", ""), currency="USD",
                   sector=SECTOR_MAP.get(r.get("sector", ""), "general"))
        for dk, pk in DIRECT.items():
            rec[pk] = r.get(dk, "")
        for k in EXTRA:
            rec[k] = r.get(k, "")
        out.append(rec)
    return out


def load_overrides():
    """([(cik, fiscal_year, field, value, note)], n_unapproved) -- manual corrections for DEFINITE
    extraction errors the engine cannot self-catch (e.g. a filing whose balance-sheet XBRL carried the
    wrong `decimals` scale, so it foots internally but is ~1000x off the income statement).

    SAFETY -- only rows with an explicit `approved` flag (yes/true/1) are applied. The file doubles as a
    CANDIDATE LEDGER: reconciliation/recovery passes append proposals (calcbench sign-flip candidates,
    revenue-fix candidates, row-consistency worklists) whose own notes say "VERIFY". An unapproved row
    is a PROPOSAL, not a correction, and must never silently rewrite an as-filed value. If no `approved`
    column exists at all, NOTHING is applied (the whole file is treated as an unvetted ledger)."""
    if not OVERRIDES.exists():
        return [], 0
    out, unapproved = [], 0
    for r in csv.DictReader(open(OVERRIDES, encoding="utf-8")):
        cik = (r.get("cik") or "").strip()
        cik = str(int(cik)) if cik.isdigit() else cik
        field = (r.get("metric") or r.get("field") or "").strip()   # 'metric' is the established column
        if not cik or not field:
            continue
        if str(r.get("approved", "")).strip().lower() not in ("y", "yes", "true", "1"):
            unapproved += 1
            continue
        note = "; ".join(x for x in ((r.get("source") or "").strip(), (r.get("note") or "").strip()) if x)
        out.append((cik, (r.get("fiscal_year") or "").strip(), field, (r.get("value") or "").strip(), note))
    return out, unapproved


def apply_overrides(rows, overrides):
    """Set rows[field]=value for each (cik, fiscal_year) match. A stale override (matches no row, or
    names a field not in the schema) is WARNED, never silently ignored -- so the correction list can't
    rot unnoticed."""
    by = {(r["cik"], r["fiscal_year"]): r for r in rows}
    applied = 0
    for cik, fy, field, value, note in overrides:
        r = by.get((cik, fy))
        if r is None:
            print(f"  !! override skipped: no row for cik {cik} fy {fy} ({field}={value}; {note})"); continue
        if field not in r:
            print(f"  !! override skipped: unknown field '{field}' (cik {cik} fy {fy})"); continue
        print(f"  ~ override cik {cik} fy {fy} {field}: {r.get(field, '')!r} -> {value!r}  ({note})")
        r[field] = value; applied += 1
    if overrides:
        print(f"  applied {applied}/{len(overrides)} manual override(s) from {OVERRIDES.name}")
    return rows


def main():
    if not FUND_DERA.exists():
        raise SystemExit(f"!! {FUND_DERA.name} not found -- run r2k_dera_classify.py first.")
    dera_rows = list(csv.DictReader(open(FUND_DERA, encoding="utf-8")))
    idx = load_index(); tickers = load_tickers()
    out = run(dera_rows, idx, tickers)
    overrides, unapproved = load_overrides()
    out = apply_overrides(out, overrides)
    if unapproved:
        print(f"  ({unapproved:,} row(s) in {OVERRIDES.name} are UNAPPROVED candidates -- skipped. "
              f"They are proposals (calcbench sign-flip / recovery worklists), not corrections; vet one "
              f"and set its 'approved' column to 'yes' to activate it.)")
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=PIPE_FIELDS, extrasaction="ignore")
        w.writeheader(); w.writerows(out)
    n_tk = sum(1 for r in out if r["ticker"])
    n_fye = sum(1 for r in out if r["fye_date"])
    print(f"  -> {OUT.name}: {len(out):,} rows  (ticker on {n_tk:,}; fye_date on {n_fye:,})")
    print(f"  sector map applied; parent_equity->stockholders_equity, cfo->operating_cash_flow")
    print(f"  steps 3-9 can now read this unchanged. (Back up any prior {OUT.name} first.)")


def selftest():
    dera = [dict(cik="1664703", fiscal_year="2024", sector="commercial", taxonomy="usgaap",
                 revenue="1473856000", net_income="-29227000", operating_income="22909000",
                 gross_profit="404648000", parent_equity="585216000", cfo="91998000",
                 total_assets="2657354000", ebitda="75957000", confidence="1.00", breaks="")]
    idx = {("1664703", "2024"): ("2024-12-31", "2025-02-27", "BLOOM ENERGY CORP")}
    out = run(dera, idx, {"1664703": "BE"})
    r = out[0]
    ok = (r["stockholders_equity"] == "585216000" and r["operating_cash_flow"] == "91998000"
          and r["ticker"] == "BE" and r["fye_date"] == "2024-12-31" and r["sector"] == "general"
          and r["ebitda"] == "75957000")
    print("mapped row:", {k: r[k] for k in ("ticker", "fye_date", "stockholders_equity",
          "operating_cash_flow", "sector", "ebitda")})
    print(f"  SELFTEST: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
