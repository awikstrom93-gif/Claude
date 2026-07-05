"""
r2k_calc.py  --  THE one definition of the pure calculation primitives shared across the pipeline.

These helpers were previously RE-DEFINED (byte-for-byte, or as mathematically-equivalent variants) in
several steps: compound() in step4/step8/step9, nearest_prior() in step5/step9, weight_conc() in step8
and the concentration view, and the Carino smoothing coefficients inline in step5/step8/step9. That is
exactly the kind of duplication that lets one tab silently drift from another. Consolidating them here --
a LEAF module with no project imports, so anything can import it without a cycle -- means a returns
compound, a holdings-timing lookup, a concentration stat, or an attribution linking factor is computed
the SAME way everywhere by construction.

No I/O, no pandas, no project imports.  Pure functions only.
"""
import math


def compound(rets):
    """Geometric compounding of periodic returns, skipping None. Returns the cumulative return
    (0.0 = flat, 0.5 = +50%). compound(monthly returns) telescopes to the exact price multiple."""
    g = 1.0
    for r in rets:
        if r is not None:
            g *= (1.0 + r)
    return g - 1.0


def nearest_prior(sorted_dates, target):
    """Latest date STRICTLY before `target` (beginning-of-month holdings snapshot), or None.
    `sorted_dates` must be ascending."""
    prev = None
    for d in sorted_dates:
        if d < target:
            prev = d
        else:
            break
    return prev


def carino_K(cum):
    """Carino TOTAL smoothing coefficient for a cumulative return `cum`: ln(1+cum)/cum (->1 as cum->0).
    Used so arithmetic per-period contributions link to the compounded total."""
    return math.log(1 + cum) / cum if (cum is not None and abs(cum) > 1e-12) else 1.0


def carino_k(r):
    """Carino PER-PERIOD smoothing coefficient for a single-period return `r`: ln(1+r)/r (->1 as r->0)."""
    return math.log(1 + r) / r if (r is not None and abs(r) > 1e-12) else 1.0


DEFAULT_TOP_NS = [1, 5, 10, 25, 50]


def weight_conc(rows, top_ns=DEFAULT_TOP_NS):
    """Concentration statistics from `rows` (each a dict/object with a 'weight' key, any scale):
    top-N weight %, max name %, HHI (sum of squared percent weights), and effective-N (1/sum(share^2)).
    Weights are normalized internally, so raw index weights or fractions both work."""
    ws = sorted((r["weight"] for r in rows), reverse=True)
    tw = sum(ws) or 1e-9
    shares = [w / tw for w in ws]
    return {"n": len(ws), "top": {k: round(sum(ws[:k]) / tw * 100, 2) for k in top_ns},
            "max": round(ws[0] / tw * 100, 2) if ws else None,
            "hhi": round(sum((s * 100) ** 2 for s in shares), 1),
            "effn": round(1 / sum(s * s for s in shares), 0) if shares else None}


def annual_spine(holdings, target_month):
    """{year: snapshot_date nearest `target_month`} from an iterable of dates (the monthly holdings
    keys). One snapshot per calendar year -- the annual spine every step aligns fundamentals to."""
    out = {}
    for d in sorted(holdings):
        cur = out.get(d.year)
        if cur is None or abs(d.month - target_month) < abs(cur.month - target_month):
            out[d.year] = d
    return out


def _selftest():
    # compound telescopes to the price multiple
    assert abs(compound([0.1, -0.05, 0.2]) - ((1.1 * 0.95 * 1.2) - 1)) < 1e-12
    assert abs(compound([None, 0.1, None]) - 0.1) < 1e-12
    # nearest_prior is strictly-before
    from datetime import date
    ds = [date(2020, 1, 31), date(2020, 4, 30), date(2020, 7, 31)]
    assert nearest_prior(ds, date(2020, 5, 15)) == date(2020, 4, 30)
    assert nearest_prior(ds, date(2020, 4, 30)) == date(2020, 1, 31)   # strictly before
    assert nearest_prior(ds, date(2019, 1, 1)) is None
    # carino: sum of per-period k*R links to ln(1+cum); K normalizes it back to cum
    rs = [0.03, -0.02, 0.05]
    cum = compound(rs)
    linked = sum(carino_k(r) / carino_K(cum) * r for r in rs)
    assert abs(linked - cum) < 1e-12, (linked, cum)
    # weight_conc: two equal names -> top1 50%, HHI 5000, effN 2
    wc = weight_conc([{"weight": 1.0}, {"weight": 1.0}])
    assert wc["top"][1] == 50.0 and wc["hhi"] == 5000.0 and wc["effn"] == 2
    # annual_spine picks the April-nearest snapshot per year
    sp = annual_spine([date(2020, 3, 31), date(2020, 4, 30), date(2021, 6, 30)], 4)
    assert sp[2020] == date(2020, 4, 30) and sp[2021] == date(2021, 6, 30)
    print("  r2k_calc selftest: PASS")


if __name__ == "__main__":
    _selftest()
