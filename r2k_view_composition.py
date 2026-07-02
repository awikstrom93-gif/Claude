"""
r2k_view_composition.py  --  "Composition Change" sheet as a pure projection of the canonical panel.
Reproduces step3's decomposition of the year-over-year change in weighted op-margin and
%unprofitable into within-name / reweight / entrants / leavers effects.  Two rows per period.

RUN:  python r2k_view_composition.py
"""
from r2k_universe import get_panel, diff_sheet, print_sheet
from r2k_step3_analytics import _p, WINSOR

SHEET = "Composition Change"
TITLE_TEXT = "What drove the change: within-name vs turnover vs reweighting"
HDR = ["Period", "Metric", "Total change", "Within-name", "Reweight", "Entrants", "Leavers"]
METRICS = [("op_margin", False, "Op margin (wt)"), ("prof_ni", True, "% unprofitable (wt)")]


def _snap_cov(panel):
    """{year: {nt: (metrics_row, weight)}} over covered constituents (keyed by ticker, like step3)."""
    years = sorted({int(r["year"]) for r in panel})
    by = {y: {} for y in years}
    for r in panel:
        if r["covered"]:
            by[int(r["year"])][r["nt"]] = (r, r["weight"])
    return years, by


def _val(m, key, flag):
    """The per-name contribution. For the flag metric it's 1.0 when UNprofitable (so the wavg is the
    %-unprofitable share); for op-margin it's the ratio WINSORIZED to the same clamp the headline
    OpMgn wavg uses on Index Quality Trends -- without matching the winsor, one tiny-revenue outlier
    (op-margin of -50x) blows the decomposition total to nonsense (the -773.5% the eye-test flagged)."""
    v = m[key]
    if flag:
        return 0.0 if v else 1.0            # %unprofitable = 1 - prof
    lo, hi = WINSOR
    return max(lo, min(hi, v))


def _wavg_present(cov, key, flag):
    """The actual metric the decomposition must tie to: the weighted average over names where the
    metric is PRESENT, normalized by PRESENT weight (not total covered weight). This is exactly what
    Index Quality Trends reports (aggregate(..., winsor=True) drops None and divides by present weight)."""
    present = [(m, w) for m, w in cov.values() if m[key] is not None]
    tw = sum(w for _, w in present) or 1.0
    return sum(_val(m, key, flag) * w for m, w in present) / tw


def _decompose(pcov, ccov, key, flag):
    """Shift-share decomposition of the change in the present-weight wavg into within / reweight /
    entrants / leavers. The population is the metric-PRESENT set each year (a continuing name whose
    metric was blank last year but present this year is an 'entrant' for the metric); with present-
    weight normalization the four effects sum EXACTLY to _wavg_present(cy) - _wavg_present(py). The
    old version normalized by TOTAL covered weight while summing only present names, so the base never
    matched the metric and op-margin came out with the wrong magnitude and sign."""
    P = {t: (m, w) for t, (m, w) in pcov.items() if m[key] is not None}
    C = {t: (m, w) for t, (m, w) in ccov.items() if m[key] is not None}
    pw = sum(w for _, w in P.values()) or 1.0
    cw = sum(w for _, w in C.values()) or 1.0
    common = set(P) & set(C)
    within = reweight = entr = leav = 0.0
    for t in common:
        mp, wp = P[t]; mc, wc_ = C[t]
        within += (wp / pw) * (_val(mc, key, flag) - _val(mp, key, flag))
        reweight += (wc_ / cw - wp / pw) * _val(mc, key, flag)
    for t in set(C) - common:
        mc, wc_ = C[t]
        entr += (wc_ / cw) * _val(mc, key, flag)
    for t in set(P) - common:
        mp, wp = P[t]
        leav -= (wp / pw) * _val(mp, key, flag)
    return within, reweight, entr, leav


def composition_rows(panel):
    years, by = _snap_cov(panel)
    out = []
    for i in range(1, len(years)):
        py, cy = years[i - 1], years[i]
        for key, flag, label in METRICS:
            within, reweight, entr, leav = _decompose(by[py], by[cy], key, flag)
            tot = within + reweight + entr + leav
            out.append([f"{py}->{cy}", label, _p(tot), _p(within), _p(reweight), _p(entr), _p(leav)])
    return out


def _selftest():
    """For BOTH metrics the four effects must sum to the actual change in the present-weight wavg,
    including a continuing name whose metric is blank one year (BBB) and a genuine entrant (CCC)."""
    panel = [
        {"year": 2020, "covered": 1, "nt": "AAA", "weight": 2.0, "op_margin": 0.10, "prof_ni": True},
        {"year": 2020, "covered": 1, "nt": "BBB", "weight": 1.0, "op_margin": None, "prof_ni": False},   # op-margin blank in 2020
        {"year": 2021, "covered": 1, "nt": "AAA", "weight": 3.0, "op_margin": 0.20, "prof_ni": True},
        {"year": 2021, "covered": 1, "nt": "BBB", "weight": 1.0, "op_margin": 0.08, "prof_ni": False},   # now present -> metric entrant
        {"year": 2021, "covered": 1, "nt": "CCC", "weight": 1.0, "op_margin": 0.05, "prof_ni": False},   # index entrant
    ]
    years, by = _snap_cov(panel)
    allok = True
    for key, flag, label in METRICS:
        w, rw, en, lv = _decompose(by[2020], by[2021], key, flag)
        tot = w + rw + en + lv
        actual = _wavg_present(by[2021], key, flag) - _wavg_present(by[2020], key, flag)
        ok = abs(tot - actual) < 1e-9
        allok &= ok
        print(f"  SELFTEST Composition [{label}] effects sum to actual change "
              f"(sum={tot:.6f}, actual={actual:.6f}): {'PASS' if ok else 'FAIL'}")
    assert allok


SUBHDR = ["Period", "Total change", "Within-name", "Reweight", "Entrants", "Leavers"]
BLOCK_ORDER = ["% unprofitable (wt)", "Op margin (wt)"]   # thesis metric on the left


def write_sheet(wb, panel):
    """Two metric blocks side by side (cols 1-6 and 8-13), each a self-contained decomposition table
    with the same header row -- so each is chartable as a stacked column and the layout is robust to
    adding years (both blocks just grow downward)."""
    from openpyxl.styles import Font
    ws = wb.create_sheet(SHEET)
    ws.cell(row=1, column=1, value=TITLE_TEXT).font = Font(bold=True, size=12)
    ws.cell(row=2, column=1, value="Each column decomposes the year-over-year change into: within-name "
            "(same names' fundamentals), reweight (existing names' weight shifts), entrants (new index "
            "members), leavers (removed members). The four sum to the total change.").font = Font(size=9, italic=True, color="555555")
    groups = {}
    for r in composition_rows(panel):
        groups.setdefault(r[1], []).append(r)              # r[1] = metric label
    for gi, metric in enumerate(BLOCK_ORDER):
        c0 = 1 + gi * 7                                     # block 1: cols 1-6; block 2: cols 8-13
        ws.cell(row=3, column=c0, value=metric).font = Font(bold=True)
        for j, h in enumerate(SUBHDR):
            ws.cell(row=4, column=c0 + j, value=h).font = Font(bold=True)
        for i, r in enumerate(groups.get(metric, []), start=5):
            for j, v in enumerate([r[0], r[2], r[3], r[4], r[5], r[6]]):   # Period, Total, Within, Reweight, Entrants, Leavers
                ws.cell(row=i, column=c0 + j, value=v)
    return ws


def main():
    import sys
    if "--selftest" in sys.argv:
        _selftest(); return
    _selftest()
    panel = get_panel()
    rows = composition_rows(panel)
    print(f"\n  {SHEET} (panel-derived):")
    print_sheet(HDR, rows, ncols=7)
    diff_sheet(SHEET, HDR, rows, keycols=2)


if __name__ == "__main__":
    main()
