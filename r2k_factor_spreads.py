"""
r2k_factor_spreads.py  --  DID QUALITY PAY INSIDE R2000G?  The keystone evidence the report was
missing: it joins each constituent's as-filed quality factor (from the panel) to its FORWARD 12-month
return (from the performance file) and measures the long-short quintile spread per factor, per year
and cumulative.  No look-ahead: the factor is known at the April snapshot; the return is earned over
the following 12 months (May→Apr).

Factors (quintiles, equal-weighted; top-quality minus bottom-quality):
  ROIC, GP/Assets (Novy-Marx), Accruals (low=quality), Net margin, FCF margin, and the
  Profitable-minus-Never-profitable cohort spread.

INPUTS  r2k_panel.csv (quality) + the Morningstar performance workbook (returns).
RUN:    python r2k_factor_spreads.py
"""
from datetime import date

from r2k_universe import get_panel, BASE
from r2k_perf_io import load_performance, ntk

OUT = BASE / "r2k_factor_spreads.txt"

# (label, panel field, direction): +1 high value = higher quality, -1 low value = higher quality
FACTORS = [("ROIC", "roic", +1), ("GP/Assets", "gp_to_assets", +1),
           ("Accruals(low=Q)", "accruals", -1), ("NetMargin", "net_margin", +1),
           ("FCFMargin", "fcf_margin", +1)]
MIN_N = 50          # need a reasonable cross-section to form quintiles


def _ck(c):
    s = str(c)
    return str(int(s)) if s.isdigit() else s


def _parse_snap(s):
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fwd_return(rec, window):
    """Compound a constituent's periodic returns over the forward window; None if too sparse."""
    rs = [rec["ret"].get(d) for d in window]
    present = [r for r in rs if r is not None]
    if len(present) < max(6, len(window) - 3):
        return None
    g = 1.0
    for r in present:
        g *= (1 + r)
    return g - 1.0


def _quintile_spread(pairs, direction):
    """pairs: [(factor_value, fwd_return)]. Returns (top-quality minus bottom-quality, n) equal-weighted."""
    pts = [(v * direction, r) for v, r in pairs if v is not None and r is not None]
    if len(pts) < MIN_N:
        return None, 0
    pts.sort(key=lambda x: x[0])
    q = len(pts) // 5
    if q < 5:
        return None, 0
    bot = [r for _, r in pts[:q]]
    top = [r for _, r in pts[-q:]]
    return (sum(top) / len(top) - sum(bot) / len(bot)), len(pts)


def compute(panel, series, index_rows, all_dates):
    by_cik, by_nt = {}, {}
    for rec in series:
        m = rec["meta"]
        if m.get("cik"):
            by_cik[_ck(m["cik"])] = rec
        if m.get("nt"):
            by_nt.setdefault(m["nt"], rec)
    ridx = index_rows.get("R2KG") or index_rows.get("R2000G")

    # panel grouped by snapshot year (covered R2000G constituents)
    years = sorted({int(r["year"]) for r in panel})
    per_year = {}
    for y in years:
        d0 = None
        rows = [r for r in panel if int(r["year"]) == y and r["covered"]]
        if rows:
            d0 = _parse_snap(rows[0]["snapshot"])
        if d0 is None:
            continue
        window = [d for d in all_dates if d > d0][:12]
        if len(window) < 10:        # incomplete forward year (e.g. the final snapshot) -> skip
            continue
        idx_fwd = _fwd_return(ridx, window) if ridx else None
        # forward return per constituent
        fwd = {}
        for r in rows:
            rec = by_cik.get(_ck(r["cik"])) or by_nt.get(r["nt"])
            if rec:
                fr = _fwd_return(rec, window)
                if fr is not None:
                    fwd[r["cik"]] = fr
        # factor spreads
        fac = {}
        for label, field, direction in FACTORS:
            pairs = [(r.get(field), fwd.get(r["cik"])) for r in rows if r["cik"] in fwd]
            sp, n = _quintile_spread(pairs, direction)
            fac[label] = sp
        # cohort spread: profitable minus never-profitable (equal-weight)
        prof = [fwd[r["cik"]] for r in rows if r["cik"] in fwd and r["cohort"] == "profitable"]
        nev = [fwd[r["cik"]] for r in rows if r["cik"] in fwd and r["cohort"] == "never_profitable"]
        coh = (sum(prof) / len(prof) - sum(nev) / len(nev)) if (len(prof) >= 10 and len(nev) >= 10) else None
        per_year[y] = {"idx": idx_fwd, "fac": fac, "coh": coh, "n": len(fwd)}
    return years, per_year


def _summ(vals):
    """(mean, hit-rate %, cumulative compounded) over the non-None annual spreads."""
    xs = [v for v in vals if v is not None]
    if not xs:
        return None, None, None
    mean = sum(xs) / len(xs)
    hit = 100 * sum(1 for v in xs if v > 0) / len(xs)
    cum = 1.0
    for v in xs:
        cum *= (1 + v)
    return mean, hit, cum - 1.0


def main():
    panel = get_panel(index="R2KG")
    series, index_rows, all_dates = load_performance(verbose=False)
    years, per_year = compute(panel, series, index_rows, all_dates)
    yy = [y for y in years if y in per_year]
    labels = [f[0] for f in FACTORS] + ["Prof-Never"]

    L = ["QUALITY-FACTOR RETURN SPREADS  --  did quality pay inside R2000G? (forward 12m, May->Apr)",
         "  long-short = top-quality quintile minus bottom-quality quintile, equal-weighted, % per year", ""]
    L.append("  " + f"{'fwd yr':<9}{'IndexR%':>9}" + "".join(f"{lab:>16}" for lab in labels) + f"{'n':>7}")
    L.append("  " + "-" * (18 + 16 * len(labels) + 7))
    for y in yy:
        d = per_year[y]
        cells = "".join(f"{(100*d['fac'][lab]):>16.1f}" if d["fac"].get(lab) is not None else f"{'·':>16}"
                        for lab in labels[:-1])
        coh = f"{100*d['coh']:>16.1f}" if d["coh"] is not None else f"{'·':>16}"
        idx = f"{100*d['idx']:>9.1f}" if d["idx"] is not None else f"{'·':>9}"
        L.append(f"  {f'{y}->{y+1}':<9}{idx}{cells}{coh}{d['n']:>7}")
    L.append("  " + "-" * (18 + 16 * len(labels) + 7))
    # summary rows
    for name, agg in (("mean/yr", 0), ("hit-rate%", 1), ("cumulative", 2)):
        cells = []
        for lab in labels:
            series_l = [per_year[y]["fac"].get(lab) if lab != "Prof-Never" else per_year[y]["coh"] for y in yy]
            m = _summ(series_l)[agg]
            scale = 1 if agg == 1 else 100
            cells.append(f"{(scale*m):>16.1f}" if m is not None else f"{'·':>16}")
        L.append(f"  {name:<18}" + "".join(cells))
    L.append("")
    L.append("  READ: a positive spread means HIGH-quality names outperformed LOW-quality over the next")
    L.append("  year. Persistently NEGATIVE spreads (esp. 2019-2021) are the quantitative proof that")
    L.append("  quality was PUNISHED in R2000G's growth regime -- the structural headwind a quality-")
    L.append("  disciplined manager faced. 'Prof-Never' = profitable minus never-profitable cohort.")
    L.append("  Equal-weighted quintiles (standard factor construction); no look-ahead (factor known at")
    L.append("  the April snapshot, return earned the following 12 months).")

    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
