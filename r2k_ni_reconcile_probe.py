"""
r2k_ni_reconcile_probe.py  --  why does step3's 'Index Quality Trends' tab disagree with the
step6/benchmark index aggregate?  (e.g. 2016 Total NI: step3 6.5  vs  benchmark 13.2)

Both pull constituents from the SAME holdings workbook, but build the snapshot two different ways:
   STEP3  = load_membership()  (one sheet per year)  + snap date forced to date(yr,4,30)
            + resolve_cik() via the POINT-IN-TIME temporal map
   BENCH  = load_monthly_holdings() + annual_spine() (actual nearest-April holdings date)
            + cik straight from the holdings row, else the STATIC ticker->cik map
So a name can (a) be in one universe and not the other, (b) resolve to a different CIK, or
(c) resolve to the same CIK but a different fiscal year (fy0) because the snap DATE differs.
Any of those moves Total NI a lot while barely moving revenue.

This prints, per year, both aggregates side by side, and for a focus year (default 2016) the
per-name diffs ranked by |NI difference| so you can see exactly which names drive the gap.

RUN:  python r2k_ni_reconcile_probe.py            (focus 2016)
      python r2k_ni_reconcile_probe.py 2020       (focus another year)
"""
import sys
from datetime import date

from r2k_step3_analytics import (load_fundamentals, load_membership, load_maps, pick_fy0,
                                  company_metrics, resolve_cik)
from r2k_step6_index_comparison import (norm_facts, fund_for, ticker_cik_map, annual_spine)
from r2k_perf_io import load_monthly_holdings
from r2k_step3_analytics import find_holdings  # same workbook both paths read


def _ni(cf, fy0):
    if cf is None or fy0 is None:
        return None, None, None
    m = company_metrics(cf, fy0)
    return m.get("net_income"), m.get("revenue"), fy0


def step3_view(year, facts, snaps, base, temporal):
    """{nt: (cik, fy0, ni, rev, weight)} the way step3 builds the snapshot."""
    out = {}
    snap_dt = date(year, 4, 30)
    skey = f"{year:04d}-04-30"
    for mem in snaps.get(year, []):
        cik = resolve_cik(facts, base, temporal, mem["ticker"], mem["nt"], skey, mem.get("cik_file"))
        if not cik:
            continue
        fy0 = pick_fy0(facts[cik], snap_dt)
        if fy0 is None:
            continue
        ni, rev, _ = _ni(facts[cik], fy0)
        out[mem["nt"]] = (cik, fy0, ni, rev, mem["weight"])
    return out, snap_dt


def bench_view(year, nfacts, tmap, hold, spine):
    """{nt: (cik, fy0, ni, rev, weight)} the way step6/benchmark builds the snapshot."""
    out = {}
    snap = spine.get(year)
    if snap is None:
        return out, None
    for h in hold[snap]:
        c = h["cik"] or tmap.get(h["nt"])
        cf = fund_for(nfacts, c)
        if not cf:
            continue
        fy0 = pick_fy0(cf, snap)
        if fy0 is None:
            continue
        ni, rev, _ = _ni(cf, fy0)
        # normalize cik display the way fund_for keyed it
        try:
            ck = str(int(c))
        except (TypeError, ValueError):
            ck = str(c)
        out[h["nt"]] = (ck, fy0, ni, rev, h["weight"])
    return out, snap


def _tot(view):
    rev = sum(v[3] for v in view.values() if v[3]) / 1e9
    ni = sum(v[2] for v in view.values() if v[2] is not None) / 1e9
    return rev, ni, len(view)


def main():
    focus = int(sys.argv[1]) if len(sys.argv) > 1 else 2016
    facts = load_fundamentals()
    nfacts = norm_facts(facts)
    snaps = load_membership()
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    hold = load_monthly_holdings(find_holdings(), verbose=False)
    spine = annual_spine(hold)
    years = sorted(set(snaps) & set(spine))

    L = ["NI RECONCILE  --  step3 (load_membership, 4/30, temporal cik) vs benchmark (holdings spine, static cik)", ""]
    L.append(f"  {'year':<6}{'s3date':<12}{'bdate':<12}{'s3 n':>6}{'b n':>6}"
             f"{'s3 rev$B':>10}{'b rev$B':>10}{'s3 NI$B':>9}{'b NI$B':>9}{'NIgap':>8}")
    L.append("  " + "-" * 92)
    views = {}
    for y in years:
        s3, s3d = step3_view(y, facts, snaps, base, temporal)
        bv, bd = bench_view(y, nfacts, tmap, hold, spine)
        views[y] = (s3, bv)
        r3, n3, c3 = _tot(s3)
        rb, nb, cb = _tot(bv)
        L.append(f"  {y:<6}{str(s3d):<12}{str(bd)[:10]:<12}{c3:>6}{cb:>6}"
                 f"{r3:>10.1f}{rb:>10.1f}{n3:>9.1f}{nb:>9.1f}{(nb-n3):>8.1f}")

    # ---- focus-year per-name decomposition ----
    s3, bv = views[focus]
    L += ["", f"  FOCUS {focus}: per-name differences ranked by |NI impact|  (NI in $M)", ""]
    keys = set(s3) | set(bv)
    rows = []
    for k in keys:
        a, b = s3.get(k), bv.get(k)
        ni_a = (a[2] or 0) if a else 0
        ni_b = (b[2] or 0) if b else 0
        d = (ni_b - ni_a) / 1e6
        if abs(d) < 1:   # ignore sub-$1M noise
            continue
        if a and not b:
            why = "step3-ONLY"
        elif b and not a:
            why = "bench-ONLY"
        elif a[0] != b[0]:
            why = "diff CIK"
        elif a[1] != b[1]:
            why = "diff fy0"
        else:
            why = "diff value"   # same cik & fy0 but metrics differ -> shouldn't happen
        rows.append((d, k, why, a, b))
    rows.sort(key=lambda t: -abs(t[0]))
    L.append(f"  {'ticker':<9}{'why':<12}{'s3 cik/fy0/NI$M':<26}{'bench cik/fy0/NI$M':<26}{'dNI$M':>9}")
    L.append("  " + "-" * 90)

    def fmt(x):
        if not x:
            return "-"
        return f"{x[0]}/{x[1]}/{(x[2] or 0)/1e6:,.0f}"
    tot_by = {}
    for d, k, why, a, b in rows[:60]:
        L.append(f"  {k:<9}{why:<12}{fmt(a):<26}{fmt(b):<26}{d:>9,.0f}")
    for d, k, why, a, b in rows:
        tot_by[why] = tot_by.get(why, 0) + d
    L += ["", "  NI gap decomposition by cause ($M, bench - step3):"]
    for why, v in sorted(tot_by.items(), key=lambda t: -abs(t[1])):
        L.append(f"     {why:<14}{v:>12,.0f}")
    total = sum(tot_by.values())
    L.append(f"     {'TOTAL':<14}{total:>12,.0f}   (= bench NI - step3 NI for {focus}, in $M)")

    out = "\n".join(L)
    print(out)
    from pathlib import Path
    Path("ni_reconcile_probe.txt").write_text(out, encoding="utf-8")
    print("\n  -> ni_reconcile_probe.txt")


if __name__ == "__main__":
    main()
