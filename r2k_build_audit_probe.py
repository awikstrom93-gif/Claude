"""
r2k_build_audit_probe.py  --  settle the data-dependent questions from the build audit.

Q1  Does the R2000G holdings file actually carry a populated CIK column?
    (step5's code comment claims it does not -- which would make the cik_file fix to step3 inert.)
Q2  Where do the CIK maps disagree?  For every HELD ticker, compare three resolutions:
      file   = the holdings row's own CIK column        (what load_monthly_holdings uses first)
      base   = security_cik_map.json  (base-first tmap)  (what steps 5/6/8/9 fall back to)
      temp   = temporal_cik_map.json[snapshot][ticker]   (what legacy step3 used FIRST)
    A point-in-time (temporal) map should be the MOST correct for reused tickers; if it instead
    holds modern CIKs for historical snapshots (the CZR/1590895 signature), that's a map bug.
Q3  Scope: across all snapshots, how many held tickers does temporal-first disagree with
    base-first on, and what is their fundamentals NI impact -- i.e. how much of the index does
    the legacy step3 resolution actually move.

Writes build_audit_probe.txt.   RUN:  python r2k_build_audit_probe.py
"""
from pathlib import Path
from datetime import date
from collections import defaultdict

from r2k_step3_analytics import (load_fundamentals, load_maps, pick_fy0, company_metrics,
                                  find_holdings)
from r2k_step6_index_comparison import norm_facts, fund_for, ticker_cik_map, annual_spine
from r2k_perf_io import load_monthly_holdings, ntk


def _cik_in_facts(facts, cand):
    """Whichever string form of a candidate CIK is a key in facts, else None.
    (Defined locally so the probe runs against any version of step3.)"""
    if not cand:
        return None
    forms = (str(cand), str(cand).zfill(10))
    if str(cand).isdigit():
        forms = forms + (str(int(cand)),)
    for form in forms:
        if form in facts:
            return form
    return None


def temp_first(temporal, base, skey, raw, nt, facts):
    cand = (temporal.get(skey, {}).get(raw) or temporal.get(skey, {}).get(raw.upper()) or base.get(nt))
    return _cik_in_facts(facts, cand)


def base_first(base, temporal, tmap, nt, facts):
    return _cik_in_facts(facts, tmap.get(nt))


def ni_at(facts, cik, snap_dt):
    cf = fund_for(facts, cik) if cik else None
    if not cf:
        return None, None
    fy0 = pick_fy0(cf, snap_dt)
    if fy0 is None:
        return None, None
    return company_metrics(cf, fy0).get("net_income"), fy0


def main():
    facts = load_fundamentals()        # raw-keyed (step3 view)
    nfacts = norm_facts(facts)         # int-string keyed (modern view)
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    hold = load_monthly_holdings(find_holdings(), verbose=False)
    spine = annual_spine(hold)
    years = sorted(spine)

    L = ["BUILD AUDIT PROBE  --  CIK-map divergence between legacy step3 (temporal-first) and the rest", ""]

    # ---- Q1: is the CIK column populated in the holdings file? ----
    L.append("  Q1  Holdings-file CIK column population (per annual snapshot):")
    L.append(f"      {'snapshot':<12}{'rows':>6}{'with CIK':>10}{'%':>7}")
    any_cik = False
    for y in years:
        rows = hold[spine[y]]
        n = len(rows); nc = sum(1 for h in rows if h.get("cik"))
        any_cik = any_cik or nc > 0
        L.append(f"      {str(spine[y])[:10]:<12}{n:>6}{nc:>10}{(100*nc/n if n else 0):>7.0f}")
    L.append(f"      => holdings file {'DOES' if any_cik else 'does NOT'} carry CIKs."
             f"  {'cik_file fix to step3 is EFFECTIVE.' if any_cik else 'cik_file fix is INERT -- must fix the fallback instead.'}")
    L.append("")

    # ---- Q2 / Q3: temporal-first vs base-first disagreements among held tickers ----
    L.append("  Q2/Q3  Tickers where legacy (temporal-first) resolves to a DIFFERENT CIK than base-first,")
    L.append("         with the fundamentals NI each path would feed into the index (focus year + scope):")
    scope = defaultdict(lambda: [0, 0.0])   # year -> [n_disagree, abs NI moved $M]
    focus_rows = []
    for y in years:
        snap = spine[y]; snap_dt = date(snap.year, snap.month, snap.day)
        skey = f"{y:04d}-04-30"
        seen = set()
        for h in hold[snap]:
            nt = h["nt"]
            if nt in seen:
                continue
            seen.add(nt)
            raw = h["ticker"]
            t_cik = temp_first(temporal, base, skey, raw, nt, facts)   # uses raw-keyed facts (step3)
            b_cik = base_first(base, temporal, tmap, nt, facts)
            if t_cik == b_cik:
                continue
            ni_t, fy_t = ni_at(facts, t_cik, snap_dt)
            ni_b, fy_b = ni_at(nfacts, b_cik, snap_dt)
            d = ((ni_b or 0) - (ni_t or 0)) / 1e6
            scope[y][0] += 1
            scope[y][1] += abs(d)
            if y == 2016:
                focus_rows.append((d, raw, t_cik, fy_t, ni_t, b_cik, fy_b, ni_b, h["weight"]))
    focus_rows.sort(key=lambda r: -abs(r[0]))
    L.append("")
    L.append(f"      FOCUS 2016 (ranked by |NI move|, $M):  {'tick':<8}{'wt%':>6}  "
             f"{'temporal cik/fy/NI$M':<26}{'base cik/fy/NI$M':<26}{'dNI$M':>9}")
    for d, raw, tc, fyt, nit, bc, fyb, nib, wt in focus_rows[:25]:
        a = f"{tc}/{fyt}/{(nit or 0)/1e6:,.0f}" if tc else "-"
        b = f"{bc}/{fyb}/{(nib or 0)/1e6:,.0f}" if bc else "-"
        L.append(f"      {'':<41}{raw:<8}{wt:>6.2f}  {a:<26}{b:<26}{d:>9,.0f}")
    L.append("")
    L.append(f"      SCOPE across all snapshots:  {'year':<6}{'#disagree':>10}{'|NI moved|$M':>14}")
    for y in years:
        n, mv = scope[y]
        L.append(f"      {'':<27}{y:<6}{n:>10}{mv:>14,.0f}")
    tot_n = sum(v[0] for v in scope.values())
    L.append(f"      {'':<27}{'TOTAL':<6}{tot_n:>10}{sum(v[1] for v in scope.values()):>14,.0f}")
    L.append("")
    L.append("  READ:  if 'with CIK' is ~0, step3 must fall back base-FIRST (not temporal-first) to match")
    L.append("         the other steps. If disagreements are many, temporal_cik_map.json itself needs a")
    L.append("         rebuild (it appears to hold present-day CIKs for historical snapshots).")

    out = "\n".join(L)
    print(out)
    Path("build_audit_probe.txt").write_text(out, encoding="utf-8")
    print("\n  -> build_audit_probe.txt")


if __name__ == "__main__":
    main()
