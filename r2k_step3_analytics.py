"""
============================================================
r2k_step3_analytics.py  --  Russell 2000 Growth index-quality analytics
US Small Cap Growth asset-class review
============================================================
Consumes the validated as-filed dataset + point-in-time membership and produces a
charted workbook answering the mandate: how the benchmark's quality/composition has
evolved 2015-2026, and where that created headwinds for quality-disciplined managers.

INPUTS  (Benchmark Analysis folder)
    edgar_annual_fundamentals_ASFILED.csv   (Step 2 -- as-filed, validated)
    Russell_2000_Growth_Holdings_*.xlsx     (membership, weight, GICS/Morningstar)
    temporal_cik_map.json / security_cik_map.json

OUTPUT
    Russell2000Growth_Analytics.xlsx
      README, Coverage(by weight), Index Quality Trends, Quality Lenses, DuPont,
      Profitability Cohorts, Biotech, Concentration, Composition Change,
      Constituent Detail, Charts

POINT-IN-TIME: each constituent's FY0 = latest fiscal year whose 10-K was actually
filed before the annual snapshot (SNAP_MONTH, June by default; uses filed_date, falls back to FYE+90d). No look-ahead.
RATIOS: ROE/ROA/ROIC use AVERAGE (opening+closing) denominators (CFA convention).
============================================================
"""
from pathlib import Path
from datetime import date, timedelta
from statistics import median
import json, csv, re, os

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference
from openpyxl.utils import get_column_letter

BASE = Path(os.environ.get("R2KG_BASE", "."))
FUND = BASE / "edgar_annual_fundamentals_ASFILED.csv"
CIK_MAP, TEMPORAL = BASE / "security_cik_map.json", BASE / "temporal_cik_map.json"
OUT = BASE / "Russell2000Growth_Analytics.xlsx"
FILING_LAG = 90
BIOTECH = {"Biotechnology"}
TOP_NS = [5, 10, 25, 50]
FIRST_RELIABLE_YEAR = 2012
WINSOR = (-2.0, 5.0)          # clamp per-name ratios before weight-averaging
ROIC_CLAMP = 5.0


# ---------- helpers ----------
def to_f(x):
    try: return float(x)
    except (TypeError, ValueError): return None

def ntk(t): return re.sub(r"[^A-Z0-9]", "", str(t or "").upper())

def safe_div(a, b): return a / b if (a is not None and b not in (None, 0)) else None

def avg(a, b): return (a + b) / 2 if (a is not None and b is not None) else a

def cagr(end, beg, n):
    return (end / beg) ** (1.0 / n) - 1.0 if (end and beg and beg > 0 and end > 0 and n > 0) else None

def eff_tax(tax, pretax):
    if tax is not None and pretax is not None and pretax > 0:
        return max(0.0, min(0.35, tax / pretax))
    return 0.21

def quantile(sv, q):
    if not sv: return None
    n = len(sv)
    if n == 1: return sv[0]
    pos = q * (n - 1); lo = int(pos); frac = pos - lo
    return sv[lo] * (1 - frac) + sv[lo + 1] * frac if lo + 1 < n else sv[lo]

def aggregate(pairs, winsor=False):
    """pairs: [(value, weight)]. Returns wavg / median / q1 / q3 / n."""
    vals = [v for v, w in pairs if v is not None]
    wp = [(v, w) for v, w in pairs if v is not None and w is not None]
    out = {"n": len(vals), "wavg": None, "median": None, "q1": None, "q3": None}
    if vals:
        sv = sorted(vals)
        out["median"], out["q1"], out["q3"] = median(sv), quantile(sv, .25), quantile(sv, .75)
    if wp:
        tw = sum(w for _, w in wp)
        if tw > 0:
            if winsor:
                lo, hi = WINSOR
                out["wavg"] = sum(max(lo, min(hi, v)) * w for v, w in wp) / tw
            else:
                out["wavg"] = sum(v * w for v, w in wp) / tw
    return out

def dollar_agg(nd):
    sn = sum(n for n, d in nd if n is not None and d is not None)
    sd = sum(d for n, d in nd if n is not None and d is not None)
    return safe_div(sn, sd)


# ---------- loaders ----------
def load_fundamentals():
    facts = {}
    with open(FUND, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            cik = str(r["cik"]); yr = to_f(r["fiscal_year"])
            if yr is None: continue
            yr = int(yr)
            fye = filed = None
            if r.get("fye_date"):
                try: fye = date.fromisoformat(r["fye_date"][:10])
                except ValueError: pass
            if r.get("filed_date"):
                try: filed = date.fromisoformat(r["filed_date"][:10])
                except ValueError: pass
            facts.setdefault(cik, {})[yr] = {k: to_f(r.get(k)) for k in
                ("revenue","net_income","operating_income","gross_profit","tax_expense","pretax_income",
                 "stockholders_equity","total_assets","cash","short_term_investments","long_term_investments",
                 "restricted_cash","total_debt","operating_cash_flow","capex","free_cash_flow",
                 "ebitda","interest_expense")}   # for the solvency tab -> panel-sourced, single vintage
            facts[cik][yr].update(fye=fye, filed=filed, sector=r.get("sector","general"))
    return facts

def find_holdings():
    c = list(BASE.glob("*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx")) or list(BASE.glob("*[Hh]olding*.xlsx"))
    if not c: raise FileNotFoundError("holdings workbook not found")
    return c[0]

def snapshot_year(sn):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", str(sn))
    return int("20" + m.group(3)) if m and len(m.group(3)) == 2 else (int(m.group(3)) if m else None)

def load_membership():
    wb = openpyxl.load_workbook(find_holdings(), read_only=True, data_only=True)
    snaps = {}
    for sn in wb.sheetnames:
        yr = snapshot_year(sn)
        if yr is None: continue
        ws = wb[sn]; hdr = None; idx = {}; rows = []
        for r in ws.iter_rows(values_only=True):
            if hdr is None:
                hdr = [str(c).strip() if c else "" for c in r]; idx = {h: i for i, h in enumerate(hdr)}
                if "Ticker" not in idx: hdr = None
                continue
            def g(col):
                i = idx.get(col); return r[i] if (i is not None and i < len(r) and r[i] is not None) else None
            tk = g("Ticker")
            if tk is None: continue
            cikv = g("CIK")   # the index provider's own point-in-time CIK -- authoritative when present
            cik_file = str(int(to_f(cikv))).zfill(10) if (cikv is not None and to_f(cikv) is not None) else None
            rows.append({"nt": ntk(tk), "ticker": str(tk).strip(), "name": str(g("Name") or ""),
                "cik_file": cik_file,
                "weight": to_f(g("Portfolio Weighting %")) or 0.0, "gics": str(g("GICS Sector") or ""),
                "ms_sector": str(g("Morningstar Sector") or ""), "ms_industry": str(g("Morningstar Industry") or "")})
        snaps[yr] = rows
    wb.close()
    return snaps

def load_maps():
    base = {}
    if CIK_MAP.exists():
        for t, m in json.load(open(CIK_MAP)).items():
            base[ntk(t)] = (m["cik"] if isinstance(m, dict) else m)
    temporal = json.load(open(TEMPORAL)) if TEMPORAL.exists() else {}
    return base, temporal

def _cik_in_facts(facts, cand):
    """Return whichever string form of a candidate CIK is a key in facts, else None."""
    if not cand: return None
    forms = (str(cand), str(cand).zfill(10))
    if str(cand).isdigit():
        forms = forms + (str(int(cand)),)
    for form in forms:
        if form in facts: return form
    return None

def resolve_cik(facts, base, temporal, raw, nt, skey, cik_file=None):
    # The holdings file's own CIK column is the index provider's point-in-time designation --
    # prefer it over re-deriving from the ticker maps (which mis-maps reused tickers, e.g. CZR/BBBY
    # in 2016 resolved to the wrong Caesars/Bed-Bath entity and swung index NI by ~$6.7B).
    hit = _cik_in_facts(facts, cik_file)
    if hit: return hit
    cand = (temporal.get(skey, {}).get(raw) or temporal.get(skey, {}).get(raw.upper()) or base.get(nt))
    return _cik_in_facts(facts, cand)

def pick_fy0(cf, snap_dt):
    cutoff = snap_dt - timedelta(days=FILING_LAG)
    elig = []
    for y, r in cf.items():
        if r.get("filed"):
            if r["filed"] <= snap_dt: elig.append(y)
        elif r.get("fye") and r["fye"] <= cutoff: elig.append(y)
    if elig: return max(elig)
    cand = [y for y in cf if y <= snap_dt.year - 1]
    return max(cand) if cand else None


# ---------- per-company metrics ----------
def company_metrics(cf, fy0):
    r0, r1, r2, r3 = cf.get(fy0, {}), cf.get(fy0-1, {}), cf.get(fy0-2, {}), cf.get(fy0-3, {})
    g = r0.get
    rev, ni, oi, gp = g("revenue"), g("net_income"), g("operating_income"), g("gross_profit")
    eq, ta, cash, debt = g("stockholders_equity"), g("total_assets"), g("cash"), g("total_debt")
    cfo, fcf = g("operating_cash_flow"), g("free_cash_flow")
    if debt is None and eq is not None: debt = 0.0          # debt-free treatment
    aeq, ata = avg(eq, r1.get("stockholders_equity")), avg(ta, r1.get("total_assets"))
    m = {"fy0": fy0, "revenue": rev, "net_income": ni, "operating_income": oi, "gross_profit": gp,
         "equity": eq, "assets": ta, "cash": cash, "debt": debt, "cfo": cfo, "fcf": fcf,
         "sector": r0.get("sector", "general")}
    # growth
    m["rev_yoy"] = (rev / r1["revenue"] - 1) if (rev and r1.get("revenue") and r1["revenue"] > 0) else None
    m["rev_cagr3"] = cagr(rev, r3.get("revenue"), 3)
    m["ni_yoy"] = (ni / r1["net_income"] - 1) if (ni is not None and r1.get("net_income") and r1["net_income"] > 0) else None
    # margins
    m["gross_margin"] = safe_div(gp, rev) if (rev and rev > 0) else None
    m["op_margin"] = safe_div(oi, rev) if (rev and rev > 0) else None
    m["net_margin"] = safe_div(ni, rev) if (rev and rev > 0) else None
    m["fcf_margin"] = safe_div(fcf, rev) if (rev and rev > 0) else None
    # returns (average denominators)
    m["roe"] = safe_div(ni, aeq) if (aeq and aeq > 0) else None
    m["roa"] = safe_div(ni, ata) if (ata and ata > 0) else None
    nopat = ic = roic = None
    if oi is not None and eq is not None:
        ic0 = debt + eq - (cash or 0.0); ic1 = (r1.get("total_debt") or 0) + (r1.get("stockholders_equity") or 0) - (r1.get("cash") or 0)
        ic = avg(ic0, ic1) if r1.get("stockholders_equity") is not None else ic0
        if ic and ic > 0:
            nopat = oi * (1 - eff_tax(g("tax_expense"), g("pretax_income")))
            roic = safe_div(nopat, ic)
            if roic is not None and abs(roic) > ROIC_CLAMP: roic = None
        else: ic = None
    m["roic"], m["_nopat"], m["_ic"] = roic, nopat, ic
    # AVERAGE equity / assets (opening+closing) exposed so the dollar-aggregate ROE and the DuPont tab
    # can use the SAME average-denominator convention as the per-name ROE/ROA and as ROIC $agg (which
    # already aggregates over the average _ic). Without these, ROE $agg used ending equity while ROE
    # wavg/median and ROIC $agg used average -- an inconsistency inside the $agg family. For a first
    # fiscal year (no prior) avg() already returns the ending value, so these are never None when eq/ta are.
    m["_aeq"], m["_ata"] = aeq, ata
    m["ebitda"], m["interest_expense"] = g("ebitda"), g("interest_expense")   # solvency tab (panel-sourced)
    # quality lenses
    m["gp_to_assets"] = safe_div(gp, ata) if (ata and ata > 0) else None          # Novy-Marx
    m["accruals"] = safe_div((ni - cfo), ata) if (ni is not None and cfo is not None and ata and ata > 0) else None  # Sloan
    m["cash_conversion"] = safe_div(cfo, ni) if (ni and ni > 0) else None          # earnings quality
    m["asset_turnover"] = safe_div(rev, ata) if (ata and ata > 0) else None
    m["rule_of_40"] = (m["rev_yoy"] + m["fcf_margin"]) if (m["rev_yoy"] is not None and m["fcf_margin"] is not None) else None
    # leverage
    m["d_to_equity"] = safe_div(debt, eq) if (eq and eq > 0) else None
    m["d_to_capital"] = safe_div(debt, debt + eq) if (eq is not None and (debt + eq) > 0) else None
    # profitability flags + persistence
    m["prof_ni"] = (ni > 0) if ni is not None else None
    m["prof_oi"] = (oi > 0) if oi is not None else None
    p1 = ni is not None and ni > 0
    m["p2"] = p1 and (r1.get("net_income") or -1) > 0
    m["p3"] = m["p2"] and (r2.get("net_income") or -1) > 0
    hist = sorted(y for y in cf if y <= fy0)
    prof_years = [y for y in hist if (cf[y].get("net_income") or -1) > 0]
    m["ever_profitable"] = len(prof_years) > 0
    m["was_profitable_prior"] = any(y < fy0 for y in prof_years)
    earliest = min(hist) if hist else None
    if ni is None: m["cohort"] = "unknown"
    elif ni > 0: m["cohort"] = "profitable"
    elif m["was_profitable_prior"]: m["cohort"] = "fallen"
    else: m["cohort"] = "never_profitable"
    m["never_basis"] = "" if m["ever_profitable"] else ("confirmed" if (earliest and earliest >= FIRST_RELIABLE_YEAR) else "limited_history")
    m["has_rev"] = rev is not None and rev > 0
    return m


METRICS_RATIO = ["gross_margin","op_margin","net_margin","roe","roa","roic","gp_to_assets",
                 "accruals","cash_conversion","asset_turnover","fcf_margin","d_to_equity","d_to_capital"]


# ---------- build ----------
def build(facts, snaps, base, temporal):
    years = sorted(snaps); per_snap = {}; detail = []
    for yr in years:
        snap_dt = date(yr, 4, 30); skey = f"{yr:04d}-04-30"
        cov = []   # (metrics, weight, member)
        wt_total = sum(m["weight"] for m in snaps[yr]); wt_cov = 0.0
        present_wt = {k: 0.0 for k in ("revenue","net_income","operating_income","gross_profit",
                       "stockholders_equity","total_assets","total_debt","operating_cash_flow")}
        for mem in snaps[yr]:
            cik = resolve_cik(facts, base, temporal, mem["ticker"], mem["nt"], skey, mem.get("cik_file"))
            if not cik: continue
            fy0 = pick_fy0(facts[cik], snap_dt)
            if fy0 is None: continue
            cm = company_metrics(facts[cik], fy0); cm["_w"] = mem["weight"]; cm["_tk"] = mem["nt"]
            cm["is_biotech"] = mem["ms_industry"] in BIOTECH; cm["gics"] = mem["gics"]
            cov.append((cm, mem["weight"], mem)); wt_cov += mem["weight"]
            for k in present_wt:
                if facts[cik][fy0].get(k) is not None: present_wt[k] += mem["weight"]
            detail.append([yr, mem["ticker"], cik, fy0, cm["sector"], int(cm["is_biotech"]),
                cm["revenue"], cm["net_income"], cm["operating_income"], cm["gross_profit"],
                cm["rev_yoy"], cm["rev_cagr3"], cm["gross_margin"], cm["op_margin"], cm["net_margin"],
                cm["roe"], cm["roa"], cm["roic"], cm["gp_to_assets"], cm["accruals"], cm["cash_conversion"],
                cm["asset_turnover"], cm["rule_of_40"], cm["d_to_equity"], cm["d_to_capital"],
                cm["cohort"], cm["never_basis"], int(bool(cm["prof_ni"])), int(bool(cm["prof_oi"])), int(cm["has_rev"])])
        per_snap[yr] = {"cov": cov, "wt_total": wt_total, "wt_cov": wt_cov, "present_wt": present_wt,
                        "n_members": len(snaps[yr])}
    return per_snap, detail, years


# ---------- workbook ----------
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
TITLE = Font(bold=True, size=12)

def _hdr(ws, row, headers):
    for c, h in enumerate(headers, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = HDR; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)

def _p(v): return round(100 * v, 1) if v is not None else None
def _x(v): return round(v, 2) if v is not None else None
def _r(v, s): return round(v / s, 1) if v is not None else None

def write_workbook(per_snap, detail, years):
    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Coverage (by index weight) ----
    ws = wb.create_sheet("Coverage")
    ws.cell(row=1, column=1, value="Coverage by index WEIGHT (the materiality check)").font = TITLE
    cw = ["Snapshot","Members","Covered","%Wt covered","revenue","net_income","operating_income",
          "gross_profit","equity","assets","total_debt","op_cash_flow"]
    _hdr(ws, 3, cw); r = 4
    for yr in years:
        ps = per_snap[yr]; wt = ps["wt_total"] or 1
        row = [f"{yr}-04-30", ps["n_members"], len(ps["cov"]), round(100*ps["wt_cov"]/wt,1)]
        for k in ("revenue","net_income","operating_income","gross_profit","stockholders_equity",
                  "total_assets","total_debt","operating_cash_flow"):
            row.append(round(100*ps["present_wt"][k]/wt,1))
        for c,v in enumerate(row,1): ws.cell(row=r,column=c,value=v)
        r += 1

    # ---- Index Quality Trends ----
    wt = wb.create_sheet("Index Quality Trends")
    wt.cell(row=1,column=1,value="Russell 2000 Growth -- Index Quality Trends (weight-weighted / median / dollar-aggregate)").font = TITLE
    hdr = ["Snapshot","Total Rev $B","Total NI $B","% with Revenue","% Unprofitable (NI) wt","% Unprofitable (OI) wt",
           "GrossMgn wavg","GrossMgn med","OpMgn wavg","OpMgn med","OpMgn $agg","NetMgn med",
           "ROE wavg","ROE med","ROE $agg","ROA med","ROIC wavg","ROIC med","ROIC $agg",
           "GP/Assets med","Accruals med","CashConv med","AssetTurn med",
           "Rev YoY wavg","Rev 3yCAGR med","FCF mgn med","RuleOf40 med",
           "D/E wavg","D/Cap wavg","D/Cap $agg"]
    _hdr(wt, 3, hdr); rr = 4
    for yr in years:
        cov = per_snap[yr]["cov"]
        def col(k): return [(m[k], w) for m,w,_ in cov]
        ag = lambda k, wnz=True: aggregate(col(k), winsor=wnz)
        tot_rev = sum(m["revenue"] for m,_,_ in cov if m["revenue"]) / 1e9
        tot_ni = sum(m["net_income"] for m,_,_ in cov if m["net_income"] is not None) / 1e9
        wtot = sum(w for _,w,_ in cov) or 1
        pct_rev = 100*sum(w for m,w,_ in cov if m["has_rev"]) / wtot
        up_ni = 100*sum(w for m,w,_ in cov if m["prof_ni"] is False)/sum(w for m,w,_ in cov if m["prof_ni"] is not None) if any(m["prof_ni"] is not None for m,_,_ in cov) else None
        up_oi = 100*sum(w for m,w,_ in cov if m["prof_oi"] is False)/sum(w for m,w,_ in cov if m["prof_oi"] is not None) if any(m["prof_oi"] is not None for m,_,_ in cov) else None
        opm_da = dollar_agg([(m["operating_income"], m["revenue"]) for m,_,_ in cov])
        roe_da = dollar_agg([(m["net_income"], m.get("_aeq") if m.get("_aeq") is not None else m["equity"]) for m,_,_ in cov])
        roic_da = dollar_agg([(m["_nopat"], m["_ic"]) for m,_,_ in cov])
        dcap_da = dollar_agg([(m["debt"], (m["debt"]+m["equity"]) if (m["debt"] is not None and m["equity"] is not None) else None) for m,_,_ in cov])
        row = [f"{yr}-04-30", round(tot_rev,1), round(tot_ni,1), round(pct_rev,1),
               round(up_ni,1) if up_ni is not None else None, round(up_oi,1) if up_oi is not None else None,
               _p(ag("gross_margin")["wavg"]), _p(ag("gross_margin")["median"]),
               _p(ag("op_margin")["wavg"]), _p(ag("op_margin")["median"]), _p(opm_da), _p(ag("net_margin")["median"]),
               _p(ag("roe")["wavg"]), _p(ag("roe")["median"]), _p(roe_da), _p(ag("roa")["median"]),
               _p(ag("roic")["wavg"]), _p(ag("roic")["median"]), _p(roic_da),
               _p(ag("gp_to_assets")["median"]), _p(ag("accruals")["median"]), _x(ag("cash_conversion")["median"]),
               _x(ag("asset_turnover")["median"]),
               _p(ag("rev_yoy")["wavg"]), _p(ag("rev_cagr3")["median"]), _p(ag("fcf_margin")["median"]), _p(ag("rule_of_40")["median"]),
               _x(ag("d_to_equity")["wavg"]), _p(ag("d_to_capital")["wavg"]), _p(dcap_da)]
        for c,v in enumerate(row,1): wt.cell(row=rr,column=c,value=v)
        rr += 1

    # ---- Profitability Cohorts ----
    wc = wb.create_sheet("Profitability Cohorts")
    wc.cell(row=1,column=1,value="Profitability cohorts (count and index weight)").font = TITLE
    ch = ["Snapshot","Covered","Unprof NI %cnt","Unprof NI %wt","Unprof OI %cnt","Unprof OI %wt",
          "Never (cnt)","  confirmed","  ltd-hist","Fallen (cnt)","Prof 2y %wt","Prof 3y %wt","% with Revenue"]
    _hdr(wc, 3, ch); pr = 4
    for yr in years:
        cov = per_snap[yr]["cov"]; n = len(cov); wtot = sum(w for _,w,_ in cov) or 1
        cls = [m for m,_,_ in cov if m["prof_ni"] is not None]
        un = [m for m,_,_ in cov if m["prof_ni"] is False]
        upc = 100*len(un)/len(cls) if cls else None
        upw = 100*sum(m["_w"] for m in un)/sum(m["_w"] for m in cls) if cls else None
        clo = [m for m,_,_ in cov if m["prof_oi"] is not None]; uno=[m for m,_,_ in cov if m["prof_oi"] is False]
        uoc = 100*len(uno)/len(clo) if clo else None
        uow = 100*sum(m["_w"] for m in uno)/sum(m["_w"] for m in clo) if clo else None
        never = [m for m in un if m["cohort"]=="never_profitable"]
        nconf = [m for m in never if m["never_basis"]=="confirmed"]; nlim=[m for m in never if m["never_basis"]=="limited_history"]
        fallen = [m for m in un if m["cohort"]=="fallen"]
        w2 = 100*sum(w for m,w,_ in cov if m["p2"])/wtot; w3 = 100*sum(w for m,w,_ in cov if m["p3"])/wtot
        prev = 100*sum(w for m,w,_ in cov if m["has_rev"])/wtot
        row=[f"{yr}-04-30",n,round(upc,1) if upc is not None else None,round(upw,1) if upw is not None else None,
             round(uoc,1) if uoc is not None else None,round(uow,1) if uow is not None else None,
             len(never),len(nconf),len(nlim),len(fallen),round(w2,1),round(w3,1),round(prev,1)]
        for c,v in enumerate(row,1): wc.cell(row=pr,column=c,value=v)
        pr += 1

    # ---- Concentration ----
    wn = wb.create_sheet("Concentration")
    wn.cell(row=1,column=1,value="Weight concentration & breadth").font = TITLE
    nh = ["Snapshot","Members"]+[f"Top{n} %wt" for n in TOP_NS]+["HHI","Effective N"]
    _hdr(wn, 3, nh); nr = 4
    for yr in years:
        ws_=sorted((m["_w"] for m,_,_ in per_snap[yr]["cov"]), reverse=True)
        tot=sum(ws_) or 1e-9; shares=[w/tot for w in ws_]
        hhi=round(sum((s*100)**2 for s in shares),1); effn=round(1/sum(s*s for s in shares),1) if shares else None
        topn=[round(sum(ws_[:n])/tot*100,1) for n in TOP_NS]
        row=[f"{yr}-04-30",len(ws_)]+topn+[hhi,effn]
        for c,v in enumerate(row,1): wn.cell(row=nr,column=c,value=v)
        nr += 1

    # ---- DuPont (dollar-aggregate ROE decomposition) ----
    wd = wb.create_sheet("DuPont")
    wd.cell(row=1,column=1,value="DuPont: index ROE = Net margin x Asset turnover x Leverage (dollar-aggregate)").font = TITLE
    dh = ["Snapshot","Net margin","Asset turnover (x)","Leverage (Assets/Equity, x)","Implied ROE","ROE $agg (check)"]
    _hdr(wd, 3, dh); dr = 4
    for yr in years:
        cov=per_snap[yr]["cov"]
        _ata=lambda m: m.get("_ata") if m.get("_ata") is not None else m["assets"]   # AVERAGE assets/equity,
        _aeq=lambda m: m.get("_aeq") if m.get("_aeq") is not None else m["equity"]    # consistent with the views
        nm=dollar_agg([(m["net_income"],m["revenue"]) for m,_,_ in cov])
        at=dollar_agg([(m["revenue"],_ata(m)) for m,_,_ in cov])
        lev=dollar_agg([(_ata(m),_aeq(m)) for m,_,_ in cov])
        roe_da=dollar_agg([(m["net_income"],_aeq(m)) for m,_,_ in cov])
        implied=(nm*at*lev) if (nm is not None and at is not None and lev is not None) else None
        row=[f"{yr}-04-30",_p(nm),_x(at),_x(lev),_p(implied),_p(roe_da)]
        for c,v in enumerate(row,1): wd.cell(row=dr,column=c,value=v)
        dr += 1

    # ---- Composition Change (decomposition of weighted op-margin & %unprofitable) ----
    wcc = wb.create_sheet("Composition Change")
    wcc.cell(row=1,column=1,value="What drove the change: within-name vs turnover vs reweighting").font = TITLE
    cch = ["Period","Metric","Total change","Within-name","Reweight","Entrants","Leavers"]
    _hdr(wcc, 3, cch); cr = 4
    def wavg_metric(cov, key, flag=False):
        tw = sum(w for m,w,_ in cov if (m[key] is not None));
        if not tw: return None
        return sum((1.0 if (flag and m[key]) else (0.0 if flag else m[key])) * w for m,w,_ in cov if m[key] is not None)/tw
    for i in range(1, len(years)):
        py, cy = years[i-1], years[i]
        pcov = {m["_tk"]: (m,w) for m,w,_ in per_snap[py]["cov"]}
        ccov = {m["_tk"]: (m,w) for m,w,_ in per_snap[cy]["cov"]}
        for key, flag, label in [("op_margin", False, "Op margin (wt)"), ("prof_ni", True, "% unprofitable (wt)")]:
            def val(m):
                v = m[key]; return (0.0 if v else 1.0) if flag else v   # %unprofitable = 1 - prof
            common = set(pcov) & set(ccov)
            pw = sum(w for t,(m,w) in pcov.items()) or 1; cw = sum(w for t,(m,w) in ccov.items()) or 1
            within = reweight = entr = leav = 0.0
            for t in common:
                mp, wp = pcov[t]; mc, wc_ = ccov[t]
                if mp[key] is not None and mc[key] is not None:
                    within += (wp/pw) * (val(mc) - val(mp))
                    reweight += (wc_/cw - wp/pw) * val(mc)
            for t in set(ccov) - common:
                mc, wc_ = ccov[t]
                if mc[key] is not None: entr += (wc_/cw) * val(mc)
            for t in set(pcov) - common:
                mp, wp = pcov[t]
                if mp[key] is not None: leav -= (wp/pw) * val(mp)
            tot = within + reweight + entr + leav
            row=[f"{py}->{cy}", label, _p(tot), _p(within), _p(reweight), _p(entr), _p(leav)]
            for c,v in enumerate(row,1): wcc.cell(row=cr,column=c,value=v)
            cr += 1

    # ---- Constituent Detail ----
    wde = wb.create_sheet("Constituent Detail")
    dh2 = ["Snapshot","Ticker","CIK","FY0","Sector","Biotech","Revenue","NetIncome","OpIncome","GrossProfit",
           "RevYoY","Rev3yCAGR","GrossMgn","OpMgn","NetMgn","ROE","ROA","ROIC","GP/Assets","Accruals",
           "CashConv","AssetTurn","RuleOf40","D/E","D/Cap","Cohort","NeverBasis","ProfNI","ProfOI","HasRev"]
    _hdr(wde, 1, dh2)
    for i, row in enumerate(detail, 2):
        for c, v in enumerate(row, 1): wde.cell(row=i, column=c, value=v)
    wde.freeze_panes = "A2"

    # ---- README ----
    rd = wb.create_sheet("README")
    for i, line in enumerate([
        "Russell 2000 Growth -- Index Quality Analytics",
        "Point-in-time: FY0 = latest fiscal year whose 10-K was FILED before the Apr-30 snapshot (no look-ahead).",
        "Ratios: ROE/ROA/ROIC use AVERAGE (opening+closing) denominators. Per-name ratios winsorized before weight-averaging.",
        "Three aggregation views: wavg (weight-weighted), median (typical constituent), $agg (sum numerator / sum denominator).",
        "Quality lenses: GP/Assets (Novy-Marx gross profitability), Accruals (Sloan = (NI-CFO)/avg assets),",
        "   Cash conversion (CFO/NI), Rule of 40 (rev growth% + FCF margin%).",
        "Cohorts: unprofitable split into never (confirmed vs limited-history pre-XBRL) and fallen; reported by COUNT and WEIGHT.",
        "Composition Change: decomposes each period's weighted-metric change into within-name drift, reweighting,",
        "   entrants, and leavers -- separating 'the index changed' from 'the same companies changed'.",
        "Coverage tab = % of index WEIGHT with each metric (the materiality check on extraction gaps).",
        "NOT YET INCLUDED: returns / performance attribution (pending Morningstar returns + quarterly weights).",
    ], 1): rd.cell(row=i, column=1, value=line)
    rd.column_dimensions["A"].width = 115
    wb.move_sheet("README", -(len(wb.sheetnames)-1))

    # ---- Charts ----
    cs = wb.create_sheet("Charts"); n = len(years)
    def chart(title, sheet, cols, anchor):
        c = LineChart(); c.title = title; c.height, c.width = 8, 16
        c.add_data(Reference(wb[sheet], min_col=cols[0], max_col=cols[1], min_row=3, max_row=3+n), titles_from_data=True)
        c.set_categories(Reference(wb[sheet], min_col=1, min_row=4, max_row=3+n)); cs.add_chart(c, anchor)
    chart("% Unprofitable by weight (NI / OI)", "Index Quality Trends", (5,6), "A1")
    chart("Operating margin (wavg / median / $agg)", "Index Quality Trends", (9,11), "A18")
    chart("ROE & ROIC (median)", "Index Quality Trends", (14,18), "A35")
    chart("Concentration: Top10 weight & HHI", "Concentration", (4,4), "A52")
    wb.save(OUT)


def main():
    if not FUND.exists(): print(f"!! {FUND.name} not found. Run r2k_step2_asfiled.py first."); return
    print("Loading..."); facts = load_fundamentals(); snaps = load_membership(); base, temporal = load_maps()
    print(f"  {len(facts)} companies, {len(snaps)} snapshots")
    per_snap, detail, years = build(facts, snaps, base, temporal)
    print("Writing workbook..."); write_workbook(per_snap, detail, years)
    print(f"\n  DONE -> {OUT.name}")
    for yr in years:
        ps = per_snap[yr]; pctw = 100*ps["wt_cov"]/(ps["wt_total"] or 1)
        print(f"  {yr}: {len(ps['cov']):>4} covered  ({pctw:4.1f}% weight)")


if __name__ == "__main__":
    main()
