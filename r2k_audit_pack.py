"""
r2k_audit_pack.py -- traceable audit / working-papers pack, SEPARATE from the IC workbook (so that
stays small) and provided to compliance on request. Everything, all years, constituent-level, in three
layers:

  1. DATA + PROVENANCE (audit_constituents.csv): every constituent x snapshot for BOTH indices, with
     index weight, as-filed metrics, cohort / biotech / financial flags, and per-value provenance
     (fiscal year used, fiscal-year-end and 10-K filing dates, confidence) joined from
     fundamentals_dera.csv -- so any figure walks back to a specific as-filed 10-K.

  2. FORMULA-TRACED AGGREGATES (R2000G_Audit_Workbook.xlsx): every headline aggregate recomputed with a
     LIVE Excel formula (SUMIFS over the constituent rows on the same sheet) beside the pipeline's
     reported value and an automatic tie check. Click a number and trace it to the underlying names,
     weights and fundamentals; the tie column proves the formula recompute equals the published number.

  3. REGRESSIONS (audit_factor_crosssection.csv + audit_factor_coefficients.csv, and a sample month in
     the workbook): the monthly Fama-MacBeth cross-section (each constituent's raw factor values,
     raw and sector-neutral z-scores, index weight, GICS sector, and realized next-month return) and
     the fitted per-month coefficients -- slope, index exposure and contribution per factor, the market
     intercept, and the residual -- so the factor attribution is independently reproducible. The
     workbook reproduces one month in-sheet with LINEST.

  4. FACTOR RELIABILITY (audit_factor_correlation.csv + audit_factor_uni_vs_multi.csv, and the 'Factor
     Reliability' tab): the collinearity and stability diagnostics behind the coefficients -- the average
     cross-sectional correlation matrix of the raw z-score regressors, the VIF per factor, and each
     factor's slope in a UNIVARIATE vs the MULTIVARIATE regression with Fama-MacBeth t-stats -- so a
     reviewer can see directly how much any coefficient could be contaminated by the others (momentum is
     significant and stable across specs; the correlated fundamental factors are read together).

Reuses the SAME functions the pipeline uses (r2k_universe.index_quality / dedup_cik and the
r2k_factor_analysis building blocks), so the audit reconciles to the published workbook by construction.

RUN: python r2k_audit_pack.py     (needs the panel + fundamentals_dera.csv, and the factor input files)
"""
import os
import csv
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

from r2k_universe import get_panel, index_quality, BASE
from r2k_step3_analytics import dedup_cik

OUT_WB = BASE / "R2000G_Audit_Workbook.xlsx"
OUT_CONS = BASE / "audit_constituents.csv"
OUT_XS = BASE / "audit_factor_crosssection.csv"
OUT_COEF = BASE / "audit_factor_coefficients.csv"
FUND_DERA = BASE / "fundamentals_dera.csv"        # per-(cik,fy) provenance from r2k_dera_classify

TITLE = Font(bold=True, size=13, color="1F4E5F")
H = Font(bold=True, size=11, color="1F4E5F")
BODY = Font(size=10)
HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)


def _yn(v):
    return "yes" if v else ("no" if v is False or v == 0 else "")


def _prof(v):
    return "profitable" if v is True else ("unprofitable" if v is False else "")


# --------------------------------------------------------------------------- small stats helpers
def _pearson(xs, ys):
    """Pearson correlation of two equal-length lists; None if undegenerate."""
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    d = (sxx * syy) ** 0.5
    return (sxy / d) if d > 1e-12 else None


def _inv(M):
    """Inverse of an n x n matrix via Gauss-Jordan with partial pivot; None if singular."""
    n = len(M)
    A = [list(M[i]) + [1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(A[r][c]))
        if abs(A[piv][c]) < 1e-12:
            return None
        A[c], A[piv] = A[piv], A[c]
        pv = A[c][c]; A[c] = [v / pv for v in A[c]]
        for r in range(n):
            if r != c and A[r][c]:
                f = A[r][c]; A[r] = [A[r][k] - f * A[c][k] for k in range(2 * n)]
    return [row[n:] for row in A]


def _fm_stat(series):
    """Fama-MacBeth aggregate of a monthly slope series: (mean, t-stat, n). The t-stat divides the mean
    by the standard error of the month-to-month slopes -- so a slope that jumps around across months
    (noise / instability) earns a low t, which is the whole point of the reliability read."""
    s = [x for x in series if x is not None]
    n = len(s)
    if n < 2:
        return (None, None, n)
    m = sum(s) / n
    sd = (sum((x - m) ** 2 for x in s) / (n - 1)) ** 0.5
    t = (m / (sd / n ** 0.5)) if sd > 1e-12 else None
    return (m, t, n)


# --------------------------------------------------------------------------- constituent backup
# (header, extractor) -- the extractor sees the panel row `r` and its provenance dict `p`
CONS = [
    ("index", lambda r, p: r.get("index")),
    ("year", lambda r, p: r.get("year")),
    ("snapshot", lambda r, p: r.get("snapshot")),
    ("cik", lambda r, p: r.get("cik")),
    ("ticker", lambda r, p: r.get("ticker")),
    ("name", lambda r, p: r.get("name")),
    ("weight", lambda r, p: r.get("weight")),
    ("covered", lambda r, p: _yn(r.get("covered"))),
    ("gics", lambda r, p: r.get("gics")),
    ("is_financial", lambda r, p: _yn(r.get("is_financial"))),
    ("biotech", lambda r, p: _yn(r.get("is_biotech"))),
    ("has_revenue", lambda r, p: _yn(r.get("has_rev"))),
    ("prof_ni_label", lambda r, p: _prof(r.get("prof_ni"))),
    ("prof_oi_label", lambda r, p: _prof(r.get("prof_oi"))),
    ("cohort", lambda r, p: r.get("cohort")),
    ("no_revenue", lambda r, p: (_yn(not r.get("has_rev") and not r.get("is_financial"))
                                 if r.get("covered") else "")),
    ("dedup_primary", lambda r, p: r.get("_dedup", "")),
    ("revenue", lambda r, p: r.get("revenue")),
    ("net_income", lambda r, p: r.get("net_income")),
    ("operating_income", lambda r, p: r.get("operating_income")),
    ("gross_profit", lambda r, p: r.get("gross_profit")),
    ("equity", lambda r, p: r.get("equity")),
    ("assets", lambda r, p: r.get("assets")),
    ("debt", lambda r, p: r.get("debt")),
    ("cfo", lambda r, p: r.get("cfo")),
    ("fiscal_year_used", lambda r, p: r.get("fy0")),
    ("form", lambda r, p: p.get("form")),
    ("taxonomy", lambda r, p: p.get("taxonomy")),
    ("fye_date", lambda r, p: p.get("fye_date")),
    ("filed_date", lambda r, p: p.get("filed_date")),
    ("confidence", lambda r, p: p.get("confidence")),
    ("breaks", lambda r, p: p.get("breaks")),
    ("value_provenance", lambda r, p: p.get("provenance")),   # which tag/derivation produced each figure
]
CIDX = {name: i for i, (name, _) in enumerate(CONS)}                 # 0-based column index
CLET = {name: get_column_letter(i + 1) for i, (name, _) in enumerate(CONS)}   # Excel column letter


PROV_FIELDS = ("form", "taxonomy", "fye_date", "filed_date", "confidence", "breaks", "provenance")
# read the pipeline's fundamentals first, then let the resolved variant fill any gaps (it carries the
# derivation string / form / taxonomy even when the plain file's filing-date index was absent)
PROV_FILES = ("fundamentals_dera.csv", "fundamentals_dera_resolved.csv")


def load_provenance():
    """(cik, fiscal_year) -> provenance dict, merged across the fundamentals files (first non-blank wins).
    'provenance' is the per-filing derivation string -- which XBRL tag or accounting-identity derivation
    produced each figure. fye_date/filed_date need dera_filing_index.csv at build time or stay blank."""
    prov = {}
    found = False
    for fname in PROV_FILES:
        path = BASE / fname
        if not path.exists():
            continue
        found = True
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                cik = str(r.get("cik", "")).strip()
                try:
                    fy = int(float(r.get("fiscal_year", "")))
                except (TypeError, ValueError):
                    continue
                d = prov.setdefault((cik, fy), {})
                for k in PROV_FIELDS:
                    v = (r.get(k) or "").strip()
                    if v and not d.get(k):       # first file's non-blank value wins; later files fill gaps
                        d[k] = v
    if not found:
        print(f"  (no provenance source found among {PROV_FILES} -- provenance columns left blank)")
    return prov


def _mark_dedup(rows):
    """Set r['_dedup'] = 'yes' for the FIRST covered row per (index, year, cik) -- the row kept in dollar
    aggregation -- 'no' for collapsed duplicate share classes, '' for uncovered rows. Mirrors dedup_cik."""
    seen = set()
    for r in rows:
        if not r.get("covered"):
            r["_dedup"] = ""
            continue
        key = (r.get("index"), r.get("year"), (r.get("cik") or "").strip())
        if key in seen:
            r["_dedup"] = "no"
        else:
            seen.add(key); r["_dedup"] = "yes"


def write_constituents(rows, prov):
    """Write audit_constituents.csv and return the ordered value rows (for the workbook tabs)."""
    out = []
    for r in rows:
        p = prov.get(((r.get("cik") or "").strip(), r.get("fy0")), {}) if r.get("fy0") is not None else {}
        out.append([fn(r, p) for _, fn in CONS])
    with open(OUT_CONS, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([h for h, _ in CONS])
        w.writerows(out)
    print(f"  wrote {OUT_CONS.name}  ({len(out)} constituent-rows)")
    return out


def _hdr(ws, row, headers):
    for c, h in enumerate(headers, 1):
        x = ws.cell(row, c, h); x.fill = HDR; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)


def _cons_tab(wb, index_tag, valrows):
    """Write one index's constituent rows to a sheet; return the sheet name (for formula references)."""
    nm = f"Constituents {index_tag}"
    ws = wb.create_sheet(nm[:31])
    _hdr(ws, 1, [h for h, _ in CONS])
    for i, vr in enumerate(valrows, 2):
        for c, v in enumerate(vr, 1):
            ws.cell(i, c, v)
    ws.freeze_panes = "A2"
    return ws.title


def _sifs(ct, sumcol, year, *conds):
    """Build a SUMIFS(...) string over full columns of the constituent sheet `ct`, filtered to `year`
    plus optional (column-name, criterion) pairs."""
    parts = [f"'{ct}'!${CLET[sumcol]}:${CLET[sumcol]}",
             f"'{ct}'!${CLET['year']}:${CLET['year']}", str(year)]
    for col, crit in conds:
        parts += [f"'{ct}'!${CLET[col]}:${CLET[col]}", f'"{crit}"']
    return "SUMIFS(" + ",".join(parts) + ")"


# (metric label, formula builder over (ct, year), reported extractor over the index_quality dict q)
AGG = [
    ("Members (n)", lambda ct, y: f"=COUNTIFS('{ct}'!${CLET['year']}:${CLET['year']},{y})",
     lambda q, extra: q.get("n_members")),
    ("Covered (n)", lambda ct, y: f"=COUNTIFS('{ct}'!${CLET['year']}:${CLET['year']},{y},"
                                  f"'{ct}'!${CLET['covered']}:${CLET['covered']},\"yes\")",
     lambda q, extra: q.get("n_cov")),
    ("Unprofitable (NI) weight %",
     lambda ct, y: f"={_sifs(ct, 'weight', y, ('prof_ni_label', 'unprofitable'))}/"
                   f"{_sifs(ct, 'weight', y, ('prof_ni_label', '<>'))}*100",
     lambda q, extra: q.get("unprof_ni")),
    ("Unprofitable (OI) weight %",
     lambda ct, y: f"={_sifs(ct, 'weight', y, ('prof_oi_label', 'unprofitable'))}/"
                   f"{_sifs(ct, 'weight', y, ('prof_oi_label', '<>'))}*100",
     lambda q, extra: q.get("unprof_oi")),
    ("No-revenue weight %",
     lambda ct, y: f"={_sifs(ct, 'weight', y, ('no_revenue', 'yes'))}/"
                   f"{_sifs(ct, 'weight', y, ('covered', 'yes'))}*100",
     lambda q, extra: q.get("no_rev")),
    ("Never-profitable weight %",
     lambda ct, y: f"={_sifs(ct, 'weight', y, ('cohort', 'never_profitable'), ('covered', 'yes'))}/"
                   f"{_sifs(ct, 'weight', y, ('covered', 'yes'))}*100",
     lambda q, extra: q.get("w_never")),
    ("Biotech weight %",
     lambda ct, y: f"={_sifs(ct, 'weight', y, ('biotech', 'yes'))}/{_sifs(ct, 'weight', y)}*100",
     lambda q, extra: extra.get("biotech")),
    ("Total revenue ($B, deduped)",
     lambda ct, y: f"={_sifs(ct, 'revenue', y, ('dedup_primary', 'yes'))}/1000000000",
     lambda q, extra: q.get("tot_rev")),
    ("Total net income ($B, deduped)",
     lambda ct, y: f"={_sifs(ct, 'net_income', y, ('dedup_primary', 'yes'))}/1000000000",
     lambda q, extra: extra.get("tot_ni")),
    # dollar-agg net margin pairs numerator & denominator: only companies with BOTH revenue and net
    # income present (matching dollar_agg, which skips a pair if either side is blank -- else a
    # no-revenue biotech's loss would inflate the numerator with no matching revenue).
    ("Net margin ($agg, deduped) %",
     lambda ct, y: f"={_sifs(ct, 'net_income', y, ('dedup_primary', 'yes'), ('net_income', '<>'), ('revenue', '<>'))}/"
                   f"{_sifs(ct, 'revenue', y, ('dedup_primary', 'yes'), ('net_income', '<>'), ('revenue', '<>'))}*100",
     lambda q, extra: (extra.get("net_da") * 100 if extra.get("net_da") is not None else None)),
]


def _reported(members):
    """The pipeline's own aggregates for one (index, year), via the SAME index_quality() the tabs use,
    plus biotech / total-NI which index_quality does not itself return."""
    q = index_quality(members)
    covd = dedup_cik([r for r in members if r["covered"]])
    wt_all = sum(r["weight"] for r in members) or 1e-9
    extra = {
        "biotech": 100 * sum(r["weight"] for r in members if r.get("is_biotech")) / wt_all,
        "tot_ni": sum((r["net_income"] or 0) for r in covd) / 1e9,
        "net_da": q.get("net_da"),
    }
    return q, extra


def _agg_tab(wb, index_tag, cons_tab, members_by_year):
    ws = wb.create_sheet(f"Aggregates {index_tag}"[:31])
    ws.cell(1, 1, f"{index_tag} -- headline aggregates recomputed by live formula vs the pipeline value").font = TITLE
    ws.cell(2, 1, "'Formula' recomputes from the constituent rows on the "
                  f"'{cons_tab}' sheet (SUMIFS); 'Reported' is the pipeline's own value; 'Tie' should be ~0.").font = BODY
    years = sorted(members_by_year)
    r = 4
    for y in years:
        q, extra = _reported(members_by_year[y])
        ws.cell(r, 1, f"Year {y}").font = H
        _hdr(ws, r + 1, ["Metric", "Formula (recompute)", "Reported (pipeline)", "Tie |Δ|"])
        r += 2
        for label, fbuild, rep in AGG:
            ws.cell(r, 1, label).font = BODY
            ws.cell(r, 2, fbuild(cons_tab, y))
            rv = rep(q, extra)
            ws.cell(r, 3, round(rv, 6) if isinstance(rv, (int, float)) else rv)
            ws.cell(r, 4, f"=ABS(B{r}-C{r})")
            r += 1
        r += 1
    ws.column_dimensions["A"].width = 30
    for col in "BCD":
        ws.column_dimensions[col].width = 22
    ws.freeze_panes = "A4"


# --------------------------------------------------------------------------- factor regression audit
def factor_audit(wb):
    """Emit the monthly factor cross-section and fitted coefficients, reproducing the pipeline's
    regression with the SAME building blocks; add a sample-month LINEST reproduction to the workbook."""
    try:
        import r2k_factor_analysis as fa
    except Exception as e:
        print(f"  (factor audit skipped -- cannot import r2k_factor_analysis: {e})")
        return
    try:
        months, ret, t2c, c2t = fa.load_returns()
        mcap = fa.load_mktcap(months)
        fund = fa.load_fundamentals()
    except FileNotFoundError as e:
        print(f"  (factor audit skipped -- input file not found: {e})")
        return
    FF = fa.FACTORS
    midx = {m: i for i, m in enumerate(months)}
    xs_rows = []                        # cross-section: one row per (index, month, name)
    coef_rows = []                      # coefficients: one row per (index, month)
    sample = None                       # (label, ym, header, matrix, pyslopes) for the in-sheet LINEST

    for pat, label in [("*Russell*Growth*Holdings*.xlsx", "R2000G"), ("*600*Growth*Holdings*.xlsx", "S&P600G")]:
        univ, sect = fa.load_holdings(pat)
        hmonths = sorted(univ)

        def snap(ym):
            prior = [h for h in hmonths if h <= ym]
            return prior[-1] if prior else None

        for ym in months:
            if int(ym[:4]) < fa.Y0:
                continue
            mi = midx.get(ym); s = snap(ym)
            if mi is None or mi + 1 >= len(months) or s is None:
                continue
            univ_ym, sect_ym = univ[s], sect[s]
            names = [c for c in univ_ym if (mi + 1) in ret.get(c, {})]
            if len(names) < fa.MIN_NAMES:
                continue
            w = {c: univ_ym[c] for c in names}; sec = {c: sect_ym.get(c, "?") for c in names}
            raw = fa.build_factor_scores(names, mi, ym, ret, mcap, fund)
            zsn = {ff: fa.zscore(raw[ff], sec) for ff in FF}      # sector-neutral z (efficacy)
            zr = {ff: fa.zscore(raw[ff]) for ff in FF}            # raw z (attribution)
            fwd = {c: ret[c][mi + 1] for c in names}
            for c in names:
                xs_rows.append(
                    [label, ym, c, c2t.get(c, ""), sec[c], round(w[c], 6)]
                    + [round(raw[ff][c], 6) if raw[ff].get(c) is not None else "" for ff in FF]
                    + [round(zr[ff][c], 4) if zr[ff].get(c) is not None else "" for ff in FF]
                    + [round(zsn[ff][c], 4) if zsn[ff].get(c) is not None else "" for ff in FF]
                    + [round(fwd[c], 6)])
            # --- reproduce the RAW Fama-MacBeth regression, exposing slope / exposure / contribution ---
            common = [c for c in names if all(c in zr[ff] for ff in FF)]
            if len(common) < fa.MIN_NAMES:
                continue
            tw = sum(w[c] for c in common) or 1.0
            ir = sum(w[c] * fwd[c] for c in common) / tw
            X = [[1.0] + [zr[ff][c] for ff in FF] for c in common]
            b = fa.ols(X, [fwd[c] for c in common])
            if not b:
                continue
            expo = {ff: sum(w[c] * zr[ff][c] for c in common) / tw for ff in FF}
            contrib = {ff: b[i + 1] * expo[ff] for i, ff in enumerate(FF)}
            market = b[0]; resid = ir - market - sum(contrib.values())
            # sector-adjusted contributions (styles controlled for GICS sector) + sector bucket
            dadj = fa._fm_decompose(common, w, zr, fwd, sect=sec)
            coef_rows.append(
                [label, ym, len(common), round(market, 8)]
                + [round(b[i + 1], 8) for i in range(len(FF))]           # raw slopes
                + [round(expo[ff], 8) for ff in FF]                       # index exposures
                + [round(contrib[ff], 8) for ff in FF]                    # raw contributions
                + [round(resid, 8), round(ir, 8)]
                + ([round(dadj["market"], 8)] + [round(dadj["style"][ff], 8) for ff in FF]
                   + [round(dadj["sect_bucket"], 8), round(dadj["resid"], 8)] if dadj else [""] * (len(FF) + 3)))
            if label == "R2000G":     # keep the most recent R2000G month as the in-sheet sample
                sample = (label, ym, common[:], [[zr[ff][c] for ff in FF] for c in common],
                          [fwd[c] for c in common], [b[i + 1] for i in range(len(FF))], b[0])

    # ---- CSVs ----
    xs_head = (["index", "month", "cik", "ticker", "gics_sector", "index_weight"]
               + [f"raw_{ff}" for ff in FF] + [f"z_{ff}" for ff in FF]
               + [f"zneutral_{ff}" for ff in FF] + ["fwd_return_next_month"])
    with open(OUT_XS, "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f); wtr.writerow(xs_head); wtr.writerows(xs_rows)
    print(f"  wrote {OUT_XS.name}  ({len(xs_rows)} name-months)")
    coef_head = (["index", "month", "n_names", "market_intercept"]
                 + [f"slope_{ff}" for ff in FF] + [f"exposure_{ff}" for ff in FF]
                 + [f"contribution_{ff}" for ff in FF] + ["residual", "index_return"]
                 + ["adj_market"] + [f"adj_contribution_{ff}" for ff in FF] + ["adj_sectors_bucket", "adj_residual"])
    with open(OUT_COEF, "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f); wtr.writerow(coef_head); wtr.writerows(coef_rows)
    print(f"  wrote {OUT_COEF.name}  ({len(coef_rows)} index-months)")

    # ---- workbook: method + one month reproduced in-sheet with LINEST ----
    ws = wb.create_sheet("Regression (sample month)")
    ws.cell(1, 1, "Factor attribution -- one month reproduced from the raw cross-section").font = TITLE
    ws.cell(2, 1, "Each month the constituents' next-month return is regressed on their raw factor "
                  "z-scores (a multivariate OLS / Fama-MacBeth cross-section). A factor's contribution = "
                  "its slope x the index's weighted exposure; market (intercept) + factor contributions + "
                  "residual = the index return. The full monthly inputs and outputs are in "
                  "audit_factor_crosssection.csv and audit_factor_coefficients.csv; below, one month is "
                  "reproduced in-sheet so LINEST matches the coefficients file.").font = BODY
    ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical="top"); ws.row_dimensions[2].height = 60
    if not sample:
        ws.cell(4, 1, "(no sample month available -- factor inputs were absent)").font = BODY
        return
    label, ym, cnames, Z, Y, pyslopes, pyint = sample
    ws.cell(4, 1, f"Sample: {label}  month {ym}  ({len(cnames)} names)").font = H
    hrow = 6
    _hdr(ws, hrow, ["cik"] + [f"z_{ff}" for ff in FF] + ["fwd_return"])
    for i, (c, zrow, y) in enumerate(zip(cnames, Z, Y), hrow + 1):
        ws.cell(i, 1, c)
        for j, zv in enumerate(zrow, 2):
            ws.cell(i, j, round(zv, 4))
        ws.cell(i, 2 + len(FF), round(y, 6))
    last = hrow + len(cnames)
    zc0, zc1 = get_column_letter(2), get_column_letter(1 + len(FF))          # z columns span
    yc = get_column_letter(2 + len(FF))                                       # fwd-return column
    # LINEST reproduces the OLS slopes (returned in REVERSE variable order, intercept last)
    lr = last + 2
    ws.cell(lr, 1, "LINEST(fwd_return, z_1..z_6)  ->  slopes in REVERSE order, then intercept:").font = H
    ws.cell(lr + 1, 1, f"=LINEST({yc}{hrow+1}:{yc}{last},{zc0}{hrow+1}:{zc1}{last},TRUE,FALSE)")
    ws.cell(lr + 3, 1, "Python slopes from audit_factor_coefficients.csv (same month, natural order):").font = H
    _hdr(ws, lr + 4, list(FF) + ["intercept"])
    for j, sv in enumerate(pyslopes, 1):
        ws.cell(lr + 5, j, round(sv, 6))
    ws.cell(lr + 5, len(FF) + 1, round(pyint, 6))
    ws.cell(lr + 6, 1, "Note: LINEST lists coefficients right-to-left (z_6 first ... z_1), intercept last; "
                       "reverse it to line up with the natural order above.").font = Font(italic=True, size=9)
    ws.column_dimensions["A"].width = 16


# --------------------------------------------------------------------------- factor collinearity / stability
OUT_CORR = BASE / "audit_factor_correlation.csv"
OUT_UVM = BASE / "audit_factor_uni_vs_multi.csv"


def factor_reliability(wb):
    """Quantify how much the MULTIVARIATE factor coefficients can be contaminated by cross-factor
    correlation and measurement error, using the SAME building blocks as the attribution:

      A. average cross-sectional CORRELATION MATRIX of the raw factor z-scores (the regressors);
      B. VIF per factor (variance-inflation = diagonal of the inverse correlation matrix): how much a
         factor's coefficient variance is inflated by its correlation with the other five;
      C. UNIVARIATE vs MULTIVARIATE Fama-MacBeth slope per factor, each with its FM t-stat -- a factor
         whose slope barely moves between the two specs (and stays significant) is robust to the joint
         estimation; one that swings is collinearity-sensitive and its multivariate coefficient should
         not be read on its own.

    Both are per index. Correlation/VIF/regressions use the RAW z (zr) the attribution regresses on."""
    try:
        import r2k_factor_analysis as fa
    except Exception as e:
        print(f"  (factor reliability skipped -- cannot import r2k_factor_analysis: {e})")
        return
    try:
        months, ret, t2c, c2t = fa.load_returns()
        mcap = fa.load_mktcap(months)
        fund = fa.load_fundamentals()
    except FileNotFoundError as e:
        print(f"  (factor reliability skipped -- input file not found: {e})")
        return
    FF = fa.FACTORS; nF = len(FF)
    midx = {m: i for i, m in enumerate(months)}
    per_index = {}   # label -> dict(corr_sum, corr_n, uni{ff:[]}, multi{ff:[]})

    for pat, label in [("*Russell*Growth*Holdings*.xlsx", "R2000G"), ("*600*Growth*Holdings*.xlsx", "S&P600G")]:
        univ, sect = fa.load_holdings(pat)
        hmonths = sorted(univ)

        def snap(ym):
            prior = [h for h in hmonths if h <= ym]
            return prior[-1] if prior else None

        corr_sum = [[0.0] * nF for _ in range(nF)]; corr_n = [[0] * nF for _ in range(nF)]
        uni = {ff: [] for ff in FF}; multi = {ff: [] for ff in FF}
        for ym in months:
            if int(ym[:4]) < fa.Y0:
                continue
            mi = midx.get(ym); s = snap(ym)
            if mi is None or mi + 1 >= len(months) or s is None:
                continue
            univ_ym = univ[s]
            names = [c for c in univ_ym if (mi + 1) in ret.get(c, {})]
            if len(names) < fa.MIN_NAMES:
                continue
            w = {c: univ_ym[c] for c in names}
            raw = fa.build_factor_scores(names, mi, ym, ret, mcap, fund)
            zr = {ff: fa.zscore(raw[ff]) for ff in FF}                 # raw z = the attribution regressors
            common = [c for c in names if all(c in zr[ff] for ff in FF)]
            if len(common) < fa.MIN_NAMES:
                continue
            fwd = {c: ret[c][mi + 1] for c in common}
            cols = {ff: [zr[ff][c] for c in common] for ff in FF}
            # A. accumulate this month's pairwise correlations
            for a in range(nF):
                for b in range(a, nF):
                    r = _pearson(cols[FF[a]], cols[FF[b]])
                    if r is not None:
                        corr_sum[a][b] += r; corr_n[a][b] += 1
                        if a != b:
                            corr_sum[b][a] += r; corr_n[b][a] += 1
            # C. multivariate slopes (all six + intercept) and univariate slopes (one factor + intercept)
            y = [fwd[c] for c in common]
            bm = fa.ols([[1.0] + [zr[ff][c] for ff in FF] for c in common], y)
            if bm:
                for i, ff in enumerate(FF):
                    multi[ff].append(bm[i + 1])
            for ff in FF:
                bu = fa.ols([[1.0, zr[ff][c]] for c in common], y)
                if bu:
                    uni[ff].append(bu[1])
        # average correlation matrix
        corr = [[(corr_sum[a][b] / corr_n[a][b]) if corr_n[a][b] else (1.0 if a == b else 0.0)
                 for b in range(nF)] for a in range(nF)]
        inv = _inv(corr)
        vif = [inv[i][i] if inv else None for i in range(nF)]
        per_index[label] = dict(corr=corr, vif=vif, uni=uni, multi=multi)

    if not per_index:
        print("  (factor reliability skipped -- no index-months produced)")
        return

    # ---- CSVs ----
    with open(OUT_CORR, "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f); wtr.writerow(["index", "factor"] + list(FF) + ["VIF"])
        for label, d in per_index.items():
            for i, ff in enumerate(FF):
                wtr.writerow([label, ff] + [round(d["corr"][i][j], 4) for j in range(nF)]
                             + [round(d["vif"][i], 3) if d["vif"][i] is not None else ""])
    print(f"  wrote {OUT_CORR.name}")

    def _ann(m):     # monthly slope (return per +1 SD of z) -> annualized %
        return round(m * 12 * 100, 2) if m is not None else None

    uvm_rows = []
    for label, d in per_index.items():
        for ff in FF:
            um, ut, un = _fm_stat(d["uni"][ff])
            mm, mt, mn = _fm_stat(d["multi"][ff])
            dchg = (mm - um) if (um is not None and mm is not None) else None
            pct = (100 * abs(dchg) / abs(um)) if (dchg is not None and um not in (None, 0)) else None
            # verdict: reliable only if the multivariate slope is significant AND close to univariate
            rel = (mt is not None and abs(mt) >= 2.0)
            stable = (pct is not None and pct <= 25)
            verdict = ("reliable (significant, stable across specs)" if rel and stable else
                       "significant but collinearity-sensitive" if rel and not stable else
                       "not individually significant")
            uvm_rows.append([label, ff, un, _ann(um), round(ut, 2) if ut is not None else None,
                             _ann(mm), round(mt, 2) if mt is not None else None,
                             _ann(dchg), round(pct, 0) if pct is not None else None, verdict])
    with open(OUT_UVM, "w", newline="", encoding="utf-8") as f:
        wtr = csv.writer(f)
        wtr.writerow(["index", "factor", "n_months", "univariate_slope_ann_pct", "univariate_t",
                      "multivariate_slope_ann_pct", "multivariate_t", "delta_ann_pct", "abs_pct_change", "verdict"])
        wtr.writerows(uvm_rows)
    print(f"  wrote {OUT_UVM.name}")

    # ---- workbook tab ----
    ws = wb.create_sheet("Factor Reliability")
    ws.cell(1, 1, "Factor reliability -- collinearity (correlation, VIF) and coefficient stability "
                  "(univariate vs multivariate)").font = TITLE
    ws.cell(2, 1, "The attribution is ONE multivariate regression per month, so correlated factors share "
                  "explanatory power and errors in one regressor can move the others' coefficients. This tab "
                  "sizes that: (A) how correlated the regressors are, (B) VIF = how much each coefficient's "
                  "variance is inflated by that correlation, (C) whether each factor's slope survives moving "
                  "from a one-factor regression to the full six-factor regression. A factor that is "
                  "significant AND stable across the two specs is not an artifact of the joint estimation.").font = BODY
    ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical="top"); ws.row_dimensions[2].height = 74
    r = 4
    for label in per_index:
        d = per_index[label]
        ws.cell(r, 1, f"{label} — A. Average cross-sectional correlation of the raw factor z-scores "
                      "(|r|>0.5 = materially collinear)").font = H
        r += 1
        _hdr(ws, r, [""] + list(FF));
        for i, ff in enumerate(FF):
            ws.cell(r + 1 + i, 1, ff).font = Font(bold=True, size=10)
            for j in range(nF):
                cell = ws.cell(r + 1 + i, 2 + j, round(d["corr"][i][j], 2))
                if i != j and abs(d["corr"][i][j]) > 0.5:
                    cell.font = Font(bold=True, color="B00000")
        r += nF + 2
        ws.cell(r, 1, f"{label} — B. Variance Inflation Factor (VIF < 5 low · 5–10 moderate · > 10 high "
                      "collinearity)").font = H
        r += 1
        _hdr(ws, r, list(FF));
        for i in range(nF):
            v = d["vif"][i]
            cell = ws.cell(r + 1, 1 + i, round(v, 2) if v is not None else "n/a")
            if v is not None and v > 5:
                cell.font = Font(bold=True, color="B00000")
        r += 3
        ws.cell(r, 1, f"{label} — C. Univariate vs multivariate Fama-MacBeth slope (annualized %, per +1 SD "
                      "of the factor) with FM t-stats").font = H
        r += 1
        _hdr(ws, r, ["Factor", "Univariate slope %", "Univ t", "Multivariate slope %", "Multi t",
                     "Δ slope (pts)", "|Δ| / univ %", "Verdict"])
        r += 1
        for row in [x for x in uvm_rows if x[0] == label]:
            _, ff, un, us, ut, ms, mt, dl, pc, verdict = row
            vals = [ff, us, ut, ms, mt, dl, pc, verdict]
            for c, v in enumerate(vals, 1):
                cell = ws.cell(r, c, v)
                if c == 1:
                    cell.font = Font(bold=True, size=10)
                if c == 8 and verdict.startswith("reliable"):
                    cell.font = Font(bold=True, color="1F6E1F")
            r += 1
        r += 2
    ws.cell(r, 1, "How to read it: Momentum uses only returns (no accounting data), is significant, and its "
                  "slope barely moves between the univariate and multivariate specs — so its result is not a "
                  "product of the joint regression. The fundamental factors (Value / Quality / Growth) are "
                  "correlated (they load on the same profitability/tail axis), so their individual multivariate "
                  "coefficients are a partition of a shared effect and should not be read in isolation; the "
                  "quality conclusion is corroborated separately by the profitability-cohort attribution and the "
                  "biotech contribution, which use no factor regression. Sector-neutral demeaning (the efficacy "
                  "lens) further reduces sector-driven correlation; VIF here is for the raw-z attribution, the "
                  "conservative case.").font = Font(italic=True, size=9)
    ws.cell(r, 1).alignment = Alignment(wrap_text=True, vertical="top"); ws.row_dimensions[r].height = 88
    ws.column_dimensions["A"].width = 24
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 17
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=8)


# --------------------------------------------------------------------------- readme + main
def readme(wb):
    ws = wb.create_sheet("READ ME", 0)
    ws.column_dimensions["A"].width = 118
    notes = [
        ("Audit / Working Papers -- how to trace any number", TITLE),
        ("", BODY),
        ("This workbook is a SEPARATE audit companion to the IC deliverable (R2000G_SmallCapGrowth_"
         "Benchmark_Review.xlsx); it is not part of it. Everything reconciles to the published numbers "
         "because it is built from the same panel and the same functions.", BODY),
        ("", BODY),
        ("1. TRACE AN AGGREGATE.  On each 'Aggregates <index>' tab, every headline figure has a live "
         "Excel formula that recomputes it with SUMIFS over the constituent rows on the matching "
         "'Constituents <index>' tab, next to the pipeline's reported value and a Tie column (|Δ|, ~0). "
         "Follow the formula into the constituent rows to see the names, weights and fundamentals behind "
         "the number.", BODY),
        ("", BODY),
        ("2. TRACE A FUNDAMENTAL.  The constituent tabs (and audit_constituents.csv) carry, per name per "
         "snapshot: the as-filed revenue / net income / etc.; the fiscal year used; the filing FORM and "
         "TAXONOMY; a confidence flag and any accounting-identity BREAKS; and a 'value_provenance' string "
         "-- the per-filing derivation showing which XBRL tag or identity produced each figure (e.g. "
         "'revenue=NetInterestIncome+Noninterest'). 'dedup_primary = yes' marks the one row per company "
         "kept in dollar totals (dual share classes share a CIK and identical fundamentals). Note: the "
         "fiscal-year-end and 10-K filing DATES populate only when dera_filing_index.csv is present at "
         "build time; SEC accession numbers can be added on request by joining the DERA filing index.", BODY),
        ("", BODY),
        ("3. REPRODUCE THE REGRESSIONS.  The factor attribution is a monthly multivariate OLS "
         "(Fama-MacBeth) of each constituent's next-month return on its raw factor z-scores. "
         "audit_factor_crosssection.csv is every input (name x month: raw factor values, raw and "
         "sector-neutral z-scores, weight, sector, realized next-month return); "
         "audit_factor_coefficients.csv is every output (month: slope, exposure and contribution per "
         "factor, market intercept, residual, and the sector-adjusted version). The 'Regression (sample "
         "month)' tab reproduces one month in-sheet with LINEST. Contribution = slope x exposure; "
         "market + Σ contributions + residual = the index return, by construction.", BODY),
        ("", BODY),
        ("4. TEST THE FACTOR COEFFICIENTS.  The 'Factor Reliability' tab quantifies how much the "
         "multivariate coefficients can be moved by cross-factor correlation and measurement error: (A) the "
         "average cross-sectional correlation matrix of the raw z-score regressors, (B) VIF per factor "
         "(variance inflation from that correlation), and (C) each factor's slope in a one-factor regression "
         "vs the full six-factor regression, with Fama-MacBeth t-stats. A factor that is significant AND "
         "stable across the two specs (momentum) is not an artifact of the joint estimation; correlated "
         "fundamental factors (Value/Quality/Growth) share explanatory power, so their individual "
         "coefficients are a partition of one effect and are read together, not in isolation. Flat copies: "
         "audit_factor_correlation.csv and audit_factor_uni_vs_multi.csv.", BODY),
        ("", BODY),
        ("Provenance note: fundamentals are as originally filed (by original accession, no restatement "
         "blending); the per-value tag/derivation that produced each figure is in fundamentals_dera.csv "
         "(the file this pack joins for filing dates and confidence).", BODY),
    ]
    for i, (t, fnt) in enumerate(notes, 1):
        c = ws.cell(i, 1, t); c.font = fnt; c.alignment = Alignment(wrap_text=True, vertical="top")


def main():
    rows = get_panel(index=None)                    # both indices, all years, constituent-level
    if not rows:
        raise SystemExit("!! panel is empty -- run the pipeline (r2k_universe / r2k_report) first.")
    _mark_dedup(rows)
    prov = load_provenance()
    valrows = write_constituents(rows, prov)

    wb = openpyxl.Workbook(); wb.remove(wb.active)
    by_index = {}
    for r in rows:
        by_index.setdefault(r["index"], []).append(r)
    tagmap = {"R2KG": "R2KG", "SP600G": "SP600G"}
    for idx, members in by_index.items():
        tag = tagmap.get(idx, idx)
        # constituent tab (values already in `valrows`, but re-filter by index to keep sheets per index)
        sub = [vr for vr in valrows if vr[CIDX["index"]] == idx]
        cons_tab = _cons_tab(wb, tag, sub)
        by_year = {}
        for r in members:
            by_year.setdefault(r["year"], []).append(r)
        _agg_tab(wb, tag, cons_tab, by_year)

    factor_audit(wb)
    factor_reliability(wb)
    readme(wb)
    # order: READ ME, Aggregates/Constituents per index, Regression, Factor Reliability
    order = ["READ ME"] + [s for s in wb.sheetnames if s.startswith("Aggregates")] \
        + [s for s in wb.sheetnames if s.startswith("Constituents")] \
        + [s for s in wb.sheetnames if s.startswith("Regression")] \
        + [s for s in wb.sheetnames if s.startswith("Factor Reliability")]
    wb._sheets.sort(key=lambda s: order.index(s.title) if s.title in order else 999)
    wb.save(OUT_WB)
    print(f"  wrote {OUT_WB.name}  ({len(wb.sheetnames)} tabs)")


if __name__ == "__main__":
    main()
