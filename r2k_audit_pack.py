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
    readme(wb)
    # order: READ ME, Aggregates/Constituents per index, Regression
    order = ["READ ME"] + [s for s in wb.sheetnames if s.startswith("Aggregates")] \
        + [s for s in wb.sheetnames if s.startswith("Constituents")] \
        + [s for s in wb.sheetnames if s.startswith("Regression")]
    wb._sheets.sort(key=lambda s: order.index(s.title) if s.title in order else 999)
    wb.save(OUT_WB)
    print(f"  wrote {OUT_WB.name}  ({len(wb.sheetnames)} tabs)")


if __name__ == "__main__":
    main()
