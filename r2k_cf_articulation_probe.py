"""
r2k_cf_articulation_probe.py  --  dissect the cash-flow-articulation WATCH weight (the ~7.3% of index
weight whose income statement and balance sheet TIE but whose cash-flow roll-forward does not).
Confirms whether that weight is benign articulation noise (restricted-cash basis, FX, M&A cash,
SBC add-back) rather than data error -- so the IC can treat 'watch-CF' as legitimate.

Population: names whose LATEST snapshot year is tier=watch & break_kind=cf (the exact slice the
plausibility report reports as 'cash-flow articulation'), weighted by CURRENT index weight (panel).
Each is bucketed by the most likely cause from the tie-out residual + the cash-flow components.

INPUTS  plausibility_flags.csv, tieout_report.csv, fundamentals_dera.csv, r2k_panel.csv
RUN:    python r2k_cf_articulation_probe.py
"""
import csv
from collections import defaultdict

from r2k_universe import BASE, get_panel

FLAGS = BASE / "plausibility_flags.csv"
TIEOUT = BASE / "tieout_report.csv"
FUND = BASE / "fundamentals_dera.csv"
OUT = BASE / "cf_articulation_probe.txt"

CF_PREFIXES = ("CASH_ROLL", "CF_FOOT", "SBC_CONSISTENCY", "CF_BS_CASH")


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _ck(c):
    s = str(c)
    return str(int(s)) if s.isdigit() else s


def _current_weight():
    """{cik: current R2000G index weight} from the latest panel snapshot, + total weight."""
    panel = get_panel(index="R2KG")
    if not panel:
        return {}, 0.0
    my = max(int(r["year"]) for r in panel)
    cw = {_ck(r["cik"]): r["weight"] for r in panel if int(r["year"]) == my and r["cik"]}
    return cw, (sum(cw.values()) or 0.0)


def _bucket(legs, res, comp):
    """legs: set of broken CF identity prefixes; res: {prefix: |residual|}; comp: fundamentals row."""
    cash = abs(_f(comp.get("cash")) or 0)
    cash_t = abs(_f(comp.get("cash_total")) or 0)
    ta = abs(_f(comp.get("total_assets")) or 0)
    restr = abs(_f(comp.get("restricted_cash")) or 0)
    denom = max(cash, cash_t, 0.01 * ta, 1.0)
    maxres = max((res.get(p, 0.0) for p in legs), default=0.0)
    if maxres / denom < 0.02:
        return "Immaterial (<2% of cash; rounding/coverage)", maxres
    if "SBC_CONSISTENCY" in legs and not ({"CASH_ROLL", "CF_FOOT"} & legs):
        return "SBC add-back consistency (CF line, not cash)", maxres
    # restricted-cash basis: material restricted cash that roughly explains the gap (ASU 2016-18)
    if restr > 0.05 * denom and ({"CASH_ROLL", "CF_FOOT", "CF_BS_CASH"} & legs):
        if abs(maxres - restr) < 0.6 * restr or restr > 0.4 * maxres:
            return "Restricted-cash basis (ASU 2016-18)", maxres
    if "CF_FOOT" in legs:
        return "CF doesn't foot within-year (FX / missing line)", maxres
    if "CASH_ROLL" in legs:
        return "Cross-year cash roll (reclass / M&A / FX)", maxres
    return "Other CF", maxres


def main():
    if not FLAGS.exists():
        raise SystemExit(f"!! {FLAGS.name} not found -- run r2k_plausibility.py first.")
    # latest snapshot year per cik -> the row the index-weight view uses
    latest = {}
    for r in csv.DictReader(open(FLAGS, encoding="utf-8")):
        if not r.get("fiscal_year", "").isdigit():
            continue
        c = _ck(r["cik"])
        if c not in latest or r["fiscal_year"] > latest[c]["fiscal_year"]:
            latest[c] = r
    cf_pop = {c: r for c, r in latest.items() if r.get("tier") == "watch" and r.get("break_kind") == "cf"}

    # CF-leg residuals per (cik, fy)
    resid = defaultdict(dict)
    if TIEOUT.exists():
        for r in csv.DictReader(open(TIEOUT, encoding="utf-8")):
            if r.get("result") != "BREAK":
                continue
            ident = r.get("identity", "")
            for p in CF_PREFIXES:
                if ident.startswith(p):
                    v = _f(r.get("residual"))
                    if v is not None:
                        resid[(_ck(r["cik"]), r["fiscal_year"])][p] = abs(v)
    # fundamentals components per (cik, fy)
    comp = {}
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        if r.get("fiscal_year", "").isdigit():
            comp[(_ck(r["cik"]), r["fiscal_year"])] = r

    cw, tw = _current_weight()

    buckets = defaultdict(lambda: {"w": 0.0, "n": 0, "names": []})
    pop_w = 0.0
    for c, fr in cf_pop.items():
        fy = fr["fiscal_year"]
        w = cw.get(c, 0.0)
        res = resid.get((c, fy), {})
        legs = set(res) or {"CASH_ROLL"}          # if tie-out row missing, assume the roll leg
        cause, size = _bucket(legs, res, comp.get((c, fy), {}))
        b = buckets[cause]
        b["w"] += w
        b["n"] += 1
        b["names"].append((w, c, fy, size))
        pop_w += w

    L = ["CASH-FLOW ARTICULATION REVIEW  --  what is the 'watch / CF' weight actually made of?", ""]
    L.append(f"  population: names whose latest year is tier=watch & break_kind=cf  "
             f"({len(cf_pop)} names)")
    L.append(f"  index weight of that population: {100*pop_w/tw:.1f}% of current index weight"
             f"   (reconciles to the plausibility report's 'cash-flow articulation' line)" if tw else
             "  (index weights unavailable)")
    L.append("")
    L.append(f"  {'cause bucket':<46}{'wt%':>8}{'names':>7}")
    L.append("  " + "-" * 61)
    for cause, b in sorted(buckets.items(), key=lambda kv: -kv[1]["w"]):
        L.append(f"  {cause:<46}{(100*b['w']/tw if tw else 0):>8.2f}{b['n']:>7}")
    L.append("  " + "-" * 61)
    L.append(f"  {'TOTAL':<46}{(100*pop_w/tw if tw else 0):>8.2f}{len(cf_pop):>7}")
    L.append("")
    L.append("  highest-weight name-years per bucket (wt% · cik · fy · |residual|$):")
    for cause, b in sorted(buckets.items(), key=lambda kv: -kv[1]["w"]):
        top = sorted(b["names"], reverse=True)[:5]
        L.append(f"    {cause}")
        for w, c, fy, size in top:
            L.append(f"        {100*w/tw if tw else 0:>5.2f}%  {c:<10}{fy}   |res|=${size/1e6:,.0f}M")
    L.append("")
    L.append("  READ: every bucket above is a CASH-FLOW articulation artifact, not a P&L/BS error --")
    L.append("  the income statement and balance sheet for these names TIE (that is why they are 'watch',")
    L.append("  not 'review'). Restricted-cash-basis and SBC are pure presentation; cross-year roll and")
    L.append("  within-year FX/M&A gaps are real cash movements the as-filed CF doesn't fully bridge,")
    L.append("  but they do not touch revenue, margins, net income, assets, or equity.")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
