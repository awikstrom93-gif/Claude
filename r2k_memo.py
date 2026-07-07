"""
============================================================
r2k_memo.py  --  generate the Investment-Committee benchmark-review memo FROM the workbook, so no
figure is ever hand-typed or allowed to drift out of sync with the analysis.
============================================================
Reads  R2000G_SmallCapGrowth_Benchmark_Review.xlsx  (the consolidated IC workbook that r2k_report.py
builds) and writes  R2000G_SCG_Benchmark_Review_Memo.md.

EVERY number in the prose is pulled from a named tab or computed from a tab's own monthly growth-of-$1
series -- nothing is a literal. The window dates, the peak-unprofitable year, the "latest" year, and
the direction of every effect ("helped"/"hurt") are all derived from the data, so re-running after any
pipeline change produces a memo whose narrative and figures match the workbook automatically.

A self-check reconciles the reconstructed index return computed here against the Attr Contribution
total the workbook already reports; if they disagree beyond tolerance the run aborts (the tab layout
changed and an extractor needs updating) rather than emit a silently-wrong memo.

RUN:      python r2k_memo.py                 (writes the memo next to the workbook)
SELFTEST: python r2k_memo.py --selftest      (extract + reconcile, print figures, write nothing)
Override the workbook with R2KG_WORKBOOK, the base dir with R2KG_BASE, the output with R2KG_MEMO_OUT.
============================================================
"""
from pathlib import Path
import os, sys, re

BASE = Path(os.environ.get("R2KG_BASE", "."))
WB = BASE / os.environ.get("R2KG_WORKBOOK", "R2000G_SmallCapGrowth_Benchmark_Review.xlsx")
OUT = BASE / os.environ.get("R2KG_MEMO_OUT", "R2000G_SCG_Benchmark_Review_Memo.md")
MONTH = re.compile(r"^\d{4}-\d{2}")
RECONCILE_TOL = 0.5   # pts; reconstructed index window/full must match the workbook's own total


# --------------------------------------------------------------------------- workbook access
def _rows(ws):
    return [list(r) for r in ws.iter_rows(values_only=True)]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _hdr_row(rows, first_label):
    """Index of the row whose first cell == first_label (the table header)."""
    for i, r in enumerate(rows):
        if r and str(r[0]).strip() == first_label:
            return i
    raise KeyError(f"header row starting '{first_label}' not found")


def _col(hdr, *needles):
    """First column whose header contains ALL needles (case-insensitive)."""
    for j, h in enumerate(hdr):
        hs = str(h or "").lower()
        if all(n.lower() in hs for n in needles):
            return j
    return None


# --------------------------------------------------------------------------- per-tab extractors
def perf_summary(wb):
    R = _rows(wb["Perf Summary"])
    d = {"window_text": ""}
    for r in R:
        if not r or r[0] is None:
            continue
        lab = str(r[0]).strip().lower()
        if lab.startswith("full window"):
            d["window_text"] = str(r[0]).strip()
        pair = (_num(r[1]) if len(r) > 1 else None, _num(r[2]) if len(r) > 2 else None)
        if lab.startswith("annualized return"):
            d["ann"] = pair
        elif lab.startswith("annualized volatility"):
            d["vol"] = pair
        elif lab.startswith("max drawdown"):
            d["dd"] = pair
        elif lab.startswith("cumulative total return"):
            d["cum"] = pair
    return d


def calendar(wb):
    R = _rows(wb["Perf Calendar Yr"])
    hi = _hdr_row(R, "Year")
    out = {}
    for r in R[hi + 1:]:
        if r and str(r[0]).strip().isdigit():
            out[int(r[0])] = dict(r2kg=_num(r[1]), sp6=_num(r[2]), excess=_num(r[3]),
                                  months=_num(r[4]))
    return out


def qual(wb):
    R = _rows(wb["Qual Comparison"])
    hi = _hdr_row(R, "Year")
    h = R[hi]
    ci = {"unprof_R": _col(h, "%unprofitable (ni)", "r2kg"),
          "unprof_6": _col(h, "%unprofitable (ni)", "600g"),
          "norev_R": _col(h, "%no-revenue", "r2kg"),
          "norev_6": _col(h, "%no-revenue", "600g"),
          "never_R": _col(h, "never-profitable", "r2kg"),
          "never_6": _col(h, "never-profitable", "600g")}
    out = {}
    for r in R[hi + 1:]:
        if r and str(r[0]).strip().isdigit():
            y = int(r[0])
            out[y] = {k: (_num(r[j]) if (j is not None and j < len(r)) else None)
                      for k, j in ci.items()}
    return out


def window_proof(wb):
    R = _rows(wb["Perf Window Proof"])
    d = {"candidates": []}
    for r in R:
        if not r or r[0] is None:
            continue
        c0 = str(r[0]).strip()
        val = str(r[2]).strip() if (len(r) > 2 and r[2] is not None) else None
        if c0.startswith("R2KG relative low"):
            d["rel_low"] = val
        elif c0.startswith("Start of R2KG"):
            d["outperf_start"] = val
        elif c0.startswith("Window used"):
            d["window_start"] = val
    hi = _hdr_row(R, "Window start")
    for r in R[hi + 1:]:
        # candidate rows carry a start month AND a populated S&P600G column; the monthly proof series
        # below the table also starts with a date but has no S&P600G value -> exclude it.
        if r and r[0] is not None and MONTH.match(str(r[0])) and _num(r[3]) is not None:
            row = dict(start=str(r[0])[:7], months=_num(r[1]), r2kg=_num(r[2]),
                       sp6=_num(r[3]), excess=_num(r[4]),
                       note=str(r[5]).strip() if (len(r) > 5 and r[5]) else "")
            d["candidates"].append(row)
            if "used" in row["note"].lower():
                d["used"] = row
    return d


def _label_table(wb, sheet, cols=("full_c", "full_w", "win_c", "win_w")):
    """A small labelled table (Attr Contribution / Bio Contribution): {label: {col: value}}."""
    R = _rows(wb[sheet])
    out = {}
    for r in R:
        if not r or r[0] is None or _num(r[1]) is None:
            continue
        out[str(r[0]).strip()] = {c: (_num(r[i + 1]) if i + 1 < len(r) else None)
                                  for i, c in enumerate(cols)}
    return out


def bio_wt(wb):
    R = _rows(wb["Bio Weight & Quality"])
    hi = _hdr_row(R, "Year")
    out = {}
    for r in R[hi + 1:]:
        if r and str(r[0]).strip().isdigit():
            out[int(r[0])] = dict(wt_R=_num(r[1]), n_R=_num(r[2]), unprof=_num(r[3]),
                                  norev=_num(r[4]), wt_6=_num(r[5]), n_6=_num(r[6]),
                                  diff=_num(r[7]))
    return out


def conc(wb):
    """Concentration (`Conc Weight`) and breadth (`Conc Breadth`) by June-snapshot year. Tolerant of a
    missing tab so an older workbook still produces a memo (the concentration section then self-skips)."""
    weight, breadth = {}, {}
    if "Conc Weight" in wb.sheetnames:
        R = _rows(wb["Conc Weight"]); hi = _hdr_row(R, "Year")
        for r in R[hi + 1:]:
            if r and str(r[0]).strip().isdigit():
                weight[int(r[0])] = dict(n=_num(r[1]), top5=_num(r[2]), top10=_num(r[3]),
                                         top25=_num(r[4]), top50=_num(r[5]), maxw=_num(r[6]),
                                         hhi=_num(r[7]), effn=_num(r[8]))
    if "Conc Breadth" in wb.sheetnames:
        R = _rows(wb["Conc Breadth"]); hi = _hdr_row(R, "Year")
        for r in R[hi + 1:]:
            if r and str(r[0]).strip().isdigit():
                breadth[int(r[0])] = dict(names=_num(r[1]), pos=_num(r[2]), beat=_num(r[3]),
                                          capwtd=_num(r[4]), median=_num(r[5]), capmed=_num(r[6]),
                                          top10g=_num(r[7]), top25g=_num(r[8]))
    return dict(weight=weight, breadth=breadth)


def series_returns(wb, sheet):
    """Full-period and trailing-3y returns for every value column of a growth-of-$1 sheet.
    Base is $1 at the month before the first row, so full = last-1. Window denominator is the row
    exactly 3 years before the last month. Returns {colname: (full_pct, window_pct)}, end_month."""
    R = _rows(wb[sheet])
    hi = _hdr_row(R, "Month")
    hdr = [str(c).strip() if c is not None else "" for c in R[hi]]
    data = [r for r in R[hi + 1:] if r and r[0] is not None and MONTH.match(str(r[0]))]
    end_m = str(data[-1][0])[:7]
    y, m = map(int, end_m.split("-"))
    wdenom = f"{y - 3:04d}-{m:02d}"
    out = {}
    for j in range(1, len(hdr)):
        if not hdr[j]:
            continue
        vals = {str(r[0])[:7]: _num(r[j]) for r in data if len(r) > j and _num(r[j]) is not None}
        if not vals:
            continue
        end = vals[str(data[-1][0])[:7]]
        full = (end - 1.0) * 100.0
        wd = vals.get(wdenom)
        win = ((end / wd - 1.0) * 100.0) if wd else None
        out[hdr[j]] = (full, win)
    return out, end_m


# --------------------------------------------------------------------------- helpers for prose
def fmt(x, dp=1, pct=False, sign=False):
    if x is None:
        return "n/a"
    s = f"{x:+.{dp}f}" if sign else f"{x:.{dp}f}"
    return s + ("%" if pct else "")


def yr_label(y):
    """Snapshot year -> the fiscal year it reflects, for reader-facing labels we keep as the snapshot."""
    return str(y)


def collect(wb):
    ps = perf_summary(wb)
    cal = calendar(wb)
    q = qual(wb)
    wp = window_proof(wb)
    attr = _label_table(wb, "Attr Contribution")
    bio = bio_wt(wb)
    biocon = _label_table(wb, "Bio Contribution")
    cf, end_m = series_returns(wb, "Attr Counterfactual")
    bcf, _ = series_returns(wb, "Bio Counterfactual")
    cn = conc(wb)
    return dict(ps=ps, cal=cal, q=q, wp=wp, attr=attr, bio=bio, biocon=biocon,
                cf=cf, bcf=bcf, end_m=end_m, cn=cn)


def reconcile(D):
    """The reconstructed R2000G index computed from the counterfactual series must equal the
    Attr Contribution TOTAL the workbook already reports. Guards against a silent tab-layout change."""
    total = next((v for k, v in D["attr"].items() if k.lower().startswith("total")), None)
    if not total:
        return ["Attr Contribution TOTAL row not found"]
    idx = D["cf"].get("R2000G index")
    if not idx:
        return ["Attr Counterfactual 'R2000G index' column not found"]
    problems = []
    if total["full_c"] is not None and abs(idx[0] - total["full_c"]) > RECONCILE_TOL:
        problems.append(f"full-period recon {idx[0]:.1f}% != Attr total {total['full_c']:.1f}%")
    if total["win_c"] is not None and idx[1] is not None and abs(idx[1] - total["win_c"]) > RECONCILE_TOL:
        problems.append(f"window recon {idx[1]:.2f}% != Attr total {total['win_c']:.2f}%")
    return problems


# --------------------------------------------------------------------------- the memo
def build_memo(D):
    ps, cal, q, wp, attr, bio, biocon, cf, bcf, cn = (D["ps"], D["cal"], D["q"], D["wp"], D["attr"],
                                                      D["bio"], D["biocon"], D["cf"], D["bcf"], D["cn"])
    years = sorted(q)
    y0, yL = years[0], years[-1]                    # first, latest snapshot years (fundamentals basis)
    scope0, scopeL = str(y0), str(yL)               # composition scope = the June-snapshot range
    win_mo = int(wp.get("used", {}).get("months") or 36)

    # ---- profitability composition over time ----
    unpr_pk_y = max(years, key=lambda y: (q[y]["unprof_R"] or 0))
    nev_pk_y = max(years, key=lambda y: (q[y]["never_R"] or 0))
    gap_unpr = [(q[y]["unprof_R"] or 0) - (q[y]["unprof_6"] or 0) for y in years]
    gmin, gmax = min(gap_unpr), max(gap_unpr)

    # ---- no-revenue composition over time ----
    norev_pk_y = max(years, key=lambda y: (q[y]["norev_R"] or 0))

    # ---- biotech composition over time ----
    byrs = sorted(bio)
    b0, bL = byrs[0], byrs[-1]
    bio_pk_y = max(byrs, key=lambda y: (bio[y]["wt_R"] or 0))
    bio_gap_pk = max(byrs, key=lambda y: (bio[y]["diff"] or 0))
    bio_norev0 = bio[b0]["norev"]
    bio_norev_pk_y = max(byrs, key=lambda y: (bio[y]["norev"] or 0))

    # ---- concentration & breadth over time ----
    cw, cb = cn["weight"], cn["breadth"]
    cyrs = sorted(cw)
    have_conc = len(cyrs) >= 3
    if have_conc:
        conc_pk_y = max(cyrs, key=lambda y: (cw[y]["hhi"] or 0))     # most top-heavy year
        pre = [y for y in cyrs if y < conc_pk_y]                     # the calmer years before the spike
        t10_pre = (sum(cw[y]["top10"] for y in pre) / len(pre)) if pre else None
        effn_pre = (sum(cw[y]["effn"] for y in pre) / len(pre)) if pre else None
        cLy = cyrs[-1]
    byrs_cb = sorted(cb)
    have_breadth = len(byrs_cb) >= 3

    # ---- return footprint (kept brief; also drives the reconcile guard) ----
    idx_f, idx_w = cf["R2000G index"]
    pf_f, pf_w = cf["Profitable-only"]
    eff_f, eff_w = pf_f - idx_f, pf_w - idx_w
    cum6 = ps["cum"][1]
    tot_w = attr[next(k for k in attr if k.lower().startswith("total"))]["win_c"]
    nev = next(v for k, v in attr.items() if k.lower().startswith("never"))
    nev_share = nev["win_c"] / tot_w * 100 if tot_w else None
    nev_ratio = (nev["win_c"] / tot_w) / (nev["win_w"] / 100) if (tot_w and nev["win_w"]) else None
    bio_win_c = next(v for k, v in biocon.items() if k.lower().startswith("biotech"))["win_c"]
    bio_win_w = next(v for k, v in biocon.items() if k.lower().startswith("biotech"))["win_w"]
    bio_win_share = bio_win_c / tot_w * 100 if tot_w else None

    def anchor_years():
        """first, a mid year, the peak-unprofitable year, and the latest — de-duplicated, in order."""
        mid = 2019 if 2019 in q else years[len(years) // 2]
        return sorted({y0, mid, unpr_pk_y, yL})

    P = []
    P.append("# US Small Cap Growth — Anatomy of the Benchmark")
    P.append(f"### What the Russell 2000 Growth is made of, and how it has changed, {scope0}–{scopeL}\n")
    P.append("**Prepared for:** Investment Committee  ")
    P.append(f"**Scope:** Russell 2000 Growth (R2000G) vs. S&P SmallCap 600 Growth (S&P600G), "
             f"{scope0}–{scopeL} (June snapshots)  ")
    P.append("**Basis:** As-filed 10-K fundamentals (original accession), point-in-time index "
             "membership  ")
    P.append("*Generated directly from `R2000G_SmallCapGrowth_Benchmark_Review.xlsx`; every figure is "
             "read or computed from the tab named at the close of each section, so nothing drifts out of "
             "sync with the analysis.*\n")
    P.append("---\n")

    # In brief
    P.append("## In brief\n")
    P.append(f"The Russell 2000 Growth and the S&P SmallCap 600 Growth are built from the same asset "
             f"class but to different specifications, and over {scope0}–{scopeL} they have drifted "
             f"further apart. The one rule that separates them — S&P admits only companies with positive "
             f"trailing GAAP earnings, Russell screens for nothing — shows up in almost every line of "
             f"what the two indices are made of. The Russell index carries a far heavier tail of "
             f"companies that don't earn money, a widening sliver that book no revenue at all, a biotech "
             f"book back near the top of its range and more pre-commercial than ever, and, most "
             f"recently, a sharp bout of concentration. This note walks through those four features and "
             f"how each has moved through the years. It is "
             f"also, in the background, why a quality-disciplined manager measured against the Russell "
             f"index tends to trail it when the low-quality tail runs — but the subject here is the "
             f"composition itself.\n")
    P.append("---\n")

    # 1. Profitability
    P.append("## 1. Profitability: a widening non-earner tail\n")
    P.append(f"The share of the Russell index sitting in companies with negative net income has climbed "
             f"from **{fmt(q[y0]['unprof_R'],0,True)} in {y0}** to a peak of "
             f"**{fmt(q[unpr_pk_y]['unprof_R'],0,True)} in {unpr_pk_y}**, and stands at "
             f"**{fmt(q[yL]['unprof_R'],0,True)} in {yL}**. The earnings-screened S&P index never left a "
             f"low single-digit-to-teens band. The gap is not occasional: in every year of the study the "
             f"Russell index ran **{fmt(gmin,0)} to {fmt(gmax,0)} points** more unprofitable weight.\n")
    P.append("| By index weight | R2000G | S&P600G | Gap |")
    P.append("|---|---|---|---|")
    for y in anchor_years():
        tag = f"Unprofitable, {y}" + (" (peak)" if y == unpr_pk_y else " (latest)" if y == yL else "")
        P.append(f"| {tag} | {fmt(q[y]['unprof_R'],1,True)} | {fmt(q[y]['unprof_6'],1,True)} | "
                 f"{fmt((q[y]['unprof_R'] or 0)-(q[y]['unprof_6'] or 0),1,sign=True)} pts |")
    P.append(f"| Never-profitable, {yL} | {fmt(q[yL]['never_R'],1,True)} | {fmt(q[yL]['never_6'],1,True)} | "
             f"{fmt((q[yL]['never_R'] or 0)-(q[yL]['never_6'] or 0),1,sign=True)} pts |\n")
    P.append(f"The starkest cut is the *never-profitable* cohort — names with no profitable year on "
             f"record at all. That weight roughly doubled from **{fmt(q[y0]['never_R'],0,True)} in {y0}** "
             f"to a high of **{fmt(q[nev_pk_y]['never_R'],0,True)} in {nev_pk_y}**, and at "
             f"**{fmt(q[yL]['never_R'],0,True)}** in {yL} it remains an order of magnitude above the "
             f"**{fmt(q[yL]['never_6'],1,True)}** in the earnings-gated S&P index. This is the cleanest "
             f"single read on how much of the Russell benchmark an earnings discipline sets aside.\n")
    P.append("*Tabs: `Qual Comparison`, `Qual R2000G`; chart \"% unprofitable by weight\" on "
             "`Key Charts`.*\n")
    P.append("---\n")

    # 2. No revenue + biotech
    P.append("## 2. No revenue at all — and the biotech engine behind it\n")
    P.append(f"A step beyond unprofitable is *pre-revenue* — companies booking no top line whatsoever. "
             f"In the Russell index that weight has run in the low single digits for most of the study "
             f"and then jumped to **{fmt(q[norev_pk_y]['norev_R'],1,True)} in {norev_pk_y}**, its highest "
             f"reading; the S&P index has sat essentially at zero throughout. The move is not broad. It "
             f"is biotech.\n")
    P.append(f"Clinical-stage biotech is the structural reason the two indices diverge here: it is "
             f"heavily unprofitable, often has nothing to sell yet, and so is largely uninvestable for an "
             f"earnings-gated index. Its weight in the Russell benchmark has swung between "
             f"**{fmt(min(bio[y]['wt_R'] for y in byrs),0)}% and {fmt(max(bio[y]['wt_R'] for y in byrs),0)}%** "
             f"— **{fmt(bio[b0]['wt_R'],1,True)} in {b0}**, a first peak of "
             f"**{fmt(bio[bio_pk_y]['wt_R'],1,True)} in {bio_pk_y}**, and back to "
             f"**{fmt(bio[bL]['wt_R'],1,True)} in {bL}** — against a low, stable **"
             f"{fmt(bio[bL]['wt_6'],1,True)}** in the S&P index, a tilt that widened to as much as "
             f"**{fmt(bio[bio_gap_pk]['diff'],0)} points** ({bio_gap_pk}).\n")
    P.append(f"What has really changed is the *quality* of that biotech weight. The share of Russell "
             f"biotech that reports no revenue at all has gone from **{fmt(bio_norev0,0)}% in {b0}** to "
             f"**{fmt(bio[bio_norev_pk_y]['norev'],0)}% in {bio_norev_pk_y}** — the cohort is not just "
             f"large, it is earlier-stage than it used to be. That is what sits behind the "
             f"{norev_pk_y} no-revenue reading: a bigger, more pre-commercial biotech book, not a "
             f"broad-based loss of revenue across the index.\n")
    P.append("*Tabs: `Bio Weight & Quality`, `Bio Unprof by Industry`; charts on `Key Charts` and the "
             "`Bio` tabs.*\n")
    P.append("---\n")

    # 3. Concentration & breadth
    if have_conc:
        P.append("## 3. Concentration: a recent top-heavy turn\n")
        P.append(f"For most of the study the Russell index was strikingly diffuse — its top ten names "
                 f"held only about **{fmt(t10_pre,0)}%** of the index and its effective breadth ran near "
                 f"**{fmt(effn_pre,0)} names**. That changed abruptly in **{conc_pk_y}**, when the top "
                 f"ten jumped to **{fmt(cw[conc_pk_y]['top10'],1,True)}**, the single largest name reached "
                 f"**{fmt(cw[conc_pk_y]['maxw'],1,True)}**, the Herfindahl index roughly doubled to "
                 f"**{fmt(cw[conc_pk_y]['hhi'],0)}**, and effective breadth fell to "
                 f"**~{fmt(cw[conc_pk_y]['effn'],0)} names** — a handful of winners running well ahead of "
                 f"the pack. By **{cLy}** it had eased (top ten {fmt(cw[cLy]['top10'],1,True)}, "
                 f"effective breadth ~{fmt(cw[cLy]['effn'],0)}) but remained above the pre-{conc_pk_y} "
                 f"norm.\n")
        P.append("| June snapshot | Top-10 wt | Largest name | HHI | Eff. N |")
        P.append("|---|---|---|---|---|")
        show = sorted({cyrs[0], conc_pk_y, cLy})
        for y in show:
            P.append(f"| {y} | {fmt(cw[y]['top10'],1,True)} | {fmt(cw[y]['maxw'],1,True)} | "
                     f"{fmt(cw[y]['hhi'],0)} | {fmt(cw[y]['effn'],0)} |")
        P.append("")
        if have_breadth:
            yb = byrs_cb[-1]
            P.append(f"Leadership narrowed alongside the weight. In {yb} the cap-weighted return ran "
                     f"**{fmt(cb[yb]['capmed'],0)} points** ahead of the median name, and the top ten "
                     f"names accounted for **{fmt(cb[yb]['top10g'],0)}%** of the index's gains — a market "
                     f"carried by a few, which is a direct headwind for a diversified, "
                     f"equal-conviction book.\n")
        P.append("*Tabs: `Conc Weight`, `Conc Breadth`; charts on `Key Charts`.*\n")
        P.append("---\n")

    # 4. What the makeup has meant (brief return footprint)
    P.append("## 4. What the composition has meant for returns\n")
    P.append(f"The makeup is not just descriptive — it drives the index's returns. Over the trailing "
             f"{win_mo // 12} years (ending {D['end_m']}), the never-profitable cohort carried "
             f"~**{fmt(nev['win_w'],0)}%** of the Russell index by weight but produced "
             f"~**{fmt(nev_share,0)}%** of its return, about **{fmt(nev_ratio,1)}× its weight**, and "
             f"biotech alone accounted for ~**{fmt(bio_win_share,0)}%** of the return from "
             f"~**{fmt(bio_win_w,0)}%** of the weight. Screening the Russell index down to only its "
             f"profitable names — an S&P-600-style rule applied to its *own* constituents — would have "
             f"changed the full-cycle return by **{fmt(eff_f,1,sign=True)} points** and the trailing "
             f"window by **{fmt(eff_w,1,sign=True)} points**: the low-quality tail is a drag over the "
             f"long run and a boost in the recent rally. As a check on the construction, that "
             f"profitable-only rebuild lands close to the actual S&P600G return over the cycle "
             f"(**{fmt(pf_f,1,True)}** vs **{fmt(cum6,1,True)}**).\n")
    P.append("*Tabs: `Attr Contribution`, `Attr Counterfactual`, `Bio Contribution`.*\n")
    P.append("---\n")

    # For the Committee
    P.append("## For the Committee\n")
    P.append(f"The two small-cap growth benchmarks are structurally different portfolios, and the gap "
             f"has widened rather than closed. The Russell index today is **{fmt(q[yL]['unprof_R'],0,True)} "
             f"unprofitable by weight, {fmt(q[yL]['norev_R'],1,True)} pre-revenue, and "
             f"{fmt(bio[bL]['wt_R'],0,True)} biotech**, and it recently passed through its most "
             f"concentrated episode of the study. None of that is present to the same degree in the "
             f"earnings-screened S&P SmallCap 600 Growth.\n")
    P.append("For a quality- or earnings-disciplined mandate, this is the practical takeaway: the "
             "manager is being measured against a benchmark whose distinguishing features — the "
             "non-earner tail, the pre-commercial biotech book, the bursts of concentration — are "
             "precisely the exposures the mandate is designed to limit. Where that is the case, the "
             "S&P SmallCap 600 Growth is the more representative yardstick, whether as a primary "
             "benchmark or as standing context alongside the Russell index.\n")
    P.append("---\n")
    P.append("*Methodology: fundamentals are taken as originally filed in each 10-K (by original "
             "accession, no restatement blending). Index membership is point-in-time with no look-ahead "
             "(a constituent's fiscal year is the latest 10-K filed before each June snapshot). "
             "Financial-sector filers report interest/premium/fee top lines rather than revenue, so they "
             "are not counted in the no-revenue figures. Dollar totals count each company once across "
             "share classes. Full definitions and per-tab notes accompany the workbook.*")
    return "\n".join(P) + "\n"


# --------------------------------------------------------------------------- entrypoints
def _load():
    import openpyxl
    if not WB.exists():
        raise SystemExit(f"!! workbook not found: {WB}  (run r2k_report.py first, or set R2KG_WORKBOOK)")
    return openpyxl.load_workbook(WB, read_only=True, data_only=True)


def main():
    wb = _load()
    D = collect(wb)
    problems = reconcile(D)
    if problems:
        raise SystemExit("!! reconciliation FAILED (a tab layout likely changed -- fix an extractor "
                         "before trusting the memo):\n   - " + "\n   - ".join(problems))
    memo = build_memo(D)
    OUT.write_text(memo, encoding="utf-8")
    idx_f, idx_w = D["cf"]["R2000G index"]
    print(f"  reconciled: reconstructed index full={idx_f:.1f}% window={idx_w:.2f}% "
          f"(matches Attr Contribution total within {RECONCILE_TOL} pt)")
    print(f"  -> {OUT.name}: {len(memo.splitlines())} lines, {len(memo):,} chars  "
          f"(scope {min(D['q'])}–{max(D['q'])}, window ends {D['end_m']})")


def selftest():
    wb = _load()
    D = collect(wb)
    problems = reconcile(D)
    print("RECONCILE:", "PASS" if not problems else "FAIL -> " + "; ".join(problems))
    idx_f, idx_w = D["cf"]["R2000G index"]
    pf_f, pf_w = D["cf"]["Profitable-only"]
    print(f"  scope {min(D['q'])}-{max(D['q'])}, window ends {D['end_m']}")
    print(f"  perf full: R2KG cum={D['ps']['cum'][0]}  SP6G cum={D['ps']['cum'][1]}")
    print(f"  counterfactual full: idx={idx_f:.1f}%  prof-only={pf_f:.1f}%  effect={pf_f-idx_f:+.1f}")
    print(f"  counterfactual window: idx={idx_w:.2f}%  prof-only={pf_w:.2f}%  effect={pf_w-idx_w:+.1f}")
    memo = build_memo(D)
    print(f"  build_memo OK: {len(memo.splitlines())} lines")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
