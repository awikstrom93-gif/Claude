"""
r2k_mezz_error_probe.py  --  diagnose the impossible MEZZANINE values the audit flagged (mezz>assets,
mezz<0). Uses the provenance column to show HOW each bad mezz was built, so we know whether it is a
legitimate large residual (temp equity > assets when permanent equity is deeply negative), a
double-count, or a memo/liquidation-preference tag wrongly swept in -- then fix reconstruct/gap-fill.

Reads fundamentals_dera.csv (redeemable_nci, total_assets/liabilities/equity, provenance).
Writes mezz_error_probe.txt.   RUN:  python r2k_mezz_error_probe.py
"""
from pathlib import Path
import os, csv, re
from collections import Counter, defaultdict

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "fundamentals_dera.csv"
OUT = BASE / "mezz_error_probe.txt"


def fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def mezz_prov(p):
    """pull just the redeemable_nci=... clause out of the provenance string."""
    for clause in (p or "").split(";"):
        if clause.strip().startswith("redeemable_nci="):
            return clause.strip()[len("redeemable_nci="):]
    return "(no mezz provenance -- primary capture)"


def main():
    over, neg = [], []
    for r in csv.DictReader(open(FUND, encoding="utf-8")):
        mz = fnum(r.get("redeemable_nci"))
        ta = fnum(r.get("total_assets"))
        if mz is None:
            continue
        if mz < 0:
            neg.append(r)
        elif ta is not None and ta > 0 and mz > ta:
            over.append(r)

    L = [f"MEZZANINE ERROR PROBE  --  {len(over)} mezz>assets, {len(neg)} mezz<0", ""]

    # mezz > assets: bucket by provenance kind
    L.append("=== mezz > assets (impossible unless permanent equity is deeply negative) ===")
    kinds = Counter()
    for r in over:
        pv = mezz_prov(r.get("provenance"))
        kind = ("residual A-L-E" if "A-L-E" in pv else
                "BS_FOOTS gap-fill" if "BS_FOOTS gap" in pv else
                "liquidation/memo?" if re.search(r"liquidation|preference", pv, re.I) else
                "named/primary")
        kinds[kind] += 1
    for k, n in kinds.most_common():
        L.append(f"   {n:>4}  {k}")
    L.append("\n   examples (mezz, assets, equity, liab, prov):")
    for r in sorted(over, key=lambda x: -(fnum(x.get("redeemable_nci")) or 0))[:15]:
        mz, ta = fnum(r["redeemable_nci"]), fnum(r["total_assets"])
        eq, tl = fnum(r.get("total_equity")), fnum(r.get("total_liabilities"))
        # is the residual self-consistent? A - L - E should equal mezz if it's the foot residual
        chk = "" if (eq is None or tl is None) else f"  A-L-E={ta-(tl or 0)-(eq or 0):,.0f}"
        L.append(f"     {r['cik']:>9} {r['fiscal_year']}  mezz={mz:,.0f} ta={ta:,.0f} eq={eq if eq is None else format(eq,',.0f')} "
                 f"tl={tl if tl is None else format(tl,',.0f')}{chk}")
        L.append(f"        prov: {mezz_prov(r.get('provenance'))}")

    # mezz < 0
    L.append("\n=== mezz < 0 (temporary equity carrying amount cannot be negative) ===")
    for r in sorted(neg, key=lambda x: fnum(x.get("redeemable_nci")) or 0)[:15]:
        L.append(f"     {r['cik']:>9} {r['fiscal_year']}  mezz={fnum(r['redeemable_nci']):,.0f}   "
                 f"prov: {mezz_prov(r.get('provenance'))}")
    OUT.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))
    print(f"\n  -> {OUT.name}")


if __name__ == "__main__":
    main()
