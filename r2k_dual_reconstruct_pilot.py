"""
============================================================
r2k_dual_reconstruct_pilot.py  --  DUAL RECONSTRUCTION PILOT.

For a small, deliberately HARD set of filings, rebuild each company's financial statements
TWICE -- once from our DERA facts (as-filed) and once from the Morningstar standardized
statements -- run the SAME accounting-identity battery on both, and emit a per-line 2x2
verdict (LOCKED / DEFINITIONAL_FORK / TRUST_DERA / TRUST_MS / SINGLE_SOURCE / MISSING).

The point is to prove -- on the names most likely to break -- that:
  (1) each source can be made to FOOT on its own (internal consistency), and
  (2) where two internally-consistent statements DISAGREE, we can tell a definitional fork
      (e.g. broker-dealer gross-vs-net revenue) from a real extraction error,
BEFORE any of this touches the IC numbers.

This is a DIAGNOSTIC. It writes nothing into the production fundamentals -- only a report.

WHY a pilot first: articulation only pins the lines an identity references ("pinned" lines:
revenue cascade, equity, cash, net income). It does NOTHING for "free" lines like total debt,
which hide inside total liabilities and foot regardless. Morningstar reports those as explicit
standardized components, so the dual build is also where we test debt reconciliation.

------------------------------------------------------------
INPUTS (all in the project folder; produced by the normal workflow):
  dera_facts.csv          reconstructed as-filed statement lines  (r2k_dera_extract.py)
  fundamentals_dera.csv   standardized DERA cascade + sector       (r2k_dera_classify.py)
  dera_filing_index.csv   filing metadata (name, sic, ifrs)        (r2k_dera_index.py)
  morningstar_long.csv    Morningstar standardized statements      (r2k_morningstar_parse.py)

OUTPUTS:
  dual_pilot_report.txt   human-readable, one block per filing (READ THIS)
  dual_pilot_lines.csv    machine-readable per-line verdicts

RUN:
  python r2k_dual_reconstruct_pilot.py                 # auto-pick a diverse hard set
  python r2k_dual_reconstruct_pilot.py --targets 717423:2021,1234567:2020
  python r2k_dual_reconstruct_pilot.py --selftest      # synthetic proof of the identity+verdict engine
============================================================
"""
from pathlib import Path
import os, csv, sys
from collections import defaultdict

# reuse the PRODUCTION accounting brain so the DERA side is identical to what ships
from r2k_dera_classify import classify_filing, detect_sector, first, fnum, OPEX

BASE = Path(os.environ.get("R2KG_BASE", "."))
FACTS = BASE / "dera_facts.csv"
FUND = BASE / "fundamentals_dera.csv"
INDEX = BASE / "dera_filing_index.csv"
MS = BASE / "morningstar_long.csv"
REPORT = BASE / "dual_pilot_report.txt"
LINES = BASE / "dual_pilot_lines.csv"

# identity tie tolerance (internal footing) and cross-source agreement tolerance (looser:
# vendors round, and as-filed vs standardized legitimately differ by small reclassifications)
TOL_REL, TOL_ABS = 0.005, 5000.0
AGREE_REL, AGREE_ABS = 0.01, 100000.0

# headline lines we render a verdict for (standardized role -> label)
HEADLINE = [
    ("revenue", "Revenue"), ("gross_profit", "Gross profit"),
    ("operating_income", "Operating income"), ("pretax", "Pretax income"),
    ("ni_parent", "Net income (parent)"), ("total_assets", "Total assets"),
    ("total_liabilities", "Total liabilities"), ("total_equity", "Total equity"),
    ("bs_cash", "Cash (BS)"), ("cfo", "Cash from operations"), ("total_debt", "Total debt"),
]

# ------------------------------------------------------------------ identity engine
def _close(a, b, rel=TOL_REL, ab=TOL_ABS):
    return abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


def compute_identities(S, sector):
    """One identity battery, applied identically to a DERA or a Morningstar statement dict S.
    Returns list of (name, status, residual). status in {tie, BREAK, n/a}."""
    out = []

    def tie(name, lhs, rhs):
        if lhs is None or rhs is None:
            out.append((name, "n/a", None)); return
        out.append((name, "tie" if _close(lhs, rhs) else "BREAK", lhs - rhs))

    g = S.get
    mezz = g("mezz") or 0
    # ---- balance sheet ----
    tie("BS_FOOTS(A=L+E+mezz)", g("total_assets"),
        None if (g("total_liabilities") is None or g("total_equity") is None)
        else g("total_liabilities") + g("total_equity") + mezz)
    tie("BS_EQUITY(E=parent+NCI)", g("total_equity"),
        None if (g("parent_equity") is None or g("nci_bs") is None)
        else g("parent_equity") + g("nci_bs"))
    # ---- income statement ----
    if sector == "commercial":
        tie("IS_GP(GP=Rev-COGS)", g("gross_profit"),
            None if (g("revenue") is None or g("cogs") is None) else g("revenue") - g("cogs"))
        tie("IS_OI(OI=GP-OpEx)", g("operating_income"),
            None if (g("gross_profit") is None or g("opex") is None)
            else g("gross_profit") - g("opex"))
    tie("IS_NI(Consol=Pretax-Tax+Disc)", g("ni_consol"),
        None if (g("pretax") is None or g("tax") is None)
        else g("pretax") - g("tax") + (g("disc_ops") or 0))
    tie("IS_NCI(Consol-Parent=NCI)",
        None if (g("ni_consol") is None or g("ni_parent") is None) else g("ni_consol") - g("ni_parent"),
        g("nci_is"))
    # ---- cash flow (the articulation legs the production engine does NOT yet check) ----
    # CF_FOOT carries restricted-cash reclassification noise, so judge it on MATERIALITY (1% or
    # $2M) rather than the tight footing tolerance -- a few $M on a multi-$B entity is not a break.
    cf_lhs = (None if (g("cfo") is None or g("cfi") is None or g("cff") is None)
              else g("cfo") + g("cfi") + g("cff") + (g("fx") or 0))
    cf_rhs = g("change_in_cash")
    if cf_lhs is None or cf_rhs is None:
        out.append(("CF_FOOT(CFO+CFI+CFF+FX=dCash)", "n/a", None))
    else:
        ok = _close(cf_lhs, cf_rhs, rel=0.01, ab=2_000_000.0)
        out.append(("CF_FOOT(CFO+CFI+CFF+FX=dCash)", "tie" if ok else "BREAK", cf_lhs - cf_rhs))
    tie("CF_CASH(begin+dCash=end)",
        None if (g("cash_begin") is None or g("change_in_cash") is None)
        else g("cash_begin") + g("change_in_cash"), g("cash_end"))
    tie("CF_BS_CASH(end=BS cash)", g("cash_end"), g("bs_cash"))
    tie("CF_NI(NI top of CF=Consol NI)", g("ni_cf"), g("ni_consol"))
    # ---- debt sanity (one-sided: total debt cannot exceed total liabilities) ----
    td, tl = g("total_debt"), g("total_liabilities")
    if td is None or tl is None:
        out.append(("DEBT_SANITY(debt<=liab)", "n/a", None))
    else:
        out.append(("DEBT_SANITY(debt<=liab)", "tie" if td <= tl * (1 + TOL_REL) else "BREAK", td - tl))
    return out


# the robust subset that decides "is this source internally consistent?" -- IS_OI is excluded
# because a grand-total OperatingExpenses line (common) makes GP-OpEx fail even when OI is right;
# CF_BS_CASH / CF_NI / DEBT_SANITY are shown but informational (restricted-cash inclusion and
# consolidated-vs-parent NI legitimately move them without the statement being "wrong").
CORE_IDENTITIES = {
    "BS_FOOTS(A=L+E+mezz)", "BS_EQUITY(E=parent+NCI)", "IS_GP(GP=Rev-COGS)",
    "IS_NI(Consol=Pretax-Tax+Disc)", "IS_NCI(Consol-Parent=NCI)",
    "CF_FOOT(CFO+CFI+CFF+FX=dCash)", "CF_CASH(begin+dCash=end)",
}


def ties_clean(idents):
    """source is internally consistent if no CORE identity BREAKs."""
    return not any(s == "BREAK" and n in CORE_IDENTITIES for n, s, _ in idents)


# ------------------------------------------------------------------ DERA side
# raw-tag fallbacks for the cash-flow articulation lines classify_filing doesn't expose
FX_TAGS = ["EffectOfExchangeRateOnCashAndCashEquivalents",
           "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
           "EffectOfExchangeRateOnCashAndCashEquivalentsContinuingOperations"]
DCASH_TAGS = ["CashAndCashEquivalentsPeriodIncreaseDecrease",
              "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
              "CashAndCashEquivalentsPeriodIncreaseDecreaseExcludingExchangeRateEffect"]


def dera_statement(d):
    """d = {tag: value} for one filing. Returns (sector, statement dict S, prov, dera_idents)."""
    sector = detect_sector(d)
    r, prov, _ = classify_filing(d, sector)
    opex, _ = first(d, *OPEX)
    fx, _ = first(d, *FX_TAGS)
    dcash, _ = first(d, *DCASH_TAGS)
    S = dict(
        revenue=r.get("revenue"), cogs=r.get("cost_of_revenue"), gross_profit=r.get("gross_profit"),
        opex=opex, operating_income=r.get("operating_income"), pretax=r.get("pretax_income"),
        tax=r.get("tax_expense"), disc_ops=r.get("discontinued_operations"),
        ni_consol=r.get("net_income_consolidated"), ni_parent=r.get("net_income"),
        nci_is=r.get("minority_interest"),
        total_assets=r.get("total_assets"), total_liabilities=r.get("total_liabilities"),
        parent_equity=r.get("parent_equity"), nci_bs=r.get("minority_interest_bs"),
        total_equity=r.get("total_equity"), mezz=r.get("redeemable_nci"), bs_cash=r.get("cash"),
        cfo=r.get("cfo"), cfi=r.get("cfi"), cff=r.get("cff"), fx=fx, change_in_cash=dcash,
        # DERA keeps only current-period facts, so beginning cash / a distinct CF net-income line
        # are not retained -> those articulation legs are n/a on the DERA side (a known gap the
        # pilot is meant to surface, not paper over).
        cash_begin=None, cash_end=None, ni_cf=None,
        total_debt=r.get("total_debt"),
    )
    return sector, S, prov, compute_identities(S, sector)


# ------------------------------------------------------------------ Morningstar side
def _pick(m, *names):
    """first present Morningstar metric among names -> (value, name)."""
    for n in names:
        if n in m and m[n] is not None:
            return m[n], n
    return None, None


def _sum_present(m, *names):
    parts = [m[n] for n in names if n in m and m[n] is not None]
    return (sum(parts) if parts else None), [n for n in names if n in m and m[n] is not None]


def ms_statement(m):
    """m = {metric: value} for one company-year of Morningstar standardized statements.

    Morningstar's IS is SIGNED: costs, opex, tax, and the NCI line are stored NEGATIVE, and the
    cascade is additive (Rev + COGS = GP; GP + OpEx = OI; Pretax + Tax = NI). We normalize those
    to DERA's convention (costs/tax/NCI POSITIVE) so one identity battery serves both sources."""
    def neg(v):
        return None if v is None else -v

    rev, _ = _pick(m, "Total Revenue", "Business Revenue", "Unadjusted Revenue")
    cogs = neg(_pick(m, "Cost Of Revenue", "Cost Of Goods And Services")[0])   # ->positive
    gp, _ = _pick(m, "Gross Profit")
    opex = neg(_pick(m, "Operating Income Expenses")[0])                       # ->positive
    oi, _ = _pick(m, "Total Operating Profit Loss")
    pretax, _ = _pick(m, "Pretax Income")
    tax = neg(_pick(m, "Provision For Income Tax")[0])                         # ->positive
    disc, _ = _pick(m, "Discontinued Operations")
    parent, _ = _pick(m, "Net Income After Non Controlling Minority Interests",
                      "Net Income Available To Common Stockholders")
    nci_is = neg(_pick(m, "Net Income attributable to Non-controlling/ Minority Interests")[0])
    # consolidated NI (incl NCI): Morningstar's "Net Income From Continuing Operations" is struck
    # BEFORE removing NCI, so it is the consolidated figure; add discontinued ops for the total.
    nicont, _ = _pick(m, "Net Income From Continuing Operations")
    if nicont is not None:
        consol = nicont + (disc or 0)
    elif parent is not None:
        consol = parent + (nci_is or 0)
    else:
        consol = None
    # balance sheet
    ta, _ = _pick(m, "Total Assets")
    tl, _ = _pick(m, "Total Liabilities")
    te, _ = _pick(m, "Total Equity", "Total Partnership Capital")
    peq, _ = _pick(m, "Equity Attributable To Parent Stockholders")
    ncibs, _ = _pick(m, "Non Controlling Minority Interests")
    bscash, _ = _pick(m, "Cash And Cash Equivalents", "Cash", "Cash&Cash Equivalents And Short Term Investments")
    # cash flow
    cfo, _ = _pick(m, "Cash Flow From Operating Activities Indirect",
                   "Net Cash Flow From Continuing Operating Activities Indirect",
                   "Cash Generated From Operating Activities")
    cfi, _ = _pick(m, "Cash Flow From Investing Activities", "Cash Flow From Continuing Investing Activities")
    cff, _ = _pick(m, "Cash Flow From Financing Activities", "Cash Flow From Continuing Financing Activities")
    fx, _ = _pick(m, "Effect Of Exchange Rate Changes")
    dcash, _ = _pick(m, "Change In Cash")
    cbeg, _ = _pick(m, "Cash And Cash Equivalents Beginning Of Period")
    cend, _ = _pick(m, "Cash And Cash Equivalents End Of Period")
    # debt: prefer the lease-combined lines to avoid double counting, else sum components
    lt, lt_parts = (m.get("Long Term Debt And Capital Lease Obligation"),
                    ["Long Term Debt And Capital Lease Obligation"]) \
        if m.get("Long Term Debt And Capital Lease Obligation") is not None \
        else _sum_present(m, "Long Term Debt", "Capital Lease Obligations Non Current")
    cur, cur_parts = (m.get("Current Debt And Capital Lease Obligation"),
                      ["Current Debt And Capital Lease Obligation"]) \
        if m.get("Current Debt And Capital Lease Obligation") is not None \
        else _sum_present(m, "Current Debt", "Current Portion Of Long Term Debt And Capital Lease",
                          "Capital Lease Obligations Current")
    debt_parts = [x for x in (lt, cur) if x is not None]
    total_debt = sum(debt_parts) if debt_parts else None

    S = dict(
        revenue=rev, cogs=cogs, gross_profit=gp, opex=opex, operating_income=oi, pretax=pretax,
        tax=tax, disc_ops=disc, ni_consol=consol, ni_parent=parent, nci_is=nci_is,
        total_assets=ta, total_liabilities=tl, parent_equity=peq, nci_bs=ncibs, total_equity=te,
        mezz=None, bs_cash=bscash, cfo=cfo, cfi=cfi, cff=cff, fx=fx, change_in_cash=dcash,
        cash_begin=cbeg, cash_end=cend, ni_cf=consol, total_debt=total_debt,   # CF NI is consolidated
    )
    S["_debt_components"] = (lt_parts or []) + (cur_parts or [])
    return S


# ------------------------------------------------------------------ verdict
def verdict(line, a, b, dera_ok, ms_ok):
    if a is None and b is None:
        return "MISSING", ""
    if a is None:
        return "SINGLE_SOURCE(MS only)", ""
    if b is None:
        return "SINGLE_SOURCE(DERA only)", ""
    if _close(a, b, AGREE_REL, AGREE_ABS):
        return "LOCKED", ""
    # disagree -> arbitrate by which source's statement is internally consistent
    diff = a - b
    pct = (diff / b * 100) if b else float("inf")
    note = f"DERA {a:,.0f} vs MS {b:,.0f}  ({pct:+.1f}%)"
    if dera_ok and ms_ok:
        return "DEFINITIONAL_FORK", note          # both foot but differ -> measuring differently
    if dera_ok and not ms_ok:
        return "TRUST_DERA", note                 # MS statement doesn't foot -> ours is the lead
    if ms_ok and not dera_ok:
        return "TRUST_MS", note                   # our statement doesn't foot -> MS is the lead
    return "BOTH_BREAK_REVIEW", note


# ------------------------------------------------------------------ loaders
def _ms_cols(header):
    """index of the columns we need, by name (robust to column reordering)."""
    h = {name: i for i, name in enumerate(header)}
    return h["cik"], h["fiscal_year"], h["period_end"], h["metric"], h["value"]


def ms_coverage():
    """set of (cik, fy) Morningstar covers -- fast indexed scan (no DictReader)."""
    keys = set()
    if not MS.exists():
        return keys
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        ci, fi, _, _, _ = _ms_cols(next(r))
        for row in r:
            if len(row) > fi:
                keys.add((row[ci], row[fi]))
    return keys


def load_ms(target_ciks):
    """{(cik, fy): {metric: value}} restricted to target ciks; collapse to latest period_end
    per (cik, fy, metric). Fast indexed scan."""
    out = defaultdict(dict)
    pe = defaultdict(dict)
    if not MS.exists():
        return out
    with open(MS, newline="", encoding="utf-8", errors="replace") as f:
        r = csv.reader(f)
        ci, fi, pi, mi, vi = _ms_cols(next(r))
        for row in r:
            if len(row) <= vi or row[ci] not in target_ciks:
                continue
            v = fnum(row[vi])
            if v is None:
                continue
            key = (row[ci], row[fi])
            metric, ped = row[mi], row[pi]
            if pe[key].get(metric) is None or ped >= pe[key][metric]:   # latest period_end wins
                out[key][metric] = v
                pe[key][metric] = ped
    return out


def load_dera_facts(target_ciks):
    """{(cik, fy): {tag: value}} for target ciks, streamed."""
    by = defaultdict(dict)
    if not FACTS.exists():
        raise SystemExit(f"!! {FACTS.name} not found -- run r2k_dera_extract.py first.")
    with open(FACTS, newline="", encoding="utf-8") as f:
        for d in csv.DictReader(f):
            if d.get("cik") not in target_ciks:
                continue
            v = fnum(d.get("value"))
            if v is None:
                continue
            by[(d["cik"], d["fiscal_year"])][d["tag"]] = v
    return by


def load_index():
    idx = {}
    if INDEX.exists():
        for d in csv.DictReader(open(INDEX, encoding="utf-8")):
            idx[d.get("cik")] = d
    return idx


# ------------------------------------------------------------------ target selection
def auto_targets(ms_ciks):
    """Pick a diverse, deliberately HARD set from fundamentals_dera, intersected with MS coverage.
    Categories: broker-dealer (by name), bank, insurer, IFRS, multi-segment industrial (big
    commercial), discontinued-ops + NCI. Falls back gracefully if the data is thin."""
    if not FUND.exists():
        return []
    rows = list(csv.DictReader(open(FUND, encoding="utf-8")))
    idx = load_index()
    BROKER_HINT = ("STONEX", "FCSTONE", "INTL FCSTONE", "SECURITIES", "BROKER", "MARKETAXESS",
                   "VIRTU", "LPL", "RAYMOND JAMES", "PIPER", "COWEN", "JEFFERIES")

    def covered(r):
        return (r["cik"], r["fiscal_year"]) in ms_ciks   # ms_ciks is already a set

    picks, seen = [], set()

    def take(label, r):
        k = (r["cik"], r["fiscal_year"])
        if k in seen:
            return False
        seen.add(k); picks.append((label, r["cik"], r["fiscal_year"])); return True

    def latest_for(pred, label, n=1):
        cands = [r for r in rows if covered(r) and pred(r)]
        cands.sort(key=lambda r: r["fiscal_year"], reverse=True)
        got = 0
        for r in cands:
            if take(label, r):
                got += 1
            if got >= n:
                break

    def name_of(cik):
        return (idx.get(cik, {}).get("name", "") or "").upper()

    latest_for(lambda r: any(h in name_of(r["cik"]) for h in BROKER_HINT), "broker-dealer")
    latest_for(lambda r: r["sector"] == "bank", "bank")
    latest_for(lambda r: r["sector"] == "insurer", "insurer")
    latest_for(lambda r: r["taxonomy"] == "ifrs", "IFRS filer")
    latest_for(lambda r: r["discontinued_operations"] not in ("", None)
               and r["minority_interest"] not in ("", None), "disc-ops + NCI")
    latest_for(lambda r: r["sector"] == "commercial" and fnum(r["revenue"]) is not None
               and fnum(r["revenue"]) > 5e9, "large commercial", n=2)
    # pad to ~10 with the biggest covered commercial names not already picked
    latest_for(lambda r: r["sector"] == "commercial" and fnum(r["revenue"]) is not None
               and fnum(r["revenue"]) > 1e9, "commercial", n=4)
    return picks[:10]


def parse_targets_arg():
    for a in sys.argv:
        if a.startswith("--targets"):
            val = a.split("=", 1)[1] if "=" in a else (sys.argv[sys.argv.index(a) + 1])
            out = []
            for tok in val.split(","):
                cik, fy = tok.split(":")
                out.append(("manual", cik.strip(), fy.strip()))
            return out
    return None


# ------------------------------------------------------------------ render
def fmt(v):
    return "" if v is None else f"{v:,.0f}"


def render(rep, label, cik, fy, name, sector, S_d, id_d, S_m, id_m):
    dera_ok, ms_ok = ties_clean(id_d), ties_clean(id_m)
    rep.append("=" * 92)
    rep.append(f"[{label}]  CIK {cik}  FY{fy}  {name}   sector={sector}")
    rep.append(f"   DERA internally consistent: {'YES' if dera_ok else 'NO'}    "
               f"Morningstar internally consistent: {'YES' if ms_ok else 'NO'}")
    # identity residuals, side by side
    rep.append("   " + "-" * 88)
    rep.append(f"   {'IDENTITY':<34}{'DERA':>14}{'  ':2}{'MORNINGSTAR':>14}")
    dmap = {n: (s, r) for n, s, r in id_d}
    mmap = {n: (s, r) for n, s, r in id_m}
    order = [x[0] for x in id_d] + [x[0] for x in id_m if x[0] not in dmap]  # union, DERA order first
    for n in order:
        ds, dr = dmap.get(n, ("n/a", None))
        msr = mmap.get(n, ("n/a", None))
        dtxt = ds if dr is None else f"{ds} ({dr:,.0f})"
        mtxt = msr[0] if msr[1] is None else f"{msr[0]} ({msr[1]:,.0f})"
        rep.append(f"   {n:<34}{dtxt:>14}{'  ':2}{mtxt:>14}")
    # per-line verdicts
    rep.append("   " + "-" * 88)
    rep.append(f"   {'LINE':<22}{'DERA':>16}{'MORNINGSTAR':>16}   VERDICT")
    line_rows = []
    for role, lbl in HEADLINE:
        a, b = S_d.get(role), S_m.get(role)
        vd, note = verdict(role, a, b, dera_ok, ms_ok)
        rep.append(f"   {lbl:<22}{fmt(a):>16}{fmt(b):>16}   {vd}")
        if note:
            rep.append(f"   {'':<22}{'':>16}{'':>16}   -> {note}")
        line_rows.append(dict(label=label, cik=cik, fiscal_year=fy, name=name, sector=sector,
                              line=role, dera_value=("" if a is None else a),
                              ms_value=("" if b is None else b),
                              dera_internally_consistent=dera_ok, ms_internally_consistent=ms_ok,
                              verdict=vd, note=note))
    if S_m.get("_debt_components"):
        rep.append(f"   debt components (MS): {', '.join(S_m['_debt_components'])}")
    rep.append("")
    return line_rows


# ------------------------------------------------------------------ main
def main():
    manual = parse_targets_arg()
    if manual:
        targets = manual
    else:
        targets = auto_targets(ms_coverage())   # only pick names Morningstar can be compared on
        if not targets:
            raise SystemExit("!! could not auto-select targets (need fundamentals_dera.csv + "
                             "morningstar_long.csv). Pass --targets cik:fy,...")
    target_ciks = {c for _, c, _ in targets}
    ms = load_ms(target_ciks)

    facts = load_dera_facts(target_ciks)
    idx = load_index()

    rep = [
        "DUAL RECONSTRUCTION PILOT  --  DERA (as-filed) vs Morningstar (standardized)",
        "Each filing rebuilt from both sources; same identity battery on both; per-line verdict.",
        "VERDICTS: LOCKED (agree) | DEFINITIONAL_FORK (both foot, differ) | TRUST_DERA / TRUST_MS",
        "          (only one foots) | BOTH_BREAK_REVIEW | SINGLE_SOURCE | MISSING",
        "",
    ]
    all_lines = []
    for label, cik, fy in targets:
        name = idx.get(cik, {}).get("name", "")
        d = facts.get((cik, fy))
        m = ms.get((cik, fy))
        if not d and not m:
            rep.append(f"[{label}] CIK {cik} FY{fy}: no data in either source -- skipped\n")
            continue
        if d:
            sector, S_d, _, id_d = dera_statement(d)
        else:
            sector, S_d, id_d = "?", {}, []
        if m:
            S_m = ms_statement(m)
            id_m = compute_identities(S_m, sector if sector != "?" else "commercial")
        else:
            S_m, id_m = {}, []
        all_lines += render(rep, label, cik, fy, name, sector, S_d, id_d, S_m, id_m)

    REPORT.write_text("\n".join(rep), encoding="utf-8")
    if all_lines:
        with open(LINES, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_lines[0].keys()))
            w.writeheader(); w.writerows(all_lines)
    # console summary
    from collections import Counter
    vc = Counter(r["verdict"] for r in all_lines)
    print(f"  -> {REPORT.name}  ({len(targets)} filings)")
    print(f"  -> {LINES.name}  ({len(all_lines)} line-verdicts)")
    print(f"  filings: " + ", ".join(f"{lbl}:{cik}/FY{fy}" for lbl, cik, fy in targets))
    print(f"  verdict mix: {dict(vc)}")


# ------------------------------------------------------------------ selftest
def selftest():
    # an industrial that foots on both, but DERA shows as-filed gross revenue while MS nets it
    # (a definitional fork on revenue) -- and a debt line MS has but DERA understates. Values in
    # realistic dollars (millions) so the cross-source absolute tolerance behaves as it will live.
    M = 1_000_000
    S_dera = dict(revenue=1000*M, cogs=600*M, gross_profit=400*M, opex=300*M, operating_income=100*M,
                  pretax=90*M, tax=20*M, disc_ops=0, ni_consol=70*M, ni_parent=65*M, nci_is=5*M,
                  total_assets=5000*M, total_liabilities=3000*M, parent_equity=1900*M, nci_bs=100*M,
                  total_equity=2000*M, mezz=0, bs_cash=300*M, cfo=150*M, cfi=-40*M, cff=-90*M, fx=0,
                  change_in_cash=20*M, cash_begin=None, cash_end=None, ni_cf=None, total_debt=600*M)
    S_ms = dict(revenue=820*M, cogs=420*M, gross_profit=400*M, opex=300*M, operating_income=100*M,
                pretax=90*M, tax=20*M, disc_ops=0, ni_consol=70*M, ni_parent=65*M, nci_is=5*M,
                total_assets=5000*M, total_liabilities=3000*M, parent_equity=1900*M, nci_bs=100*M,
                total_equity=2000*M, mezz=0, bs_cash=300*M, cfo=150*M, cfi=-40*M, cff=-90*M, fx=0,
                change_in_cash=20*M, cash_begin=280*M, cash_end=300*M, ni_cf=70*M, total_debt=850*M)
    id_d = compute_identities(S_dera, "commercial")
    id_m = compute_identities(S_ms, "commercial")
    dok, mok = ties_clean(id_d), ties_clean(id_m)
    print(f"  DERA foots: {dok}   MS foots: {mok}")
    checks = []
    # both foot, revenue differs -> DEFINITIONAL_FORK
    v, _ = verdict("revenue", S_dera["revenue"], S_ms["revenue"], dok, mok)
    checks.append(("revenue->FORK", v == "DEFINITIONAL_FORK"))
    # assets agree -> LOCKED
    v, _ = verdict("total_assets", 5000*M, 5000*M, dok, mok)
    checks.append(("assets->LOCKED", v == "LOCKED"))
    # debt differs, both foot (debt not in footing identities) -> FORK (flags the free-line gap)
    v, _ = verdict("total_debt", 600*M, 850*M, dok, mok)
    checks.append(("debt->FORK", v == "DEFINITIONAL_FORK"))
    # if DERA did NOT foot, a disagreement should defer to MS
    v, _ = verdict("revenue", 820*M, 1000*M, False, True)
    checks.append(("one-foots->TRUST_MS", v == "TRUST_MS"))
    # CF_CASH ties on MS (begin+dCash=end) but n/a on DERA
    cf_ms = {n: s for n, s, _ in id_m}
    checks.append(("MS CF_CASH ties", cf_ms.get("CF_CASH(begin+dCash=end)") == "tie"))
    cf_d = {n: s for n, s, _ in id_d}
    checks.append(("DERA CF_CASH n/a", cf_d.get("CF_CASH(begin+dCash=end)") == "n/a"))
    ok = all(p for _, p in checks)
    for nm, p in checks:
        print(f"     {'PASS' if p else 'FAIL'}  {nm}")
    print(f"\n  SELFTEST: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
