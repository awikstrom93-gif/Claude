"""
============================================================
r2k_unknown_diagnostic.py  --  audit the "Unknown (no NI)" cohort.
============================================================
Ensures the Unknown bucket isn't inflated by MISSED net income. For each annual
snapshot it classifies every R2000G constituent's cohort and, for the Unknowns,
records WHY:

  no_fundamentals     CIK not in the as-filed dataset (foreign/failed/ SPAC)   -> expected
  no_fy0_before_snap  no 10-K filed before the snapshot (recent IPO)           -> expected
  ni_blank            in the dataset with a fiscal year, but net_income is None -> SUSPECT
                      (flagged HARD if NI exists for other years of the company)

OUTPUTS  (R2KG_BASE)
    unknown_cohort_diagnostic.csv   one row per Unknown name-year (weight, reason, fy0,
                                    years_with_NI, has_return)
    + console summary: Unknown weight per year split by reason, and the worst
      ni_blank-with-NI-elsewhere offenders (the names worth a 10-K check).

RUN: python r2k_unknown_diagnostic.py
============================================================
"""
import csv

from r2k_perf_io import load_monthly_holdings, BASE, ntk
from r2k_step3_analytics import load_fundamentals, pick_fy0, company_metrics, load_maps
from r2k_universe import annual_spine   # single source: June spine + drift/truncation guards

OUT = BASE / "unknown_cohort_diagnostic.csv"


def find(pats):
    for p in pats:
        c = list(BASE.glob(p))
        if c: return c[0]
    return None


def norm_facts(facts):
    out = {}
    for k, v in facts.items():
        try: out[str(int(k))] = v
        except (TypeError, ValueError): out[str(k)] = v
    return out


def ticker_cik_map(base, temporal):
    tmap = dict(base)
    for _, d in (temporal or {}).items():
        for tk, c in d.items():
            if c: tmap.setdefault(ntk(tk), str(c))
    return tmap


def fund_for(nf, cik):
    if not cik: return None
    try: return nf.get(str(int(cik)))
    except (TypeError, ValueError): return nf.get(str(cik))


def main():
    hr = find(["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"])
    if not hr: raise SystemExit("!! R2000G holdings not found")
    hold = load_monthly_holdings(hr, verbose=False)
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps(); tmap = ticker_cik_map(base, temporal)
    def hcik(h): return h["cik"] or tmap.get(h["nt"])

    spine = annual_spine(hold); years = sorted(spine)
    rows = []
    print(f"{'Year':<6}{'Cov wt%':>9}{'Unknown wt%':>13}  | reason split (of Unknown wt) -> "
          f"{'no_fund':>9}{'no_fy0':>9}{'ni_blank':>10}{'ni_blank&NIelse':>17}")
    for y in years:
        snap = hold[spine[y]]; snap_dt = spine[y]
        tw = sum(h["weight"] for h in snap) or 1e-9
        unk = {"no_fundamentals": 0.0, "no_fy0_before_snap": 0.0, "ni_blank": 0.0}
        ni_blank_else = 0.0; cov_w = 0.0
        for h in snap:
            cik = hcik(h); cf = fund_for(facts, cik)
            if cf is None:
                unk["no_fundamentals"] += h["weight"]
                rows.append([y, h["ticker"], cik or "", round(h["weight"], 4), "no_fundamentals", "", "", h["name"]])
                continue
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is None:
                unk["no_fy0_before_snap"] += h["weight"]
                rows.append([y, h["ticker"], cik, round(h["weight"], 4), "no_fy0_before_snap", "", "", h["name"]])
                continue
            cov_w += h["weight"]
            cm = company_metrics(cf, fy0)
            if cm["cohort"] != "unknown":
                continue
            # ni blank for fy0 -- check other years
            yrs_ni = sorted(yy for yy in cf if cf[yy].get("net_income") is not None)
            unk["ni_blank"] += h["weight"]
            if yrs_ni: ni_blank_else += h["weight"]
            rows.append([y, h["ticker"], cik, round(h["weight"], 4), "ni_blank", fy0,
                         ",".join(str(x) for x in yrs_ni), h["name"]])
        u = sum(unk.values())
        print(f"{y:<6}{100*cov_w/tw:>8.1f}%{100*u/tw:>12.2f}%  | "
              f"{'':>3}{100*unk['no_fundamentals']/tw:>8.2f}%{100*unk['no_fy0_before_snap']/tw:>8.2f}%"
              f"{100*unk['ni_blank']/tw:>9.2f}%{100*ni_blank_else/tw:>16.2f}%")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["year", "ticker", "cik", "weight", "reason", "fy0", "years_with_NI", "name"])
        w.writerows(rows)

    # the actionable list: ni_blank names that HAVE NI in other years, by weight
    suspects = [r for r in rows if r[4] == "ni_blank" and r[6]]
    suspects.sort(key=lambda r: -r[3])
    print(f"\n  {len(rows)} Unknown name-years -> {OUT.name}")
    print(f"  SUSPECT (ni_blank but NI exists in other years) -- worth a 10-K check, top 20 by weight:")
    print(f"    {'year':<6}{'ticker':<8}{'wt%':>7}  {'fy0':<6}{'years_with_NI':<26}name")
    for r in suspects[:20]:
        print(f"    {r[0]:<6}{r[1]:<8}{r[3]:>7}  {str(r[5]):<6}{r[6][:24]:<26}{r[7][:30]}")
    if not suspects:
        print("    (none -- every ni_blank name has NO net income in ANY year, i.e. genuinely never reported)")


if __name__ == "__main__":
    main()
