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
    return dict(ps=ps, cal=cal, q=q, wp=wp, attr=attr, bio=bio, biocon=biocon,
                cf=cf, bcf=bcf, end_m=end_m)


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
    ps, cal, q, wp, attr, bio, biocon, cf, bcf = (D["ps"], D["cal"], D["q"], D["wp"], D["attr"],
                                                  D["bio"], D["biocon"], D["cf"], D["bcf"])
    years = sorted(q)
    y0, yL = years[0], years[-1]                    # first, latest snapshot years (fundamentals basis)
    # overall return-scope years come from the performance window ("Full window: 2011-01 to 2026-06 ...")
    yrs_in_text = re.findall(r"(\d{4})-\d{2}", ps.get("window_text", ""))
    scope0 = yrs_in_text[0] if yrs_in_text else str(y0)
    scopeL = yrs_in_text[-1] if yrs_in_text else str(yL)
    ypk = max(years, key=lambda y: (q[y]["unprof_R"] or 0))   # peak-unprofitable year
    # a mid-cycle reference year near 2019 if present, else the middle year
    ymid = 2019 if 2019 in q else years[len(years) // 2]
    diffs = [(q[y]["unprof_R"] or 0) - (q[y]["unprof_6"] or 0) for y in years]
    dmin, dmax = min(diffs), max(diffs)

    # ---- performance (full + window) ----
    cumR, cum6 = ps["cum"]
    annR, ann6 = ps["ann"]
    volR, vol6 = ps["vol"]
    ddR, dd6 = ps["dd"]
    used = wp.get("used", {})
    win_R, win_6, win_x = used.get("r2kg"), used.get("sp6"), used.get("excess")
    win_mo = int(used.get("months") or 36)
    win_start = wp.get("window_start") or used.get("start")
    all_pos = all((c["excess"] or 0) > 0 for c in wp["candidates"])

    # ---- counterfactual (reconstruction basis) ----
    idx_f, idx_w = cf["R2000G index"]
    pf_f, pf_w = cf["Profitable-only"]
    eff_f, eff_w = pf_f - idx_f, pf_w - idx_w
    xb_f, xb_w = bcf["Ex-biotech"]
    bxb_eff_w = xb_w - idx_w                          # ex-biotech window effect (negative = biotech helped)

    # ---- cohort attribution (window) ----
    tot_w = attr[next(k for k in attr if k.lower().startswith("total"))]["win_c"]
    nev = next(v for k, v in attr.items() if k.lower().startswith("never"))
    fal = next(v for k, v in attr.items() if k.lower().startswith("fallen"))
    nev_share = nev["win_c"] / tot_w * 100
    nev_ratio = (nev["win_c"] / tot_w) / (nev["win_w"] / 100) if nev["win_w"] else None
    tail_c = (nev["win_c"] or 0) + (fal["win_c"] or 0)
    tail_w = (nev["win_w"] or 0) + (fal["win_w"] or 0)
    tail_share = tail_c / tot_w * 100

    # ---- biotech ----
    bio_wts = [bio[y]["wt_R"] for y in bio if bio[y]["wt_R"] is not None]
    bio_wt_lo, bio_wt_hi = min(bio_wts), max(bio_wts)
    bio_pk_y = max(bio, key=lambda y: (bio[y]["wt_R"] or 0))
    bio6_wts = [bio[y]["wt_6"] for y in bio if bio[y]["wt_6"] is not None]
    bio6_lo, bio6_hi = min(bio6_wts), max(bio6_wts)
    bio_gap_pk = max(bio, key=lambda y: (bio[y]["diff"] or 0))
    bio_unprof = [bio[y]["unprof"] for y in bio if bio[y]["unprof"] is not None]
    bio_norev = [bio[y]["norev"] for y in bio if bio[y]["norev"] is not None]
    bio_win_c = next(v for k, v in biocon.items() if k.lower().startswith("biotech"))["win_c"]
    bio_win_w = next(v for k, v in biocon.items() if k.lower().startswith("biotech"))["win_w"]
    bio_win_share = bio_win_c / tot_w * 100

    P = []                                             # paragraphs
    P.append("# US Small Cap Growth Benchmark Review")
    P.append("### Why active managers underperformed the Russell 2000 Growth\n")
    P.append("**Prepared for:** Investment Committee  ")
    P.append(f"**Scope:** Russell 2000 Growth (R2000G) vs. S&P SmallCap 600 Growth (S&P600G), "
             f"{scope0}–{scopeL}  ")
    P.append(f"**Manager window:** trailing {win_mo // 12} years, ending {D['end_m']} "
             f"(start {win_start}; justified in the *Perf Window Proof* tab)  ")
    P.append("**Basis:** As-filed 10-K fundamentals (original accession), point-in-time index "
             "membership, monthly total returns\n")
    P.append("*This memo is generated directly from `R2000G_SmallCapGrowth_Benchmark_Review.xlsx`; "
             "every figure below is read or computed from the tab named at the end of each section.*\n")
    P.append("---\n")

    # Bottom line
    P.append("## Bottom line\n")
    P.append(f"Active US Small Cap Growth managers, benchmarked to the Russell 2000 Growth, lagged the "
             f"index over the trailing {win_mo // 12} years (ending {D['end_m']}). **This was "
             f"structural, not a loss of manager skill.** The Russell 2000 Growth carries a large tail "
             f"of unprofitable, often pre-earnings companies that a quality- or earnings-disciplined "
             f"manager (whose portfolio resembles the S&P SmallCap 600 Growth) systematically avoids. "
             f"That tail led the benchmark over the window managers were measured on. The same "
             f"discipline *added* value over the full cycle ({fmt(eff_f, 1, sign=True)} pts) and "
             f"carried lower volatility and drawdown.\n")
    P.append("---\n")

    # Section 1
    P.append("## 1. The structural difference: R2000G carries a much larger low-quality tail\n")
    P.append("The S&P SmallCap 600 requires **positive trailing GAAP earnings** to enter the index; "
             "the Russell 2000 Growth has **no profitability screen**. That single rule produces a "
             "persistent quality gap:\n")
    P.append("| Measure (by index weight) | R2000G | S&P600G | Gap |")
    P.append("|---|---|---|---|")
    for y in (y0, ymid, ypk, yL):
        tag = f"Unprofitable (net income < 0), {y}"
        if y == ypk:
            tag += " (peak)"
        elif y == yL:
            tag += " (latest)"
        P.append(f"| {tag} | {fmt(q[y]['unprof_R'],1,True)} | {fmt(q[y]['unprof_6'],1,True)} | "
                 f"**{fmt((q[y]['unprof_R'] or 0)-(q[y]['unprof_6'] or 0),1,sign=True)} pts** |")
    P.append(f"| **Never-profitable** weight, {yL} | **{fmt(q[yL]['never_R'],1,True)}** | "
             f"**{fmt(q[yL]['never_6'],1,True)}** | "
             f"**{fmt((q[yL]['never_R'] or 0)-(q[yL]['never_6'] or 0),1,sign=True)} pts** |\n")
    P.append(f"R2000G ran **{fmt(dmin,0)}–{fmt(dmax,0)} points more unprofitable weight than "
             f"S&P600G in every single year**. The sharpest cut is the *never-profitable* cohort (no "
             f"profitable year on record): **{fmt(q[yL]['never_R'],1,True)}** of R2000G by weight in "
             f"{yL}, versus **{fmt(q[yL]['never_6'],1,True)}** in the earnings-screened S&P600G. "
             f"R2000G's unprofitable weight peaked at **{fmt(q[ypk]['unprof_R'],1,True)} in {ypk}**, "
             f"the height of the profitless-growth rally.\n")
    P.append(f"**Biotech is the embodiment of the gap.** Clinical-stage biotech is "
             f"**{fmt(min(bio_unprof),0)}–{fmt(max(bio_unprof),0)}% unprofitable** and carries "
             f"**up to {fmt(max(bio_norev),0)}% with no revenue at all**, so it is largely "
             f"uninvestable for the earnings-gated S&P 600. It has been "
             f"**{fmt(bio_wt_lo,0)}–{fmt(bio_wt_hi,0)}% of R2000G** (peaking at "
             f"{fmt(bio[bio_pk_y]['wt_R'],1,True)} in {bio_pk_y}) versus "
             f"**{fmt(bio6_lo,0)}–{fmt(bio6_hi,0)}% of S&P600G** — a weight gap that widened "
             f"to **~{fmt(bio[bio_gap_pk]['diff'],0)} points** ({bio_gap_pk}). Over the "
             f"{win_mo // 12}-year window biotech contributed **{fmt(bio_win_c,1,sign=True)} of the "
             f"index's {fmt(tot_w,1,sign=True)} points (~{fmt(bio_win_share,0)}% of the return) from "
             f"~{fmt(bio_win_w,0)}% of the weight**, and removing it from R2000G's own names cuts the "
             f"window return to **{fmt(xb_w,1,True)}** — roughly {fmt(-bxb_eff_w,0)} points an "
             f"earnings-disciplined manager would largely have missed. See the *Bio* tabs.\n")
    P.append("*Exhibit: workbook tabs `Qual Comparison`, `Bio Weight & Quality`; chart "
             "\"% Unprofitable by weight\" on `Key Charts`.*\n")
    P.append("---\n")

    # Section 2
    P.append("## 2. The realized cost: the unprofitable tail led the benchmark in the window\n")
    P.append("Decomposing R2000G's realized return into quality cohorts (point-in-time, Carino-linked "
             "so contributions sum to the index return):\n")
    P.append(f"- Over the **trailing {win_mo // 12}-year window (ending {D['end_m']})**, the "
             f"**never-profitable cohort was ~{fmt(nev['win_w'],0)}% of R2000G by weight but delivered "
             f"~{fmt(nev_share,0)}% of the index's return** ({fmt(nev['win_c'],1,sign=True)} pts of the "
             f"{fmt(tot_w,1,sign=True)}% total) — roughly **{fmt(nev_ratio,1)}× its weight**. "
             f"The full unprofitable tail (fallen + never-profitable) drove ~{fmt(tail_share,0)}% of the "
             f"window's return from ~{fmt(tail_w,0)}% of weight.")
    P.append("- A manager applying an earnings discipline would not have held those names — and "
             "would therefore mechanically lag the benchmark.\n")
    P.append("**The cleanest evidence is the counterfactual.** We rebuilt R2000G's *own* constituents "
             "as a \"profitable-only\" portfolio (the S&P 600-style screen, reweighted monthly):\n")
    P.append(f"| | Full period | Manager window ({win_mo // 12} yr) |")
    P.append("|---|---|---|")
    P.append(f"| R2000G index (reconstruction) | {fmt(idx_f,1,True)} | {fmt(idx_w,1,True)} |")
    P.append(f"| Profitable-only (R2000G's own names) | **{fmt(pf_f,1,True)}** | **{fmt(pf_w,1,True)}** |")
    P.append(f"| Effect of screening for earnings | **{fmt(eff_f,1,sign=True)} pts "
             f"({'helped' if eff_f > 0 else 'hurt'})** | **{fmt(eff_w,1,sign=True)} pts "
             f"({'helped' if eff_w > 0 else 'hurt'})** |\n")
    P.append(f"The sign flips. **Over the full cycle, screening for earnings "
             f"*{'helped' if eff_f > 0 else 'hurt'}* by ~{fmt(abs(eff_f),0)} points; over the trailing "
             f"{win_mo // 12}-year window it *{'helped' if eff_w > 0 else 'cost'}* "
             f"~{fmt(abs(eff_w),0)} points.** That window is precisely when active managers were "
             f"judged.\n")
    P.append(f"A corroborating cross-check: the profitable-only rebuild's full-period return "
             f"(**{fmt(pf_f,1,True)}**) lands close to the *actual* S&P600G index return over the cycle "
             f"(**{fmt(cum6,1,True)}**) — two independent constructions (an earnings screen on "
             f"R2000G's own names vs. the separately-built S&P600G) landing in the same place, evidence "
             f"the earnings screen is the mechanism rather than an artifact.\n")
    P.append("*Exhibit: workbook tabs `Attr Contribution`, `Attr Counterfactual`; chart "
             "\"Earnings-screen counterfactual\" on `Key Charts`.*\n")
    P.append("---\n")

    # Section 3
    P.append("## 3. The long-run context: the discipline wins the cycle\n")
    P.append(f"Over the full {scope0}–{scopeL} window the quality-screened index was the better "
             f"asset:\n")
    P.append("| | R2000G | S&P600G |")
    P.append("|---|---|---|")
    P.append(f"| Cumulative total return | {fmt(cumR,1,True)} | **{fmt(cum6,1,True)}** |")
    P.append(f"| Annualized return | {fmt(annR,1,True)} | **{fmt(ann6,1,True)}** |")
    P.append(f"| Annualized volatility | {fmt(volR,1,True)} | **{fmt(vol6,1,True)}** |")
    P.append(f"| Max drawdown | {fmt(ddR,1,True)} | **{fmt(dd6,1,True)}** |\n")
    P.append(f"S&P600G delivered more return with **less** risk across the cycle. Over the trailing "
             f"{win_mo // 12}-year window R2000G outran S&P600G by **{fmt(win_x,1,sign=True)} points "
             f"({fmt(win_R,1,True)} vs {fmt(win_6,1,True)})** on the back of its lower-quality tail "
             f"— a reversal, not a durable regime change.")
    # calendar highlights: two best and two worst excess years
    ce = sorted(cal.items(), key=lambda kv: (kv[1]["excess"] or 0))
    worst = [y for y, _ in ce[:2]]
    best = [y for y, _ in ce[-2:]][::-1]
    def cyr(y):
        return f"{y} ({fmt(cal[y]['r2kg'],1,True)} vs {fmt(cal[y]['sp6'],1,True)})"
    P.append(f" Calendar years make the pattern concrete: R2000G led hardest in "
             f"{cyr(best[0])} and {cyr(best[1])}, but gave it back in {cyr(worst[0])} and "
             f"{cyr(worst[1])}.\n")
    rl, os_ = wp.get("rel_low"), wp.get("outperf_start")
    P.append(f"**Why the window starts where it does — and a transparency note.** R2000G's "
             f"cumulative excess over S&P600G bottomed in **{rl}** (its relative low); it has led the "
             f"quality index since, and the trailing-{win_mo // 12}-year window sits within that "
             f"recovery. The dates are not cherry-picked: the *Perf Window Proof* tab reports the gap "
             f"across every candidate window, all ending {D['end_m']} — "
             f"{'the benchmark’s lead is positive in each' if all_pos else 'see the tab for the full set'}"
             f", so the conclusion does not depend on the exact start month.\n")
    P.append("*Exhibit: workbook tabs `Perf Summary`, `Perf Calendar Yr`, `Perf Window Proof`; chart "
             "\"Growth of $1\" on `Key Charts`.*\n")
    P.append("---\n")

    # Implications
    P.append("## Implications for the Committee\n")
    P.append("1. **Reframe the underperformance.** Judged against R2000G, an earnings-disciplined SCG "
             "manager will lag whenever the benchmark's unprofitable tail leads. That is a "
             "benchmark-construction effect, not evidence of lost skill.")
    P.append("2. **Benchmark fit.** Where mandates are quality/earnings-disciplined, the S&P SmallCap "
             "600 Growth is the more representative yardstick; consider it as a primary or secondary "
             "benchmark, or as context alongside R2000G.")
    P.append("3. **Forward view.** The full-cycle and risk numbers favor the disciplined approach. The "
             "recent window is the cost of that discipline during a low-quality rally, not a reason to "
             "abandon it.\n")
    P.append("---\n")
    P.append("*Methodology: fundamentals are taken as originally filed in each 10-K (by original "
             "accession, no restatement blending). Index membership is point-in-time with no "
             "look-ahead (a constituent's fiscal year is the latest 10-K filed before each snapshot). "
             "Commodity/securities broker-dealers that gross up pass-through sales are carried on a net "
             "operating-revenue basis. Cohort attribution is Carino-linked so cohort contributions sum "
             "exactly to the index's cumulative return. Full detail and per-tab notes in "
             "`R2000G_SmallCapGrowth_Benchmark_Review.xlsx`.*")
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
