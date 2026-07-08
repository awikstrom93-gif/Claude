"""
r2k_factor_analysis.py -- what factors drove the two indices, over time.

Views, all holdings-based and point-in-time (factor known at month t, return realized at t+1):
  1. FACTOR EFFICACY -- cumulative long/short return of each factor inside the index (cap-weighted top-
     minus-bottom quintile on the sector-neutral factor score). "Was the factor rewarded, and when."
  2. INDEX-RETURN ATTRIBUTION -- a multivariate Fama-MacBeth cross-section each month regresses the
     constituents' next-month returns on their (raw) factor z-scores; the slope is the factor's return,
     and the index's own weighted exposure x that return is the factor's contribution to the index. Sums
     of the contributions + an intercept ("market") + a residual reconstruct the index return.
  2b. SECTOR-ADJUSTED ATTRIBUTION -- (2) re-run with GICS-sector factors added, so the style slopes are
     estimated CONTROLLING for sector (consistent with the sector-neutral efficacy lens) and the sector
     tilt becomes its own bucket. A robustness check: if the styles barely move, the effect is genuinely
     within-sector rather than a sector bet in disguise. Still reconciles to the index return.

Six factors: momentum, size, low-volatility, value, quality, growth (definitions in FACTORS below).

INPUTS (globbed from BASE): the constituent monthly-return file, the market-cap/EV/price file, the two
holdings workbooks (weights + GICS sector), and fundamentals_dera_resolved.csv (value/quality/growth).
Coverage is ~99-100% of index weight from 2015 on for BOTH indices; 2011-2014 runs 90-98% (delisted
names dropped). Names present in holdings but absent from returns (new-at-reconstitution adds) are
dropped -- they have no usable history. RUN: python r2k_factor_analysis.py
"""
import os, csv, glob, math, re
from collections import defaultdict

BASE = os.environ.get("R2KG_BASE", ".")
WINSOR_RET = (-0.60, 1.50)          # cap extreme monthly returns before any use
Z_CLIP = 3.0                        # winsorize factor z-scores at +/- 3 sigma
Y0 = 2012                           # start year (2011 has ~90% coverage; note it, exclude from headline)
MOM_SKIP, MOM_LOOK = 1, 12          # 12-1 momentum
VOL_LOOK = 12
MIN_NAMES = 60                      # skip a month/index if fewer scored names
FACTORS = ["Momentum", "Size", "LowVol", "Value", "Quality", "Growth"]


# --------------------------------------------------------------------------- small pure-python OLS
def ols(X, y):
    """Ordinary least squares b = (X'X)^-1 X'y via Gaussian elimination. X: list of rows (each len p),
    y: list. Returns coefficient list length p, or None if singular."""
    p = len(X[0])
    XtX = [[0.0] * p for _ in range(p)]
    Xty = [0.0] * p
    for xi, yi in zip(X, y):
        for a in range(p):
            Xty[a] += xi[a] * yi
            xa = xi[a]
            row = XtX[a]
            for b in range(p):
                row[b] += xa * xi[b]
    # solve XtX b = Xty (augmented Gaussian elimination with partial pivot + tiny ridge for stability)
    for a in range(p):
        XtX[a][a] += 1e-8
    M = [XtX[i] + [Xty[i]] for i in range(p)]
    for c in range(p):
        piv = max(range(c, p), key=lambda r: abs(M[r][c]))
        if abs(M[piv][c]) < 1e-12:
            return None
        M[c], M[piv] = M[piv], M[c]
        pv = M[c][c]
        M[c] = [v / pv for v in M[c]]
        for r in range(p):
            if r != c and M[r][c]:
                f = M[r][c]
                M[r] = [M[r][k] - f * M[c][k] for k in range(p + 1)]
    return [M[i][p] for i in range(p)]


def zscore(d, sectors=None):
    """Cross-sectional z-score of {name: value}; winsorized at +/-Z_CLIP. If `sectors` given (name->sec),
    z is demeaned within sector (sector-neutral) after the global z."""
    vals = [v for v in d.values() if v is not None]
    if len(vals) < 5:
        return {}
    m = sum(vals) / len(vals)
    sd = (sum((v - m) ** 2 for v in vals) / (len(vals) - 1)) ** 0.5 or 1.0
    z = {k: max(-Z_CLIP, min(Z_CLIP, (v - m) / sd)) for k, v in d.items() if v is not None}
    if sectors:
        bys = defaultdict(list)
        for k, zz in z.items():
            bys[sectors.get(k, "?")].append(zz)
        smean = {s: (sum(v) / len(v)) for s, v in bys.items()}
        z = {k: zz - smean.get(sectors.get(k, "?"), 0.0) for k, zz in z.items()}
    return z


# --------------------------------------------------------------------------- loaders
def _find(*pats):
    for p in pats:
        h = glob.glob(os.path.join(BASE, p))
        if h:
            return sorted(h)[0]
    raise FileNotFoundError(pats)


def _ym(s):
    s = str(s)
    if "-" in s and s[:4].isdigit():
        return s[:7]
    p = s.split("/")
    return f"{int(p[2][:4]):04d}-{int(p[0]):02d}" if len(p) == 3 else None


def load_returns():
    """month list + ret[cik][mi] (winsorized fraction) + ticker->cik and cik->ticker."""
    import openpyxl
    wb = openpyxl.load_workbook(_find("*Constituents*Monthly*Performance*.xlsx",
                                      "*Monthly*Performance*.xlsx"), read_only=True, data_only=True)
    R = [list(r) for r in wb[wb.sheetnames[0]].iter_rows(values_only=True)]
    hdr = next(i for i, r in enumerate(R) if r and str(r[0]).strip() == "Group/Investment")
    months = [_ym(c) for c in R[hdr - 1][5:] if c is not None]
    ret = defaultdict(dict); tkr2cik = {}; cik2tkr = {}
    for r in R[hdr + 1:]:
        cik = str(r[1]).strip().lstrip("0") if len(r) > 1 and r[1] is not None else ""
        if not cik.isdigit():
            continue
        tk = str(r[2]).strip().upper() if len(r) > 2 and r[2] else ""
        if tk:
            tkr2cik.setdefault(tk, cik); cik2tkr[cik] = tk
        for k, c in enumerate(r[5:5 + len(months)]):
            if isinstance(c, (int, float)):
                ret[cik][k] = max(WINSOR_RET[0], min(WINSOR_RET[1], c / 100.0))
    return months, ret, tkr2cik, cik2tkr


def load_mktcap(months):
    """mcap[cik][mi] in $mil (from the 'Market Cap (in Mil) - Monthly' block)."""
    import openpyxl
    wb = openpyxl.load_workbook(_find("*Market*Cap*.xlsx"), read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    hdr = rows[0]
    # locate the Market Cap monthly columns and their month labels
    cols = []
    for j, c in enumerate(hdr):
        s = str(c) if c is not None else ""
        if s.startswith("Market Cap"):
            m = re.search(r"(\d{4})-(\d{2})", s)
            if m:
                cols.append((j, f"{m.group(1)}-{m.group(2)}"))
    midx = {m: k for k, m in enumerate(months)}
    cikcol = next(j for j, c in enumerate(hdr) if str(c).strip() == "CIK")
    mcap = defaultdict(dict)
    for r in rows[1:]:
        cik = str(r[cikcol]).strip().lstrip("0") if r[cikcol] is not None else ""
        if not cik.isdigit():
            continue
        for j, ym in cols:
            if ym in midx and j < len(r) and isinstance(r[j], (int, float)) and r[j] > 0:
                mcap[cik][midx[ym]] = float(r[j])
    return mcap


def load_holdings(pat):
    """univ[mi] = {cik: weight_frac}, sector[mi] = {cik: gics} for one index."""
    import openpyxl
    wb = openpyxl.load_workbook(_find(pat), read_only=True, data_only=True)
    univ = defaultdict(dict); sect = defaultdict(dict)
    for sh in wb.sheetnames:
        try:
            mm, dd, yy = sh.split("."); ym = f"{int(yy):04d}-{int(mm):02d}"
        except ValueError:
            continue
        for i, r in enumerate(wb[sh].iter_rows(values_only=True)):
            if i == 0:
                continue
            cik = str(r[0]).strip().lstrip("0") if r[0] is not None else ""
            w = r[5] if len(r) > 5 and isinstance(r[5], (int, float)) else None
            if cik.isdigit() and w is not None:
                univ[ym][cik] = w / 100.0
                sect[ym][cik] = str(r[6]) if len(r) > 6 and r[6] else "?"
    return univ, sect


def load_fundamentals():
    """f[cik] = {fy(int): {revenue, net_income, operating_income, gross_profit, total_equity,
    total_assets, total_debt, cfo}} from fundamentals_dera_resolved.csv."""
    fn = _find("fundamentals_dera_resolved.csv", "edgar_annual_fundamentals_ASFILED.csv")
    keys = ("revenue", "net_income", "operating_income", "gross_profit", "total_equity",
            "total_assets", "total_debt", "cfo", "operating_cash_flow", "parent_equity")
    f = defaultdict(dict)
    for r in csv.DictReader(open(fn, encoding="utf-8", errors="replace")):
        cik = str(r.get("cik", "")).strip().lstrip("0")
        try:
            fy = int(float(r.get("fiscal_year", "")))
        except (TypeError, ValueError):
            continue
        if not cik:
            continue
        rec = {}
        for k in keys:
            v = (r.get(k) or "").strip()
            try:
                rec[k] = float(v)
            except (TypeError, ValueError):
                rec[k] = None
        f[cik][fy] = rec
    return f


# --------------------------------------------------------------------------- factor construction
def pit_fy(f_cik, year, month):
    """point-in-time latest fiscal year available at (year,month): assume a 10-K for FY Y is filed by
    ~April of Y+1, so before April use FY (year-2) else (year-1)."""
    target = year - 1 if month >= 4 else year - 2
    ys = [y for y in f_cik if y <= target]
    return max(ys) if ys else None


def compound(retc, a, b):
    g = 1.0; ok = False
    for k in range(a, b):
        if k in retc:
            g *= (1 + retc[k]); ok = True
    return (g - 1) if ok else None


def build_factor_scores(names, mi, ym, ret, mcap, fund):
    """raw factor values per name at month index mi (ym='YYYY-MM'). Returns {factor: {cik: value}}."""
    year, month = int(ym[:4]), int(ym[5:7])
    raw = {ff: {} for ff in FACTORS}
    for c in names:
        rc = ret.get(c, {})
        # momentum 12-1
        raw["Momentum"][c] = compound(rc, mi - MOM_LOOK, mi - MOM_SKIP)
        # size = -ln(mktcap)
        mc = mcap.get(c, {}).get(mi)
        raw["Size"][c] = (-math.log(mc)) if (mc and mc > 0) else None
        # low-vol = -stdev(trailing 12m)
        w = [rc[j] for j in range(mi - VOL_LOOK, mi) if j in rc]
        if len(w) >= 6:
            m = sum(w) / len(w)
            raw["LowVol"][c] = -((sum((x - m) ** 2 for x in w) / (len(w) - 1)) ** 0.5)
        else:
            raw["LowVol"][c] = None
        # fundamentals (value/quality/growth) point-in-time
        fy = pit_fy(fund.get(c, {}), year, month)
        fr = fund.get(c, {}).get(fy) if fy is not None else None
        eq = (fr.get("total_equity") or fr.get("parent_equity")) if fr else None
        # value: composite E/P, B/P, S/P (mcap in $mil; fundamentals in $ -> scale mcap to $)
        if fr and mc and mc > 0:
            M = mc * 1e6
            ep = (fr["net_income"] / M) if fr.get("net_income") is not None else None
            bp = (eq / M) if eq is not None else None
            sp = (fr["revenue"] / M) if fr.get("revenue") is not None else None
            raw["Value"][c] = _avail_mean([ep, bp, sp])
        else:
            raw["Value"][c] = None
        # quality: ROIC, GP/assets, net margin, -D/E, -accruals, profitable
        if fr:
            ta = fr.get("total_assets"); rev = fr.get("revenue"); ni = fr.get("net_income")
            oi = fr.get("operating_income"); gp = fr.get("gross_profit")
            debt = fr.get("total_debt"); cfo = fr.get("cfo") or fr.get("operating_cash_flow")
            ic = (debt or 0) + (eq or 0)
            q = []
            if oi is not None and ic > 0: q.append(oi / ic)                       # ROIC-ish
            if gp is not None and ta and ta > 0: q.append(gp / ta)                # GP/assets
            if ni is not None and rev and rev > 0: q.append(ni / rev)             # net margin
            if debt is not None and eq and eq > 0: q.append(-(debt / eq))         # -leverage
            if ni is not None and cfo is not None and ta and ta > 0: q.append(-((ni - cfo) / ta))  # -accruals
            if ni is not None: q.append(1.0 if ni > 0 else 0.0)                   # profitable
            raw["Quality"][c] = (sum(q) / len(q)) if q else None
        else:
            raw["Quality"][c] = None
        # growth: rev YoY + 3y CAGR
        if fy is not None:
            fc = fund.get(c, {})
            r0 = fc.get(fy, {}).get("revenue"); r1 = fc.get(fy - 1, {}).get("revenue")
            r3 = fc.get(fy - 3, {}).get("revenue")
            g = []
            if r0 and r0 > 0 and r1 and r1 > 0: g.append(r0 / r1 - 1)
            if r0 and r0 > 0 and r3 and r3 > 0: g.append((r0 / r3) ** (1 / 3) - 1)
            raw["Growth"][c] = (sum(g) / len(g)) if g else None
        else:
            raw["Growth"][c] = None
    return raw


def _avail_mean(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs)) if xs else None


# --------------------------------------------------------------------------- main analysis
def _fm_decompose(common, w, zr, fwd, sect=None):
    """One month's multivariate Fama-MacBeth decomposition of next-month returns. Columns:
    intercept + (optional demeaned GICS-sector dummies) + the six style z-scores. Returns
        {market, style{factor:contrib}, sect_bucket, resid, ir}
    which sum to the index return by construction (resid is the plug). When `sect` (cik->sector) is
    given, the style slopes are estimated CONTROLLING for sector -- i.e. sector-neutral, matching the
    efficacy lens -- and the sector tilt is reported as its own bucket. The style coefficients are
    invariant to how the sector space is parameterized, so the demeaning below only keeps the
    intercept interpretable as the average-name return."""
    tw = sum(w[c] for c in common) or 1.0
    ir = sum(w[c] * fwd[c] for c in common) / tw
    sc_cols = []                                     # (sector label, {cik: demeaned dummy})
    if sect:
        secs = sorted({sect.get(c, "?") for c in common})
        if len(secs) > 1:
            n = len(common)
            for s in secs[:-1]:                      # drop one sector to avoid collinearity w/ the constant
                p = sum(1 for c in common if sect.get(c, "?") == s) / n
                sc_cols.append((s, {c: (1.0 if sect.get(c, "?") == s else 0.0) - p for c in common}))
    X = [[1.0] + [col[c] for _, col in sc_cols] + [zr[ff][c] for ff in FACTORS] for c in common]
    b = ols(X, [fwd[c] for c in common])
    if not b:
        return None
    off = 1 + len(sc_cols)
    style = {}
    for i, ff in enumerate(FACTORS):
        expo = sum(w[c] * zr[ff][c] for c in common) / tw
        style[ff] = b[off + i] * expo
    sect_bucket = 0.0
    for j, (_, col) in enumerate(sc_cols):
        expo = sum(w[c] * col[c] for c in common) / tw
        sect_bucket += b[1 + j] * expo
    resid = ir - b[0] - sum(style.values()) - sect_bucket
    return dict(market=b[0], style=style, sect_bucket=sect_bucket, resid=resid, ir=ir)


def analyse(index_pat, label, months, ret, mcap, fund, tkr2cik):
    univ, sect = load_holdings(index_pat)
    hmonths = sorted(univ)                                # snapshot months (monthly recent, quarterly early)
    midx = {m: k for k, m in enumerate(months)}
    eff = {ff: [] for ff in FACTORS}                     # monthly L/S returns per factor
    attr = {ff: [] for ff in FACTORS}                    # raw monthly index contribution per factor
    attr_adj = {ff: [] for ff in FACTORS}                # sector-controlled contribution per factor
    intercepts = []; resid = []; idx_ret = []; mos = []; emos = []; nnames = []
    intercepts_adj = []; resid_adj = []; sect_bucket = []   # sector-adjusted decomposition
    # carry the latest holdings snapshot forward to every month (hold until the next rebalance), so the
    # factor series is continuous rather than only on snapshot dates.
    def latest_snap(ym):
        prior = [h for h in hmonths if h <= ym]
        return prior[-1] if prior else None
    for ym in months:
        if int(ym[:4]) < Y0:
            continue
        mi = midx.get(ym)
        snap = latest_snap(ym)
        if mi is None or mi + 1 >= len(months) or snap is None:
            continue
        univ_ym, sect_ym = univ[snap], sect[snap]
        # map holdings to a return-covered cik (ticker fallback handled by using cik that has returns)
        hold = {}
        for c, w in univ_ym.items():
            cc = c if (mi + 1) in ret.get(c, {}) else None
            hold[cc or c] = (w, sect_ym.get(c, "?"))
        names = [c for c in hold if (mi + 1) in ret.get(c, {})]
        if len(names) < MIN_NAMES:
            continue
        w = {c: hold[c][0] for c in names}; sec = {c: hold[c][1] for c in names}
        emos.append(ym); nnames.append(len(names))
        raw = build_factor_scores(names, mi, ym, ret, mcap, fund)
        zsn = {ff: zscore(raw[ff], sec) for ff in FACTORS}      # sector-neutral (efficacy)
        zr = {ff: zscore(raw[ff]) for ff in FACTORS}            # raw z (attribution regression)
        fwd = {c: ret[c][mi + 1] for c in names}
        # --- efficacy: cap-weighted top-minus-bottom quintile on sector-neutral z ---
        for ff in FACTORS:
            sc = zsn[ff]
            if len(sc) < MIN_NAMES:
                eff[ff].append(None); continue
            it = sorted(sc, key=lambda c: sc[c]); q = len(it) // 5
            top, bot = it[-q:], it[:q]
            qr = lambda g: sum(w[c] * fwd[c] for c in g) / (sum(w[c] for c in g) or 1)
            eff[ff].append(qr(top) - qr(bot))
        # --- attribution: multivariate Fama-MacBeth, two decompositions on the SAME cross-section ---
        #     raw = styles only (reconciles to the index return; style slopes carry the sector tilt);
        #     adj = styles + GICS-sector factors (style slopes are sector-controlled, matching efficacy,
        #           and the sector tilt is reported as its own bucket). Both reconcile by construction.
        common = [c for c in names if all(c in zr[ff] for ff in FACTORS)]
        draw = _fm_decompose(common, w, zr, fwd) if len(common) >= MIN_NAMES else None
        dadj = _fm_decompose(common, w, zr, fwd, sect=sec) if len(common) >= MIN_NAMES else None
        if draw and dadj:
            for ff in FACTORS:
                attr[ff].append(draw["style"][ff])
                attr_adj[ff].append(dadj["style"][ff])
            intercepts.append(draw["market"]); resid.append(draw["resid"])
            intercepts_adj.append(dadj["market"]); resid_adj.append(dadj["resid"])
            sect_bucket.append(dadj["sect_bucket"])
            idx_ret.append(draw["ir"]); mos.append(ym)
        else:
            for ff in FACTORS:
                attr[ff].append(None); attr_adj[ff].append(None)
    return dict(label=label, eff=eff, attr=attr, attr_adj=attr_adj, intercepts=intercepts,
                resid=resid, intercepts_adj=intercepts_adj, resid_adj=resid_adj,
                sect_bucket=sect_bucket, idx_ret=idx_ret, mos=mos, emos=emos, nnames=nnames)


def _cum(series):
    g = 1.0
    for v in series:
        if v is not None:
            g *= (1 + v)
    return (g - 1) * 100


def _ann(series, n_years):
    c = _cum(series) / 100
    return ((1 + c) ** (1 / n_years) - 1) * 100 if n_years > 0 else None


def _tstat(series):
    s = [v for v in series if v is not None]
    if len(s) < 12:
        return None
    m = sum(s) / len(s)
    sd = (sum((v - m) ** 2 for v in s) / (len(s) - 1)) ** 0.5
    return (m / (sd / len(s) ** 0.5)) if sd else None


def report(res):
    print(f"\n===== {res['label']} =====")
    nyr = len(res["mos"]) / 12 if res["mos"] else 0
    print(f"  months {res['mos'][0]}..{res['mos'][-1]}  ({len(res['mos'])} obs, {nyr:.1f} yr)")
    print(f"\n  FACTOR EFFICACY (cap-wtd top-minus-bottom quintile, sector-neutral, next-month):")
    print(f"    {'factor':10s} {'cum L/S %':>10s} {'ann %':>8s} {'t-stat':>7s}")
    for ff in FACTORS:
        s = res["eff"][ff]
        print(f"    {ff:10s} {_cum(s):>10.0f} {_ann(s, nyr):>8.1f} "
              f"{(_tstat(s) or 0):>7.2f}")
    print(f"\n  INDEX-RETURN ATTRIBUTION (cumulative contribution to {res['label']} return, pts):")
    tot = 0.0
    for ff in FACTORS:
        c = sum(v for v in res["attr"][ff] if v is not None) * 100
        tot += c
        print(f"    {ff:10s} {c:>+8.1f}")
    mkt = sum(res["intercepts"]) * 100
    rez = sum(res["resid"]) * 100
    ir = sum(res["idx_ret"]) * 100
    print(f"    {'Market(a)':10s} {mkt:>+8.1f}")
    print(f"    {'Residual':10s} {rez:>+8.1f}")
    print(f"    {'--sum--':10s} {tot + mkt + rez:>+8.1f}  vs index (sum monthly) {ir:>+8.1f}")
    print(f"\n  SECTOR-ADJUSTED ATTRIBUTION (style slopes controlled for GICS sector; pts):")
    tota = 0.0
    for ff in FACTORS:
        c = sum(v for v in res["attr_adj"][ff] if v is not None) * 100
        tota += c
        print(f"    {ff:10s} {c:>+8.1f}")
    mkta = sum(res["intercepts_adj"]) * 100
    seca = sum(res["sect_bucket"]) * 100
    reza = sum(res["resid_adj"]) * 100
    print(f"    {'Market(a)':10s} {mkta:>+8.1f}")
    print(f"    {'Sectors':10s} {seca:>+8.1f}")
    print(f"    {'Residual':10s} {reza:>+8.1f}")
    print(f"    {'--sum--':10s} {tota + mkta + seca + reza:>+8.1f}  vs index (sum monthly) {ir:>+8.1f}")


# --------------------------------------------------------------------------- workbook output
def _cy_eff(res):
    """calendar-year cumulative L/S % per factor: {factor: {year: pct}}."""
    out = {ff: {} for ff in FACTORS}
    for ff in FACTORS:
        by = defaultdict(list)
        for ym, v in zip(res["emos"], res["eff"][ff]):
            if v is not None:
                by[int(ym[:4])].append(v)
        for y, s in by.items():
            g = 1.0
            for v in s:
                g *= (1 + v)
            out[ff][y] = (g - 1) * 100
    return out


def write_workbook(results, out_path):
    """One workbook, IC-facing: Summary (both indices), per-index Efficacy $1 time series,
    Attribution bridge, calendar-year efficacy grid, README."""
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
    TITLE = Font(bold=True, size=12, color="1F4E5F"); H = Font(bold=True, size=11, color="1F4E5F")
    BODY = Font(size=10)

    def hdr(ws, row, hs, c0=1):
        for i, h in enumerate(hs):
            x = ws.cell(row=row, column=c0 + i, value=h); x.fill = HDR; x.font = HF
            x.alignment = Alignment(horizontal="center", wrap_text=True)

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Summary: efficacy (ann %, t) + attribution (pts) for both indices, factor by factor ----
    ws = wb.create_sheet("Factor Summary")
    ws.cell(row=1, column=1, value="What drove the two Small-Cap Growth indices -- factor efficacy and "
            "return attribution (point-in-time, holdings-based)").font = TITLE
    ws.cell(row=2, column=1, value="Efficacy = cap-weighted top-minus-bottom quintile on the sector-neutral "
            "factor score, held one month. Attribution = the index's own factor exposure x the "
            "cross-sectional (Fama-MacBeth) return to that factor.").font = BODY
    rd = 3
    for lab, res in results.items():
        ws.cell(row=rd, column=1, value=f"({lab}: {res['emos'][0]}..{res['emos'][-1]}, "
                f"{len(res['emos'])} months of efficacy; {len(res['mos'])} months reconciled)").font = BODY
        rd += 1
    r = rd + 1
    hdr(ws, r, ["Factor", "R2000G eff ann %", "R2000G t", "R2000G attrib pts",
                "S&P600G eff ann %", "S&P600G t", "S&P600G attrib pts"])
    r += 1
    for ff in FACTORS:
        row = [ff]
        for lab, res in results.items():
            nyr = len(res["emos"]) / 12 if res["emos"] else 1
            s = res["eff"][ff]
            row += [round(_ann(s, nyr), 1), round(_tstat(s) or 0, 2),
                    round(sum(v for v in res["attr"][ff] if v is not None) * 100, 1)]
        for i, v in enumerate(row):
            cell = ws.cell(row=r, column=1 + i, value=v)
            cell.font = Font(bold=True) if i == 0 else BODY
        r += 1
    # market + residual footer for the attribution reconciliation
    r += 1
    ws.cell(row=r, column=1, value="Market (intercept) / Residual / Index total, pts:").font = H
    r += 1
    hdr(ws, r, ["", "R2000G", "", "", "S&P600G", "", ""]); r += 1
    for name, key, sign in [("Market (intercept)", "intercepts", 1), ("Residual", "resid", 1),
                            ("Index (sum of months)", "idx_ret", 1)]:
        ws.cell(row=r, column=1, value=name).font = BODY
        for ci, (lab, res) in zip((2, 5), results.items()):
            ws.cell(row=r, column=ci, value=round(sum(res[key]) * 100, 1)).font = BODY
        r += 1
    ws.column_dimensions["A"].width = 26
    for col in "BCDEFG":
        ws.column_dimensions[col].width = 15

    # ---- per-index Efficacy growth-of-$1 (monthly) time series, for line charts ----
    for lab, res in results.items():
        tag = "R2000G" if "R2000" in lab else "SP600G"
        wsg = wb.create_sheet(f"Factor $1 {tag}")
        wsg.cell(row=1, column=1, value=f"{lab}: growth of $1 in each factor's long/short leg "
                 "(cap-wtd top-minus-bottom quintile, sector-neutral)").font = TITLE
        hdr(wsg, 3, ["Month"] + FACTORS)
        g = {ff: 1.0 for ff in FACTORS}
        rr = 4
        for k, ym in enumerate(res["emos"]):
            wsg.cell(row=rr, column=1, value=ym).font = BODY
            for i, ff in enumerate(FACTORS):
                v = res["eff"][ff][k]
                if v is not None:
                    g[ff] *= (1 + v)
                wsg.cell(row=rr, column=2 + i, value=round(g[ff], 4)).font = BODY
            rr += 1
        wsg.column_dimensions["A"].width = 10

    # ---- Attribution bridge (cumulative pts) per index, for bar charts ----
    wa = wb.create_sheet("Factor Attribution")
    wa.cell(row=1, column=1, value="Cumulative contribution to each index's total return, in points "
            "(Market + sum of factor contributions + Residual = index return)").font = TITLE
    hdr(wa, 3, ["Component", "R2000G pts", "S&P600G pts"])
    comps = [("Market (intercept)", lambda res: sum(res["intercepts"]) * 100)]
    comps += [(ff, (lambda res, ff=ff: sum(v for v in res["attr"][ff] if v is not None) * 100))
              for ff in FACTORS]
    comps += [("Residual", lambda res: sum(res["resid"]) * 100),
              ("Index total", lambda res: sum(res["idx_ret"]) * 100)]
    rr = 4
    for name, fn in comps:
        wa.cell(row=rr, column=1, value=name).font = (H if name in ("Market (intercept)", "Index total") else BODY)
        for ci, (lab, res) in zip((2, 3), results.items()):
            wa.cell(row=rr, column=ci, value=round(fn(res), 1)).font = BODY
        rr += 1
    wa.column_dimensions["A"].width = 22
    wa.column_dimensions["B"].width = 14; wa.column_dimensions["C"].width = 14

    # ---- sector-adjusted attribution: raw vs GICS-sector-controlled, side by side ----
    res_list = list(results.values())            # [R2000G, S&P600G] in insertion order
    def _S(res, key, ff=None):
        return round((sum(v for v in res[key][ff] if v is not None) if ff is not None
                      else sum(res[key])) * 100, 1)
    wj = wb.create_sheet("Factor Attribution Adj")
    wj.cell(row=1, column=1, value="Return attribution: raw vs sector-adjusted. The 'adj' columns add GICS-"
            "sector factors to the monthly regression, so the style slopes are estimated CONTROLLING for "
            "sector (consistent with the sector-neutral efficacy lens); the sector tilt is then its own "
            "bucket. Both reconcile to the index return.").font = TITLE
    wj.cell(row=2, column=1, value="Read across each style row: if a style's contribution barely changes "
            "from raw to adj (and the Sectors bucket stays small), the effect is a genuine within-sector "
            "style effect, not a sector bet in disguise.").font = BODY
    hdr(wj, 3, ["Component", "R2000G raw", "R2000G sector-adj", "S&P600G raw", "S&P600G sector-adj"])
    # bridge: Market, 6 styles, Sectors, Residual, Index total  (rows 4..13)
    bridge = [("Market (intercept)", "intercepts", "intercepts_adj", None)]
    bridge += [(ff, "attr", "attr_adj", ff) for ff in FACTORS]
    bridge += [("Sectors", None, "sect_bucket", None),
               ("Residual", "resid", "resid_adj", None),
               ("Index total", "idx_ret", "idx_ret", None)]
    rr = 4
    for name, kraw, kadj, ff in bridge:
        bold = name in ("Market (intercept)", "Sectors", "Index total")
        wj.cell(row=rr, column=1, value=name).font = (H if bold else BODY)
        for base, res in zip((2, 4), res_list):
            wj.cell(row=rr, column=base, value=(0.0 if kraw is None else _S(res, kraw, ff))).font = BODY
            wj.cell(row=rr, column=base + 1, value=_S(res, kadj, ff)).font = BODY
        rr += 1
    # style-only comparison block at a FIXED header row (15) so a chart can target just the six styles
    SB = 15
    hdr(wj, SB, ["Style", "R2000G raw", "R2000G adj", "S&P600G raw", "S&P600G adj"])
    for i, ff in enumerate(FACTORS):
        wj.cell(row=SB + 1 + i, column=1, value=ff).font = Font(bold=True)
        for base, res in zip((2, 4), res_list):
            wj.cell(row=SB + 1 + i, column=base, value=_S(res, "attr", ff)).font = BODY
            wj.cell(row=SB + 1 + i, column=base + 1, value=_S(res, "attr_adj", ff)).font = BODY
    wj.column_dimensions["A"].width = 22
    for col in "BCDE":
        wj.column_dimensions[col].width = 16

    # ---- calendar-year efficacy grid (factor x year), one block per index ----
    wy = wb.create_sheet("Factor By Year")
    wy.cell(row=1, column=1, value="Calendar-year factor efficacy (long/short %, sector-neutral) -- "
            "when each factor paid, and when it hurt").font = TITLE
    rr = 3
    for lab, res in results.items():
        cy = _cy_eff(res)
        years = sorted({y for ff in FACTORS for y in cy[ff]})
        wy.cell(row=rr, column=1, value=lab).font = H; rr += 1
        hdr(wy, rr, ["Factor"] + [str(y) for y in years]); rr += 1
        for ff in FACTORS:
            wy.cell(row=rr, column=1, value=ff).font = Font(bold=True)
            for j, y in enumerate(years):
                v = cy[ff].get(y)
                wy.cell(row=rr, column=2 + j, value=(round(v, 1) if v is not None else None)).font = BODY
            rr += 1
        rr += 2
    wy.column_dimensions["A"].width = 14

    # ---- README ----
    wr = wb.create_sheet("README")
    notes = [
        ("Factor Analysis -- how to read it", TITLE),
        ("", BODY),
        ("Purpose: decompose each index's return into the factors that drove it, using only "
         "point-in-time information (the factor is known at month t; the return is realized at t+1). "
         "No look-ahead.", BODY),
        ("", BODY),
        ("Two lenses:", H),
        ("  1. EFFICACY (Factor Summary, Factor $1, Factor By Year) -- was the factor rewarded INSIDE the "
         "index? Cap-weighted top-minus-bottom quintile on the sector-neutral score, held one month, "
         "compounded. This isolates the factor from sector bets.", BODY),
        ("  2. ATTRIBUTION (Factor Summary, Factor Attribution) -- how much did the factor CONTRIBUTE to "
         "the index's realized return? A multivariate Fama-MacBeth cross-section each month regresses "
         "next-month returns on raw factor z-scores; the index's own weighted exposure x that slope is "
         "the contribution. Market + sum of factors + residual reconstructs the index return (the "
         "reconciliation is exact by construction).", BODY),
        ("", BODY),
        ("  2b. SECTOR-ADJUSTED ATTRIBUTION (Factor Attribution Adj) -- the same bridge, but with GICS-"
         "sector factors added to each monthly regression, so the STYLE slopes are estimated controlling "
         "for sector (consistent with the sector-neutral efficacy lens) and the sector tilt becomes its "
         "own bucket. Reads directly against the raw columns: if a style barely moves and the Sectors "
         "bucket stays small, the effect is a genuine within-sector style effect rather than a sector "
         "bet. In this data the styles barely move -- e.g. R2000G Quality stays about -17 pts and the "
         "Sectors bucket is only ~+2 -- so the Quality drag is a real within-sector, profitability "
         "effect (the unprofitable-biotech phenomenon lives in Quality, not in a GICS sector), NOT a "
         "sector artifact.", BODY),
        ("", BODY),
        ("Six factors: Momentum (12-1 price), Size (-ln market cap; small tilt = positive score), "
         "LowVol (-trailing 12m stdev), Value (E/P, B/P, S/P composite), Quality (ROIC, GP/assets, "
         "net margin, -leverage, -accruals, profitable), Growth (revenue YoY + 3y CAGR).", BODY),
        ("", BODY),
        ("Coverage: holdings carried forward between rebalances so the monthly series is continuous. "
         "Names in holdings but absent from the return file (new-at-reconstitution adds) are dropped. "
         "2011 has ~90% weight coverage and is excluded from the headline; 2012 on is ~99-100%.", BODY),
        ("", BODY),
        ("Reading the result: t-stats above ~2.0 are the ones to trust. A positive efficacy with a low "
         "t is suggestive, not decisive. Attribution points are additive within an index but are NOT "
         "comparable across the two indices unless the windows match.", BODY),
    ]
    for i, (txt, fnt) in enumerate(notes, start=1):
        c = wr.cell(row=i, column=1, value=txt); c.font = fnt; c.alignment = Alignment(wrap_text=True)
    wr.column_dimensions["A"].width = 120

    # move README/order: Summary first
    wb.move_sheet("README", offset=len(wb.sheetnames))
    wb.save(out_path)
    print(f"  wrote {out_path}")


def main():
    try:
        months, ret, tkr2cik, cik2tkr = load_returns()
        mcap = load_mktcap(months)
        fund = load_fundamentals()
    except FileNotFoundError as e:
        print(f"[factor] skipped -- input file not found ({e}). Needs the constituent monthly-return, "
              "market-cap, both holdings workbooks, and fundamentals_dera_resolved.csv in R2KG_BASE.")
        return
    print(f"loaded: {len(months)} months, {len(ret)} return series, {len(mcap)} mcap series, "
          f"{len(fund)} fundamental ciks")
    results = {}
    for pat, lbl in [("*Russell*Growth*Holdings*.xlsx", "R2000G"),
                     ("*600*Growth*Holdings*.xlsx", "S&P600G")]:
        res = analyse(pat, lbl, months, ret, mcap, fund, tkr2cik)
        report(res)
        results[lbl] = res
    write_workbook(results, os.path.join(BASE, "R2000G_Factor_Analysis.xlsx"))


if __name__ == "__main__":
    main()
