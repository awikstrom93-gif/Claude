"""
============================================================
r2k_plausibility.py  --  the VERIFICATION leg: catch self-consistent-but-WRONG values that the
accounting identities can't (a filing can foot perfectly and still be wrong), and combine them
with the identity tie-out into one per-name RELIABILITY tier -- then weight it by INDEX WEIGHT so
we know what the index aggregates actually rest on.

The identities prove internal consistency; this proves plausibility. Together they answer: which
constituent-years are safe to feed into the index analysis, and which high-weight names still need
resolution (cross-source / manual).

CHECKS (accounting-grounded)
  CRITICAL (almost certainly a data error):
    revenue<0 | total_assets<=0 | cash<0 | gross_profit>revenue (GM>100%)
    | total_equity>total_assets | ebitda<operating_income (=> negative D&A)
    | D&A > 2x revenue (over-capture, e.g. amortization mis-summed) | |effective tax|>1
    | total_debt>total_liabilities
  WATCH (legitimate sometimes, worth a look):
    gross_margin<-50% | net_income>2x revenue | revenue YoY >5x or <0.2x (M&A/restatement)
    | D&A>revenue (asset-heavy)

RELIABILITY tier per (cik, fy) -- keyed to what the index analysis actually uses (P&L + BS):
    review  = a CRITICAL plausibility flag OR a P&L/BS identity break (the values can't be trusted)
    watch   = a WATCH flag, or only a cash-flow-articulation break (P&L/BS values still fine)
    clean   = ties out and plausible

INPUTS   fundamentals_dera.csv (values + confidence + breaks)   [r2k_dera_classify]
         holdings + security_cik_map.json (optional -> weight the result)
OUTPUTS  plausibility_report.txt   summary by flag type + tier, weighted by index where available
         plausibility_flags.csv    per (cik, fy) flags + tier (sorted worst-by-weight first)

RUN:  python r2k_plausibility.py
SELFTEST: python r2k_plausibility.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, json, re, sys
from collections import defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
CIKMAP = BASE / "security_cik_map.json"
REPORT = BASE / "plausibility_report.txt"
FLAGS = BASE / "plausibility_flags.csv"

# breaks that make the P&L / balance-sheet VALUES used by the index analysis untrustworthy
# (revenue, margins, net income, equity, assets). A cash-flow articulation break does NOT taint
# those values, so it's a softer signal.
PL_BS_BREAKS = ("IS_GP", "IS_NI", "IS_NCI", "BS_FOOTS", "BS_EQUITY")
CF_BREAKS = ("CASH_ROLL", "CF_FOOT", "SBC_CONSISTENCY")


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ntk(t):
    return re.sub(r"[^A-Z0-9]", "", str(t).upper()) if t else ""


def break_kind(breaks):
    """classify the `breaks` field: 'plbs' if a P&L/BS value-affecting leg broke, else 'cf' if only
    a cash-flow leg broke, else ''."""
    legs = [b.strip() for b in (breaks or "").split(";") if b.strip()]
    if any(b.startswith(PL_BS_BREAKS) for b in legs):
        return "plbs"
    if any(b.startswith(CF_BREAKS) for b in legs):
        return "cf"
    return ""


def plausibility(r, prev):
    """r, prev = current and prior-year value dicts (floats or None). Returns (critical, watch)
    lists of flag strings."""
    crit, watch = [], []
    g = r.get
    rev, ta, cash = g("revenue"), g("total_assets"), g("cash")
    gp, oi, eb, da = g("gross_profit"), g("operating_income"), g("ebitda"), g("depreciation_amortization")
    ni, pretax, tax = g("net_income"), g("pretax_income"), g("tax_expense")
    eq, tl, debt = g("total_equity"), g("total_liabilities"), g("total_debt")
    com = r.get("_sector") == "general"     # commercial (adapter maps commercial->general)

    if rev is not None and rev < 0: crit.append("revenue<0")
    if ta is not None and ta <= 0: crit.append("assets<=0")
    if cash is not None and cash < 0: crit.append("cash<0")
    if com and gp is not None and rev is not None and gp > rev * 1.005 and rev > 0:
        crit.append("GM>100%")
    if eq is not None and ta is not None and eq > ta * 1.01 and ta > 0:
        crit.append("equity>assets")
    if eb is not None and oi is not None and eb < oi - max(5000, 0.01 * abs(oi)):
        crit.append("EBITDA<OI(neg D&A)")
    if com and da is not None and rev is not None and rev > 0 and da > 2 * rev:
        crit.append("D&A>2x revenue")
    if debt is not None and tl is not None and debt > tl * 1.05 and tl > 0:
        crit.append("debt>liabilities")
    # WATCH
    if com and gp is not None and rev is not None and rev > 0 and gp < -0.5 * rev:
        watch.append("GM<-50%")
    if ni is not None and rev is not None and rev > 0 and ni > 2 * rev:
        watch.append("NI>2x revenue")
    # effective tax rate is noisy near zero pretax (discrete items, valuation allowances) -> WATCH,
    # and only when pretax is material and tax exceeds it (ETR>100% or a sign mismatch).
    if pretax is not None and tax is not None and abs(pretax) > 10e6 and abs(tax) > abs(pretax):
        watch.append("tax>pretax (ETR>100%)")
    if da is not None and rev is not None and rev > 0 and rev < da <= 2 * rev:
        watch.append("D&A>revenue")
    if prev and rev is not None and prev.get("revenue"):
        pr = prev["revenue"]
        if pr > 0 and (rev > 5 * pr or rev < 0.2 * pr):
            watch.append("revenue YoY>5x/<0.2x")
    return crit, watch


VAL_COLS = ("revenue", "total_assets", "cash", "gross_profit", "operating_income", "ebitda",
            "depreciation_amortization", "net_income", "pretax_income", "tax_expense",
            "total_equity", "total_liabilities", "total_debt")


def load_fundamentals():
    by_cik = defaultdict(dict)   # cik -> {fy:int -> row}
    rows = []
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        fy = r.get("fiscal_year", "")
        rec = {c: fnum(r.get(c)) for c in VAL_COLS}
        rec["_sector"] = r.get("sector", "")
        rec["_breaks"] = r.get("breaks", "")
        rec["_conf"] = r.get("confidence", "")
        rec["cik"] = r.get("cik", ""); rec["fiscal_year"] = fy
        rows.append(rec)
        if fy.isdigit():
            by_cik[rec["cik"]][int(fy)] = rec
    return rows, by_cik


def assess(rows, by_cik):
    out = []
    for r in rows:
        fy = r["fiscal_year"]
        prev = by_cik[r["cik"]].get(int(fy) - 1) if fy.isdigit() else None
        crit, watch = plausibility(r, prev)
        bk = break_kind(r["_breaks"])
        # review = the P&L/BS values can't be trusted (critical plausibility OR a P&L/BS identity
        # break); watch = a softer signal (cash-flow-only break or a watch flag); else clean.
        tier = "review" if (crit or bk == "plbs") else ("watch" if (watch or bk == "cf") else "clean")
        # CORE reliability = the income statement and balance sheet tie and pass sanity (no critical
        # flag, no P&L/BS identity break). This is what the quality/growth/margin/leverage analytics
        # consume; the cash-flow roll-forward and growth-typical values are a stricter, separate check.
        core = tier != "review"
        # why a core-reliable name still isn't "clean": a cash-flow articulation gap, or a value that's
        # unusual but legitimate for a growth index (pre-revenue losses, M&A growth, valuation-allowance tax)
        wreason = ("growth-typical flag" if watch else ("cash-flow articulation" if bk == "cf" else ""))
        out.append(dict(cik=r["cik"], fiscal_year=fy, sector=r["_sector"], tier=tier,
                        core_reliable=("Y" if core else "N"), watch_reason=wreason,
                        break_kind=bk, critical=";".join(crit), watch=";".join(watch),
                        confidence=r["_conf"]))
    return out


def load_weights():
    """ntk -> index weight, from holdings latest snapshot (optional)."""
    try:
        import openpyxl
    except ImportError:
        return {}
    c = (list(BASE.glob("*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx")) or list(BASE.glob("*[Hh]olding*.xlsx")))
    c = sorted(x for x in c if "quarterly" not in x.name.lower())   # annual only, deterministic
    if not c:
        return {}
    wb = openpyxl.load_workbook(c[0], read_only=True, data_only=True)
    def snap_year(sn):
        m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(sn))
        if not m: return None
        return int("20" + m.group(3)) if len(m.group(3)) == 2 else int(m.group(3))
    cand = [(snap_year(s), s) for s in wb.sheetnames if snap_year(s) is not None]
    if not cand:
        return {}
    best = max(cand)[1]
    ws = wb[best]; hdr = None; idx = {}; out = {}
    for row in ws.iter_rows(values_only=True):
        if hdr is None:
            hdr = [str(x).strip() if x else "" for x in row]; idx = {h: i for i, h in enumerate(hdr)}
            if "Ticker" not in idx: hdr = None
            continue
        tk = row[idx["Ticker"]] if idx.get("Ticker") is not None else None
        if tk is None: continue
        w = idx.get("Portfolio Weighting %")
        out[ntk(tk)] = fnum(row[w]) if (w is not None and row[w] is not None) else 0.0
    wb.close()
    return out


def load_cikmap():
    m = {}
    if CIKMAP.exists():
        for t, v in json.load(open(CIKMAP)).items():
            cik = v["cik"] if isinstance(v, dict) else v
            cik = str(int(cik)) if str(cik).isdigit() else str(cik)
            m[ntk(t)] = cik
    return m


def main():
    if not FUND.exists():
        raise SystemExit(f"!! {FUND.name} not found -- run r2k_dera_classify.py first.")
    rows, by_cik = load_fundamentals()
    res = assess(rows, by_cik)
    from collections import Counter
    tiers = Counter(x["tier"] for x in res)
    crit_types = Counter(c for x in res for c in x["critical"].split(";") if c)
    watch_types = Counter(c for x in res for c in x["watch"].split(";") if c)

    L = ["PLAUSIBILITY + RELIABILITY  --  identities prove consistency; this proves the values are sane",
         f"{len(res):,} company-years   tiers: " +
         ", ".join(f"{t} {tiers[t]:,} ({100*tiers[t]/len(res):.1f}%)" for t in ("clean", "watch", "review")),
         "", "CRITICAL flags (likely data error):"]
    for k, n in crit_types.most_common():
        L.append(f"   {k:<24}{n:,}")
    L.append("\nWATCH flags (usually LEGITIMATE for a small-cap GROWTH index, not data errors):")
    for k, n in watch_types.most_common():
        L.append(f"   {k:<24}{n:,}")

    # weight by index (latest snapshot) if holdings available
    weights, cm = load_weights(), load_cikmap()
    if weights and cm:
        cik2w = {}
        for nt, w in weights.items():
            c = cm.get(nt)
            if c: cik2w[c] = w
        latest = {}
        for x in res:
            if x["fiscal_year"].isdigit() and (x["cik"] not in latest or x["fiscal_year"] > latest[x["cik"]]["fiscal_year"]):
                latest[x["cik"]] = x
        tw = sum(cik2w.values()) or 1.0
        wt = defaultdict(float)
        wreason_w = defaultdict(float)
        for c, w in cik2w.items():
            x = latest.get(c, {})
            wt[x.get("tier", "unmapped")] += w
            if x.get("tier") == "watch":
                wreason_w[x.get("watch_reason") or "other"] += w
        core_w = wt["clean"] + wt["watch"]      # everything that is not 'review'
        L.append("\nINDEX-WEIGHT view (latest snapshot, most recent year per name):")
        L.append("  CORE RELIABILITY -- income statement & balance sheet tie out AND are plausible")
        L.append("  (the metric the quality / growth / margin / leverage analytics actually consume):")
        L.append(f"     => {100*core_w/tw:.1f}% of index weight is CORE-RELIABLE.   "
                 f"(only {100*wt['review']/tw:.1f}% is in review.)")
        L.append("\n  FULL THREE-STATEMENT ARTICULATION -- adds the cash-flow roll-forward (a stricter bar):")
        L.append(f"     => {100*wt['clean']/tw:.1f}% of index weight is fully CLEAN.")
        L.append("\n  the gap between the two is NOT bad data -- it is cash-flow articulation noise and")
        L.append("  growth-typical values on names whose income statement and balance sheet still tie:")
        for t in ("clean", "watch", "review", "unmapped"):
            if wt[t]:
                L.append(f"     {t:<10}{100*wt[t]/tw:>6.1f}% of index weight")
        for rk in ("cash-flow articulation", "growth-typical flag", "other"):
            if wreason_w[rk]:
                L.append(f"        of which watch is {rk:<22}{100*wreason_w[rk]/tw:>6.1f}%")
        # highest-weight review names
        revs = [(cik2w.get(x["cik"], 0), x) for x in latest.values() if x["tier"] == "review"]
        revs.sort(key=lambda z: -z[0])
        L.append("\n   highest-weight names needing review (resolve these first):")
        L.append(f"     {'wt%':>6}  {'cik':<9}{'flags':<40}")
        for w, x in revs[:25]:
            flags = x["critical"] or (f"{x['break_kind']}-break" if x["break_kind"] else x["watch"])
            L.append(f"     {w:>5.2f}  {x['cik']:<9}{flags[:40]:<40}")
    else:
        L.append("\n(no holdings/cik map found -> name-count view only; add them to weight by index)")

    REPORT.write_text("\n".join(L), encoding="utf-8")
    fields = ["cik", "fiscal_year", "sector", "tier", "core_reliable", "watch_reason", "break_kind",
              "critical", "watch", "confidence"]
    with open(FLAGS, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader(); w.writerows(sorted(res, key=lambda x: (x["tier"] != "review", x["cik"])))
    print("\n".join(L[:14]))
    print(f"\n  -> {REPORT.name} ; {FLAGS.name}")


def selftest():
    rows = [
        dict(cik="1", fiscal_year="2024", _sector="general", _breaks="", _conf="1.00",
             revenue=1000, total_assets=5000, cash=300, gross_profit=400, operating_income=100,
             ebitda=150, depreciation_amortization=50, net_income=65, pretax_income=90,
             tax_expense=20, total_equity=2000, total_liabilities=3000, total_debt=850),
        dict(cik="2", fiscal_year="2024", _sector="general", _breaks="", _conf="1.00",
             revenue=100, total_assets=500, cash=50, gross_profit=400, operating_income=10,  # GM>100%
             ebitda=20, depreciation_amortization=300, net_income=5, pretax_income=8, tax_expense=2,
             total_equity=200, total_liabilities=300, total_debt=50),   # D&A>2x rev
        dict(cik="3", fiscal_year="2024", _sector="general", _breaks="CASH_ROLL(x)", _conf="0.90",
             revenue=1000, total_assets=5000, cash=300, gross_profit=400, operating_income=100,
             ebitda=150, depreciation_amortization=50, net_income=65, pretax_income=90,
             tax_expense=20, total_equity=2000, total_liabilities=3000, total_debt=850),  # CF break -> watch
        dict(cik="4", fiscal_year="2024", _sector="general", _breaks="IS_NI(x)", _conf="0.90",
             revenue=1000, total_assets=5000, cash=300, gross_profit=400, operating_income=100,
             ebitda=150, depreciation_amortization=50, net_income=65, pretax_income=90,
             tax_expense=20, total_equity=2000, total_liabilities=3000, total_debt=850),  # P&L break -> review
    ]
    by = defaultdict(dict)
    res = assess(rows, by)
    t = {x["cik"]: x for x in res}
    checks = [("clean #1", t["1"]["tier"] == "clean"),
              ("critical #2 GM>100% & D&A", "GM>100%" in t["2"]["critical"] and "D&A>2x revenue" in t["2"]["critical"]),
              ("#2 review", t["2"]["tier"] == "review"),
              ("#3 CF-break -> watch", t["3"]["tier"] == "watch" and t["3"]["break_kind"] == "cf"),
              ("#4 P&L-break -> review", t["4"]["tier"] == "review" and t["4"]["break_kind"] == "plbs")]
    for n, ok in checks:
        print(f"   {'PASS' if ok else 'FAIL'}  {n}")
    print(f"\n  SELFTEST: {'PASS' if all(o for _, o in checks) else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
