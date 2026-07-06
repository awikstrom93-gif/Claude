"""
r2k_biotech_check.py  --  break down R2000G's UNPROFITABLE weight by Morningstar Industry,
so the biotech definition can be validated/calibrated. Shows how much of the unprofitable
tail sits in 'Biotechnology' vs adjacent life-sciences industries (Drug Manufacturers,
Diagnostics & Research, Medical Devices) vs everything else.

RUN: python r2k_biotech_check.py            # latest year + a couple of reference years
"""
from collections import defaultdict

from r2k_perf_io import load_monthly_holdings, BASE, ntk
from r2k_step3_analytics import load_fundamentals, pick_fy0, company_metrics, load_maps
from r2k_universe import annual_spine   # single source: June spine + drift/truncation guards

LIFESCI_HINTS = ["biotech", "drug manufactur", "pharmaceutical", "diagnostics", "medical device",
                 "medical instrument", "life science"]


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

def tmap_of(base, temporal):
    t = dict(base)
    for _, d in (temporal or {}).items():
        for tk, c in d.items():
            if c: t.setdefault(ntk(tk), str(c))
    return t

def fund_for(nf, cik):
    if not cik: return None
    try: return nf.get(str(int(cik)))
    except (TypeError, ValueError): return nf.get(str(cik))

def main():
    hr = find(["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"])
    if not hr: raise SystemExit("R2000G holdings not found")
    hold = load_monthly_holdings(hr, verbose=False)
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps(); tmap = tmap_of(base, temporal)
    def hcik(h): return h["cik"] or tmap.get(h["nt"])
    spine = annual_spine(hold); years = sorted(spine)
    focus = [y for y in (2021, years[-1]) if y in spine] or [years[-1]]

    for y in focus:
        snap = hold[spine[y]]; snap_dt = spine[y]
        by_ind = defaultdict(float); unprof_tot = 0.0
        narrow_bio = lifesci = 0.0
        for h in snap:
            cf = fund_for(facts, hcik(h))
            if not cf: continue
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is None: continue
            cm = company_metrics(cf, fy0)
            if cm["prof_ni"] is not False: continue          # only unprofitable names
            w = h["weight"]; ind = (h.get("ms_industry") or "Unknown").strip() or "Unknown"
            by_ind[ind] += w; unprof_tot += w
            il = ind.lower()
            if "biotech" in il: narrow_bio += w
            if any(k in il for k in LIFESCI_HINTS): lifesci += w
        if unprof_tot <= 0: continue
        print(f"\n==== {y}: unprofitable weight by Morningstar Industry (top 15) ====")
        print(f"  {'industry':<42}{'% of unprofitable':>18}")
        for ind, w in sorted(by_ind.items(), key=lambda x: -x[1])[:15]:
            star = "  <-- life-sci" if any(k in ind.lower() for k in LIFESCI_HINTS) else ""
            print(f"  {ind[:42]:<42}{100*w/unprof_tot:>17.1f}%{star}")
        print(f"  {'-'*60}")
        print(f"  NARROW biotech ('Biotechnology' only): {100*narrow_bio/unprof_tot:>5.1f}% of unprofitable")
        print(f"  BROAD life-sciences (biotech+pharma+diagnostics+devices): {100*lifesci/unprof_tot:>5.1f}% of unprofitable")
    print("\n  -> If a big slice of the unprofitable tail is in Drug Manufacturers / Diagnostics / Medical")
    print("     Devices, the narrow 'Biotechnology' tag understates it. Set BIOTECH_KEYWORDS in step 9")
    print("     to broaden (e.g. \"biotech,drug manufactur,diagnostics\").")


if __name__ == "__main__":
    main()
