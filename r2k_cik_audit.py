"""
r2k_cik_audit.py  --  adjudicate the ticker-reuse identity problem so it can be FIXED point-in-time.
For every (ticker, year) where the base security map and the temporal map disagree on the CIK, it lays
out the EVIDENCE side by side -- each candidate CIK's as-filed DERA name that fiscal year, its
revenue/NI, whether it has fundamentals at all, and what ticker/name Morningstar attaches to it -- so
we resolve each from facts, not guesswork.  Ranked by index weight.

A heuristic verdict is offered (the candidate that has fundamentals for that fiscal year AND whose
Morningstar ticker matches the held ticker), but every material row should be eyeballed.

INPUTS  r2k_panel.csv, security_cik_map.json + temporal_cik_map.json, dera_filing_index.csv,
        fundamentals_dera.csv, morningstar_long.csv
OUT     cik_audit.txt (readable) + cik_audit.csv (all rows, for building the override map)
RUN     python r2k_cik_audit.py
"""
import csv
import json

from r2k_universe import BASE, get_panel, load_maps, canon_cik

INDEX = BASE / "dera_filing_index.csv"
FUND = BASE / "fundamentals_dera.csv"
MS = BASE / "morningstar_long.csv"
OUT = BASE / "cik_audit.txt"
OUT_CSV = BASE / "cik_audit.csv"


def _ck(c):
    s = str(c)
    return str(int(s)) if s.isdigit() else s


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ntk(t):
    import re
    return re.sub(r"[^A-Z0-9]", "", str(t or "").upper())


def load_names():
    """{(cik, fy): as-filed registrant name} from the DERA filing index (point-in-time)."""
    out = {}
    if INDEX.exists():
        for r in csv.DictReader(open(INDEX, encoding="utf-8")):
            fy = str(r.get("fy", ""))
            if fy.isdigit():
                out[(_ck(r.get("cik", "")), fy)] = r.get("name", "")
    return out


def load_fund():
    """{(cik, fy): (revenue, net_income)} from fundamentals_dera.csv."""
    out = {}
    if FUND.exists():
        for r in csv.DictReader(open(FUND, encoding="utf-8")):
            if str(r.get("fiscal_year", "")).isdigit():
                out[(_ck(r["cik"]), r["fiscal_year"])] = (_f(r.get("revenue")), _f(r.get("net_income")))
    return out


def load_ms_ref(targets):
    """{(cik, fy): (ms_ticker_normalized, ms_name)} from morningstar_long.csv for the candidate ciks."""
    out = {}
    if not MS.exists():
        return out
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        hdr = next(r)
        h = {name: i for i, name in enumerate(hdr)}
        ci, ti, ni, fi = h.get("cik"), h.get("ticker"), h.get("name"), h.get("fiscal_year")
        if ci is None or fi is None:
            return out
        for row in r:
            if len(row) <= max(x for x in (ci, ti, ni, fi) if x is not None):
                continue
            c = _ck(row[ci])
            if c not in targets:
                continue
            key = (c, row[fi])
            out.setdefault(key, (_ntk(row[ti]) if ti is not None else "", row[ni] if ni is not None else ""))
    return out


def main():
    base, temporal = load_maps()
    panel = get_panel(index="R2KG")

    # find (ticker, year) where base vs temporal disagree
    rows, seen = [], set()
    for r in panel:
        nt, ticker, year, fy0, w = r["nt"], r["ticker"], int(r["year"]), r["fy0"], r["weight"]
        base_cik = canon_cik(base.get(nt))
        td = temporal.get(f"{year}-04-30", {})
        temp_cik = canon_cik(td.get(ticker) or td.get(str(ticker).upper()) or td.get(nt))
        if not (base_cik and temp_cik and base_cik != temp_cik):
            continue
        key = (nt, year)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"w": w, "ticker": ticker, "nt": nt, "year": year,
                     "fy": str(fy0) if fy0 not in (None, "") else "", "base": base_cik, "temp": temp_cik})

    cands = {x["base"] for x in rows} | {x["temp"] for x in rows}
    names, fund, msref = load_names(), load_fund(), load_ms_ref(cands)
    rows.sort(key=lambda x: -x["w"])

    def evid(cik, fy, held_nt):
        nm = names.get((cik, fy), "")
        fv = fund.get((cik, fy))
        has = fv is not None
        rev = f"{fv[0]/1e6:,.0f}" if (fv and fv[0] is not None) else "-"
        ni = f"{fv[1]/1e6:,.0f}" if (fv and fv[1] is not None) else "-"
        mst, _msn = msref.get((cik, fy), ("", ""))
        ms_match = (mst == held_nt)
        return nm[:26], has, rev, ni, ms_match

    def verdict(r):
        b_has = (r["base"], r["fy"]) in fund
        t_has = (r["temp"], r["fy"]) in fund
        b_ms = msref.get((r["base"], r["fy"]), ("", ""))[0] == r["nt"]
        t_ms = msref.get((r["temp"], r["fy"]), ("", ""))[0] == r["nt"]
        if b_has and not t_has: return "BASE (only it has FY fund.)"
        if t_has and not b_has: return "TEMP (only it has FY fund.)"
        if b_ms and not t_ms:   return "BASE (MS ticker match)"
        if t_ms and not b_ms:   return "TEMP (MS ticker match)"
        return "INSPECT (both plausible)"

    L = ["CIK ADJUDICATION  --  ticker-years where the base map and temporal map disagree on identity",
         f"  {len(rows)} disagreements; evidence per candidate so each is resolved from facts, by weight.", ""]
    vcount = {}
    csv_rows = []
    for r in rows:
        v = verdict(r); vcount[v.split(" (")[0]] = vcount.get(v.split(" (")[0], 0) + 1
        bnm, bhas, brev, bni, bms = evid(r["base"], r["fy"], r["nt"])
        tnm, thas, trev, tni, tms = evid(r["temp"], r["fy"], r["nt"])
        csv_rows.append({**{k: r[k] for k in ("ticker", "year", "fy", "w", "base", "temp")},
                         "base_name": bnm, "base_rev": brev, "base_ni": bni, "base_fund": int(bhas), "base_ms": int(bms),
                         "temp_name": tnm, "temp_rev": trev, "temp_ni": tni, "temp_fund": int(thas), "temp_ms": int(tms),
                         "verdict": v})
        if r["w"] >= 0.001 or len([x for x in rows if x["w"] >= 0.001]) < 25:  # detail the material ones
            L.append(f"  {r['ticker']:<7} {r['year']} (FY{r['fy']})   wt {100*r['w']:.3f}%    -> {v}")
            L.append(f"      BASE {r['base']:<10} {'fund' if bhas else 'NOFUND':<6} rev={brev:>9} ni={bni:>8} "
                     f"ms-tick={'Y' if bms else '.'}  {bnm}")
            L.append(f"      TEMP {r['temp']:<10} {'fund' if thas else 'NOFUND':<6} rev={trev:>9} ni={tni:>8} "
                     f"ms-tick={'Y' if tms else '.'}  {tnm}")
    L.append("")
    L.append("  VERDICT TALLY: " + "   ".join(f"{k}={v}" for k, v in sorted(vcount.items(), key=lambda kv: -kv[1])))
    L.append("  (BASE/TEMP = evidence favors that candidate; INSPECT = adjudicate from the names by hand.)")
    L.append("  Next: bake the resolved (ticker, year)->CIK overrides into a point-in-time map and switch")
    L.append("  resolve_identity to consult it first; base-first stays the fallback.")

    OUT.write_text("\n".join(L), encoding="utf-8")
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        if csv_rows:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
            w.writeheader(); w.writerows(csv_rows)
    print("\n".join(L))
    print(f"\n  -> {OUT.name} ; {OUT_CSV.name} ({len(csv_rows)} rows)")


if __name__ == "__main__":
    main()
