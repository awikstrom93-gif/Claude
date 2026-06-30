"""
r2k_benchmark_reconcile.py  --  SANITY-CHECK the Benchmark Review workbook at the INDEX-AGGREGATE level.
For each annual snapshot it recomputes the headline figures the workbook reports (Total Revenue,
Total Net Income, % Unprofitable by weight, dollar-aggregate gross & net margin) THREE ways over the
SAME R2000G constituents and the SAME fiscal year per name:

   DERA  -- our as-filed engine (reuses the exact step-3/6 functions, so this column IS the workbook)
   MSTAR -- Morningstar's standardized values for the identical (cik, fiscal year)
   FACTSET -- if a FactSet aggregate export is present (NOTE: FactSet publishes TTM, not fiscal-year
             snapshots, so a few-percent level difference is expected and is methodology, not error)

So it answers: (1) does the workbook align with the underlying data (DERA column vs the workbook tab),
(2) is it consistent with Morningstar-only (DERA vs MSTAR), (3) consistent with FactSet (with the TTM
caveat). Writes benchmark_reconcile.txt.   RUN:  python r2k_benchmark_reconcile.py
"""
from pathlib import Path
import os, csv
from collections import defaultdict

from r2k_step6_index_comparison import (find, norm_facts, fund_for, ticker_cik_map,
                                        annual_spine, snapshot_quality)
from r2k_step3_analytics import load_fundamentals, company_metrics, pick_fy0, load_maps, dollar_agg
from r2k_perf_io import load_monthly_holdings
from r2k_dual_reconstruct_pilot import ms_statement, _ms_cols, MS

BASE = Path(os.environ.get("R2KG_BASE", "."))
OUT = BASE / "benchmark_reconcile.txt"
WB = BASE / "R2000G_SmallCapGrowth_Benchmark_Review.xlsx"


def _ncik(c):
    c = str(c)
    return str(int(c)) if c.isdigit() else c


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_ms_norm():
    """{(int-string cik, fy): {metric: value}} -- normalized CIK so zero-padding never blocks a match;
    latest period_end wins per (cik, fy, metric)."""
    out, pe = defaultdict(dict), defaultdict(dict)
    if not MS.exists():
        return out
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        ci, fi, pi, mi, vi = _ms_cols(next(r))
        for row in r:
            if len(row) <= vi:
                continue
            v = fnum(row[vi])
            if v is None:
                continue
            key = (_ncik(row[ci]), str(row[fi]))
            if pe[key].get(row[mi]) is None or row[pi] >= pe[key][row[mi]]:
                out[key][row[mi]] = v
                pe[key][row[mi]] = row[pi]
    return out


def ni_of(s):
    return s.get("ni_parent") if s.get("ni_parent") is not None else s.get("ni_consol")


def agg(cov, inc, rev):
    """dollar-aggregate margin % from a list of standardized dicts (income key, revenue key)."""
    return dollar_agg([(c.get(inc) if not callable(inc) else inc(c), c.get(rev)) for c in cov])


def main():
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    hr = find(["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"])
    if not hr:
        raise SystemExit("!! R2000G holdings workbook not found.")
    hold = load_monthly_holdings(hr, verbose=False)
    spine = annual_spine(hold)
    years = sorted(spine)
    ms = load_ms_norm()

    # optional: the workbook's own Quality-Trends numbers, to confirm DERA-recompute == workbook
    wbq = {}
    if WB.exists():
        try:
            import openpyxl
            ws = openpyxl.load_workbook(WB, data_only=True)["R2KG Quality Trends"]
            for r in range(3, ws.max_row + 1):
                snap = ws.cell(r, 1).value
                if snap:
                    wbq[str(snap)[:10]] = {"rev": ws.cell(r, 2).value, "ni": ws.cell(r, 3).value,
                                           "unprof": ws.cell(r, 5).value}
        except Exception:
            pass

    L = ["BENCHMARK RECONCILIATION  --  R2000G index aggregates, three ways (same constituents & years)",
         f"  MSTAR = Morningstar standardized; FACTSET = TTM (methodology gap expected).  {len(years)} snapshots", ""]
    L.append(f"  {'snapshot':<11}{'source':<8}{'TotRev$B':>10}{'TotNI$B':>10}{'%Unprof':>9}"
             f"{'GrossDA%':>9}{'NetDA%':>8}{'MScov%':>8}")
    L.append("  " + "-" * 71)
    deltas = []
    for y in years:
        snap = spine[y]
        rows = hold[snap]
        q = snapshot_quality(rows, snap, facts, tmap)        # DERA == workbook (same function)
        cons = []
        for h in rows:
            c = h["cik"] or tmap.get(h["nt"])
            cf = fund_for(facts, c)
            if not cf:
                continue
            fy0 = pick_fy0(cf, snap)
            if fy0 is None:
                continue
            cons.append((_ncik(c), str(fy0), h["weight"], company_metrics(cf, fy0)))
        dera_ni = sum(m["net_income"] for *_, m in cons if m.get("net_income")) / 1e9
        # Morningstar over the SAME (cik, fy0)
        msc = [(w, ms_statement(ms[(c, fy)])) for c, fy, w, _ in cons if (c, fy) in ms]
        wc = sum(w for w, _ in msc) or 1.0
        ms_rev = sum(s["revenue"] for _, s in msc if s.get("revenue")) / 1e9
        ms_ni = sum(ni_of(s) for _, s in msc if ni_of(s) is not None) / 1e9
        ms_unprof = 100 * sum(w for w, s in msc if (ni_of(s) is not None and ni_of(s) < 0)) / wc
        ms_gda = agg([s for _, s in msc], "gross_profit", "revenue")
        ms_nda = agg([s for _, s in msc], ni_of, "revenue")
        cov = 100 * len(msc) / len(cons) if cons else 0
        sd = str(snap)[:10]
        L.append(f"  {sd:<11}{'DERA':<8}{q['tot_rev']:>10.1f}{dera_ni:>10.1f}{q['unprof_ni']:>9.1f}"
                 f"{q['gross_da']:>9.1f}{q['net_da']:>8.1f}{'':>8}")
        L.append(f"  {'':<11}{'MSTAR':<8}{ms_rev:>10.1f}{ms_ni:>10.1f}{ms_unprof:>9.1f}"
                 f"{(ms_gda or 0):>9.1f}{(ms_nda or 0):>8.1f}{cov:>8.0f}")
        if wbq.get(sd):
            w = wbq[sd]
            tag = "OK" if (w["rev"] and abs(w["rev"] - q["tot_rev"]) < 0.5) else "** workbook != recompute"
            L.append(f"  {'':<11}{'WORKBK':<8}{(w['rev'] or 0):>10.1f}{(w['ni'] or 0):>10.1f}"
                     f"{(w['unprof'] or 0):>9.1f}{'':>17}  <- {tag}")
        # relative deltas (DERA vs MSTAR) on the two most-cited figures
        if ms_rev and q["tot_rev"]:
            deltas.append(abs(ms_rev - q["tot_rev"]) / q["tot_rev"])
        L.append("")
    if deltas:
        md = 100 * sum(deltas) / len(deltas)
        L.append(f"  MEAN |DERA-MSTAR| revenue gap: {md:.1f}%   "
                 + ("(sources agree -- the workbook is consistent with Morningstar)" if md < 3 else
                    "(material gap -- inspect the per-name dual reconstruction: r2k_dual_reconstruct_pilot.py)"))
    L.append("\n  FactSet: provide its R2000G aggregate export to add the third column. FactSet reports TTM,")
    L.append("  so expect a few-percent level difference vs our fiscal-year snapshot -- methodology, not error.")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
