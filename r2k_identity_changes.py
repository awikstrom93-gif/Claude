"""
r2k_identity_changes.py  --  show EXACTLY which constituent identities the point-in-time freshness
rule changes versus pure base-first, so the change set is auditable. For every annual R2000G holding
where the new resolve_identity picks a different CIK than base-first, it prints the ticker/year/weight,
the old vs new entity (as-filed DERA name), and each one's revenue/NI -- plus the net impact on the
index revenue/NI total. Every row should be a defunct-entity fix (e.g. TRAK DealerTrack->Park City,
CZR Caesars->Eldorado in 2021), never a healthy name flipping.

RUN:  python r2k_identity_changes.py
"""
import csv

from r2k_universe import (BASE, find_annual, find_holdings, load_maps, ticker_cik_map, norm_facts,
                          fund_for, canon_cik, resolve_identity, annual_spine)
from r2k_step3_analytics import load_fundamentals, pick_fy0
from r2k_perf_io import load_monthly_holdings

INDEX = BASE / "dera_filing_index.csv"
OUT = BASE / "identity_changes.txt"


def _ck(c):
    s = str(c)
    return str(int(s)) if s.isdigit() else s


def load_names():
    out = {}
    if INDEX.exists():
        for r in csv.DictReader(open(INDEX, encoding="utf-8")):
            fy = str(r.get("fy", ""))
            if fy.isdigit():
                out[(_ck(r.get("cik", "")), fy)] = r.get("name", "")
    return out


def _revni(nfacts, cik, snap):
    cf = fund_for(nfacts, cik)
    if not cf:
        return None, None, None
    fy0 = pick_fy0(cf, snap)
    if fy0 is None:
        return None, None, None
    r0 = cf.get(fy0, {})
    return r0.get("revenue"), r0.get("net_income"), fy0


def main():
    nfacts = norm_facts(load_fundamentals())
    base, temporal = load_maps()
    tmap = ticker_cik_map(base, temporal)
    names = load_names()
    hold = load_monthly_holdings(find_annual("R2KG") or find_holdings(), verbose=False)
    spine = annual_spine(hold)

    rows, d_rev, d_ni = [], 0.0, 0.0
    for year in sorted(spine):
        snap = spine[year]
        skey = str(snap)[:10]
        for h in hold[snap]:
            base_cik = canon_cik(tmap.get(h["nt"]))
            new_cik, _cf, _r = resolve_identity(h, tmap, nfacts, snap_dt=snap, temporal=temporal, skey=skey)
            if new_cik == base_cik or new_cik is None:
                continue
            brev, bni, bfy = _revni(nfacts, base_cik, snap)
            nrev, nni, nfy = _revni(nfacts, new_cik, snap)
            d_rev += ((nrev or 0) - (brev or 0)) / 1e9
            d_ni += ((nni or 0) - (bni or 0)) / 1e9
            rows.append({"w": h.get("weight", 0.0), "ticker": h.get("ticker", ""), "year": year,
                         "bcik": base_cik, "bname": names.get((base_cik, str(bfy)), "")[:24],
                         "brev": brev, "bni": bni, "bfy": bfy,
                         "ncik": new_cik, "nname": names.get((new_cik, str(nfy)), "")[:24],
                         "nrev": nrev, "nni": nni, "nfy": nfy})
    rows.sort(key=lambda x: -x["w"])

    def m(x):
        return f"{x/1e6:,.0f}" if x is not None else "-"

    L = [f"IDENTITY CHANGES  --  point-in-time freshness vs pure base-first  ({len(rows)} constituent-years)",
         f"  net impact on the index: revenue {d_rev:+.1f} $B,  net income {d_ni:+.1f} $B  (summed across all years)", ""]
    for r in rows:
        L.append(f"  {r['ticker']:<7} {r['year']}  wt {r['w']:.3f}%")
        L.append(f"      base {r['bcik']:<9} FY{r['bfy']}  rev={m(r['brev']):>9} ni={m(r['bni']):>8}  {r['bname']}")
        L.append(f"      NEW  {r['ncik']:<9} FY{r['nfy']}  rev={m(r['nrev']):>9} ni={m(r['nni']):>8}  {r['nname']}")
    L.append("")
    L.append("  Every row should be a DEFUNCT base entity replaced by the still-filing current issuer")
    L.append("  (the ticker's point-in-time owner). A healthy name flipping would be a regression -- flag it.")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
