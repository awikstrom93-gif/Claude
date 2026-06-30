"""
r2k_audit.py  --  COMPREHENSIVE data audit: hunt the *unknown* errors (the next mezzanine / -$10B
IS_GP) across the whole universe, ranked by INDEX WEIGHT x SEVERITY so we fix what moves the
benchmark first. Three families, one prioritized findings list:

  1. IDENTITY-BREAK SEVERITY -- every tie-out break scaled by company size; a residual that is a large
     fraction of revenue/assets is catastrophic (not rounding) even on an otherwise-"tolerated" leg.
  2. LINE-ITEM SANITY -- per-item bounds that prove a value is wrong regardless of internal consistency
     (GM>100%, cash>assets, equity>assets, neg D&A, PPE>assets, debt>liab, NI-to-common>parent NI, ...).
  3. YoY DISCONTINUITY -- revenue/assets/equity/debt/NI jumping >6x or <1/6x (M&A or error -> review).

Reads fundamentals_dera.csv + tieout_report.csv (+ holdings/cik map for weighting, via r2k_plausibility).
Writes audit_report.txt (top findings by weight x severity) + audit_findings.csv (every finding).
RUN:  python r2k_audit.py
"""
from pathlib import Path
import os, csv
from collections import defaultdict

from r2k_plausibility import load_weights, load_cikmap, ntk, BASE

FUND = BASE / "fundamentals_dera.csv"
TIE = BASE / "tieout_report.csv"
OUT = BASE / "audit_report.txt"
CSVOUT = BASE / "audit_findings.csv"

GATING = ("IS_GP", "IS_NI", "IS_NCI", "BS_FOOTS", "BS_EQUITY")
KEY_YOY = ["revenue", "total_assets", "total_equity", "total_debt", "net_income"]


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def scale_of(r):
    return max(abs(r.get("revenue") or 0), abs(r.get("total_assets") or 0), 1.0)


def line_item_checks(r):
    """Return (severity_fraction, severity_class, code, detail) for each violated bound. severity_fraction
    = magnitude of the violation / company scale, so big-dollar violations rank above pennies."""
    f = r.get
    out = []
    sc = scale_of(r)
    rev, ta, tl = f("revenue"), f("total_assets"), f("total_liabilities")
    gp, oi, eb, da = f("gross_profit"), f("operating_income"), f("ebitda"), f("depreciation_amortization")
    ni, nicom, pretax, tax = f("net_income"), f("net_income_to_common"), f("pretax_income"), f("tax_expense")
    cash, eq, ppe = f("cash"), f("total_equity"), f("ppe_net")
    debt, mezz, inte = f("total_debt"), f("redeemable_nci"), f("interest_expense")

    def n(x):
        return f"{x:,.0f}" if x is not None else "?"

    def add(cond, mag, cls, code, detail):
        if cond:
            out.append((abs(mag) / sc, cls, code, detail))

    cogs = f("cost_of_revenue")
    # CRITICAL -- the value is provably wrong
    add(rev is not None and rev < 0, rev, "CRIT", "revenue<0", f"rev={n(rev)}")
    add(gp is not None and rev is not None and rev > 0 and gp > rev * 1.005, (gp or 0) - (rev or 0),
        "CRIT", "GM>100%", f"gp={n(gp)} rev={n(rev)}")
    add(cogs is not None and cogs < 0, cogs, "CRIT", "COGS<0", f"cogs={n(cogs)}")
    add(eb is not None and oi is not None and eb < oi - max(5000, 0.01 * abs(oi)), (oi or 0) - (eb or 0),
        "CRIT", "EBITDA<OI(negD&A)", f"ebitda={n(eb)} oi={n(oi)}")
    add(da is not None and da < 0, da, "CRIT", "D&A<0", f"da={n(da)}")
    add(cash is not None and cash < 0, cash, "CRIT", "cash<0", f"cash={n(cash)}")
    add(cash is not None and ta is not None and ta > 0 and cash > ta * 1.01, (cash or 0) - (ta or 0),
        "CRIT", "cash>assets", f"cash={n(cash)} ta={n(ta)}")
    add(eq is not None and ta is not None and ta > 0 and eq > ta * 1.01, (eq or 0) - (ta or 0),
        "CRIT", "equity>assets", f"eq={n(eq)} ta={n(ta)}")
    add(ta is not None and ta <= 0, ta, "CRIT", "assets<=0", f"ta={n(ta)}")
    add(tl is not None and tl < 0, tl, "CRIT", "liabilities<0", f"tl={n(tl)}")
    add(ppe is not None and ta is not None and ta > 0 and ppe > ta * 1.01, (ppe or 0) - (ta or 0),
        "CRIT", "PPE>assets", f"ppe={n(ppe)} ta={n(ta)}")
    # NOTE: mezz<0 and mezz>assets are NOT independent errors -- a deeply-negative-equity company
    # (large redeemable preferred + huge accumulated deficit) legitimately has mezz = A-L-E > assets,
    # and small negative redeemable NCI from accumulated losses is real. Balance-sheet consistency is
    # owned authoritatively by BS_FOOTS (A=L+E+mezz); duplicating it here only produced false positives.
    add(inte is not None and inte < 0, inte, "CRIT", "interest<0", f"int={n(inte)}")
    add(nicom is not None and ni is not None and ni > 0 and nicom > ni * 1.02, (nicom or 0) - (ni or 0),
        "CRIT", "NItoCommon>parentNI", f"nicom={n(nicom)} ni={n(ni)}")

    # SUSPECT -- usually wrong, occasionally legitimate
    add(debt is not None and tl is not None and tl > 0 and debt > tl * 1.05, (debt or 0) - (tl or 0),
        "SUSPECT", "debt>liabilities", f"debt={n(debt)} tl={n(tl)}")
    add(inte is not None and debt is not None and debt > 0 and inte > debt * 0.4, inte,
        "SUSPECT", "interest>40%ofdebt", f"int={n(inte)} debt={n(debt)}")
    add(da is not None and rev is not None and rev > 0 and da > rev, (da or 0) - (rev or 0),
        "SUSPECT", "D&A>revenue", f"da={n(da)} rev={n(rev)}")
    add(ni is not None and rev is not None and rev > 0 and abs(ni) > 2 * rev, ni,
        "SUSPECT", "|NI|>2xrevenue", f"ni={n(ni)} rev={n(rev)}")
    add(tax is not None and pretax is not None and pretax > 0 and tax > pretax * 1.05, (tax or 0) - (pretax or 0),
        "SUSPECT", "tax>pretax", f"tax={n(tax)} pretax={n(pretax)}")
    return out


def main():
    rows = list(csv.DictReader(open(FUND, encoding="utf-8")))
    for r in rows:
        for k, v in list(r.items()):
            if k not in ("cik", "fiscal_year", "sector", "taxonomy", "form", "breaks", "provenance",
                         "debt_flag", "entity_flag"):
                r[k] = fnum(v)
    # drop reverse-merger PREDECESSOR years -- a different entity occupied the CIK then, so its values
    # (and YoY jumps at the boundary) are not this constituent's and must not pollute the audit.
    predecessor = {(r["cik"], r["fiscal_year"]) for r in rows if r.get("entity_flag") == "PREDECESSOR"}
    rows = [r for r in rows if (r["cik"], r["fiscal_year"]) not in predecessor]
    by_key = {(r["cik"], r["fiscal_year"]): r for r in rows}
    by_cik = defaultdict(list)
    for r in rows:
        by_cik[r["cik"]].append(r)

    # index weight per cik (latest snapshot, name-level)
    weights, cm = load_weights(), load_cikmap()
    cik2w = {}
    if weights and cm:
        for nt, w in weights.items():
            c = cm.get(nt)
            if c:
                cik2w[c] = w
    wt = lambda c: cik2w.get(c, 0.0)

    findings = []   # (score, weight, cik, fy, sevclass, code, detail)

    # 1. identity-break severity
    for t in csv.DictReader(open(TIE, encoding="utf-8")):
        if t["result"] != "BREAK":
            continue
        base = t["identity"].split("(")[0]
        if base not in GATING:
            continue
        resid = fnum(t["residual"])
        r = by_key.get((t["cik"], t["fiscal_year"]))
        if resid is None or r is None:
            continue
        sev = abs(resid) / scale_of(r)
        cls = "CATASTROPHIC" if sev >= 0.5 else ("MATERIAL" if sev >= 0.05 else "MINOR")
        if cls == "MINOR":
            continue
        findings.append((sev * max(wt(t["cik"]), 0.003), wt(t["cik"]), t["cik"], t["fiscal_year"],
                         cls, f"IDBREAK:{base}", f"residual={resid:,.0f} ({sev:.0%} of scale)"))

    # 2. line-item sanity
    for r in rows:
        for sev, cls, code, detail in line_item_checks(r):
            findings.append((sev * max(wt(r["cik"]), 0.003), wt(r["cik"]), r["cik"], r["fiscal_year"],
                             cls, code, detail))

    # 3. YoY discontinuity
    for c, yrs in by_cik.items():
        yrs = sorted([y for y in yrs if y["fiscal_year"].isdigit()], key=lambda y: y["fiscal_year"])
        for prev, cur in zip(yrs, yrs[1:]):
            for k in KEY_YOY:
                a, b = prev.get(k), cur.get(k)
                if a is None or b is None or abs(a) < 1e6:
                    continue
                ratio = b / a
                if a > 0 and (ratio > 6 or ratio < 1 / 6):
                    sev = min(abs(b - a) / scale_of(cur), 5.0)
                    findings.append((sev * max(wt(c), 0.003), wt(c), c, cur["fiscal_year"],
                                     "YOY", f"YoY:{k}", f"{prev['fiscal_year']}->{cur['fiscal_year']}: {a:,.0f} -> {b:,.0f} ({ratio:.1f}x)"))

    findings.sort(key=lambda x: -x[0])
    # write every finding
    with open(CSVOUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["score", "index_weight_pct", "cik", "fiscal_year", "severity", "code", "detail"])
        for sc, wgt, cik, fy, cls, code, detail in findings:
            w.writerow([f"{sc:.4f}", f"{wgt:.3f}", cik, fy, cls, code, detail])

    from collections import Counter
    by_code = Counter(code for *_, code, _ in findings)
    by_cls = Counter(cls for *_3, cls, _2, _1 in findings)
    L = [f"COMPREHENSIVE AUDIT  --  {len(findings):,} findings across {len(rows):,} company-years"
         f"  ({'weighted by index' if cik2w else 'UNWEIGHTED -- no holdings found'})", ""]
    L.append("BY SEVERITY: " + ", ".join(f"{k} {by_cls[k]}" for k in
             ("CATASTROPHIC", "CRIT", "MATERIAL", "SUSPECT", "YOY") if by_cls[k]))
    L.append("\nBY CODE:")
    for code, n in by_code.most_common():
        L.append(f"   {n:>5}  {code}")
    L.append("\nTOP 50 FINDINGS (index weight x severity) -- fix these first:")
    L.append(f"   {'wt%':>5} {'cik':>10} {'fy':>5}  {'severity':<13} {'code':<22} detail")
    for sc, wgt, cik, fy, cls, code, detail in findings[:50]:
        L.append(f"   {wgt:>5.2f} {cik:>10} {fy:>5}  {cls:<13} {code:<22} {detail}")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L[:80]))
    print(f"\n  -> {OUT.name} + {CSVOUT.name}")


if __name__ == "__main__":
    main()
