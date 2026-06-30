"""
============================================================
r2k_step9_biotech.py  --  biotech deep-dive.
============================================================
Biotech (clinical-stage, typically pre-revenue, rarely profitable) is the clearest
embodiment of the R2000G vs S&P 600 Growth quality gap: it is a large, volatile slice
of R2000G's unprofitable tail and is almost entirely screened out of the earnings-
gated S&P 600 Growth. This module quantifies its weight, quality, and return impact.

INPUTS  (R2KG_BASE)
    *Russell*Growth*Holdings*.xlsx   *600*Growth*Holdings*.xlsx   (Morningstar Industry)
    *Performance*.xlsx               edgar_annual_fundamentals_ASFILED.csv
    security_cik_map.json / temporal_cik_map.json

OUTPUT
    R2000G_Biotech.xlsx
      Biotech Weight & Quality, Biotech in the Tail, Biotech Contribution,
      Biotech Counterfactual, Charts, Notes

Biotech is flagged from the holdings' Morningstar Industry (contains 'biotech').
Weight/return tabs need no fundamentals; quality/tail tabs join fundamentals by
CIK then ticker (R2000G holdings carry no CIK column).
============================================================
"""
from pathlib import Path
from datetime import date
import os, math

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import LineChart, Reference

from r2k_perf_io import load_performance, load_monthly_holdings, BASE, ntk
from r2k_step3_analytics import load_fundamentals, pick_fy0, company_metrics, load_maps
from r2k_universe import norm_facts, fund_for, ticker_cik_map   # consolidated: one definition

OUT = BASE / "R2000G_Biotech.xlsx"
TARGET_MONTH = int(os.environ.get("SNAP_MONTH", "4"))
WINDOW_MONTHS = int(os.environ.get("WINDOW_MONTHS", "36"))
WINDOW_START = os.environ.get("WINDOW_START")
# Biotech definition (Morningstar Industry keywords). Default = narrow 'Biotechnology' only.
# Broaden to the clinical life-sciences tail with e.g.
#   set BIOTECH_KEYWORDS=biotech,drug manufactur,diagnostics
BIO_KEYWORDS = [k.strip().lower() for k in os.environ.get("BIOTECH_KEYWORDS", "biotech").split(",") if k.strip()]
# broad life-sciences cut (companion to strict biotech) + theme map for the tail-composition exhibit
LIFESCI_KEYWORDS = ["biotech", "drug manufactur", "pharmaceutical", "diagnostics", "medical device",
                    "medical instrument", "medical care", "health information", "life science"]
THEMES = [  # (label, [industry keywords]); first match wins, order matters
    ("Biotech", ["biotech"]),
    ("Other healthcare/life-sci", ["drug manufactur", "pharmaceutical", "diagnostics", "medical",
                                   "health", "life science"]),
    ("Software & internet", ["software", "internet", "information technology services"]),
    ("Hardware, semis & electrical", ["semiconductor", "computer hardware", "electronic",
                                      "communication equipment", "electrical", "scientific & technical", "solar"]),
    ("Energy & materials", ["oil", "gas", "coal", "uranium", "mining", "metal", "chemical", "materials"]),
    ("Industrials", ["aerospace", "defense", "machinery", "industrial", "engineering", "construction",
                     "transportation", "trucking", "railroad", "airline", "building products"]),
    ("Consumer", ["restaurant", "retail", "apparel", "leisure", "resort", "casino", "gambling", "travel",
                  "lodging", "auto", "furnishing", "packaging", "beverage", "food", "consumer", "education"]),
    ("Financials & real estate", ["bank", "capital markets", "insurance", "asset management", "credit",
                                  "financial", "mortgage", "reit", "real estate"]),
]

HDR = PatternFill("solid", fgColor="1F4E5F"); HF = Font(bold=True, color="FFFFFF", size=10)
TITLE = Font(bold=True, size=12)


def find(pats):
    for p in pats:
        c = list(BASE.glob(p))
        if c: return c[0]
    return None


def _hdr(ws, row, hs):
    for c, h in enumerate(hs, 1):
        x = ws.cell(row=row, column=c, value=h); x.fill = HDR; x.font = HF
        x.alignment = Alignment(horizontal="center", wrap_text=True)


def is_bio(h):
    il = (h.get("ms_industry") or "").lower()
    return any(k in il for k in BIO_KEYWORDS)

def is_lifesci(h):
    il = (h.get("ms_industry") or "").lower()
    return any(k in il for k in LIFESCI_KEYWORDS)

def theme_of(ind):
    il = (ind or "").lower()
    for label, keys in THEMES:
        if any(k in il for k in keys): return label
    return "Other"


# norm_facts / ticker_cik_map / fund_for imported from r2k_universe (single source of truth).
def annual_spine(holdings):
    out = {}
    for d in sorted(holdings):
        cur = out.get(d.year)
        if cur is None or abs(d.month - TARGET_MONTH) < abs(cur.month - TARGET_MONTH):
            out[d.year] = d
    return out


def nearest_prior(sorted_dates, target):
    prev = None
    for d in sorted_dates:
        if d < target: prev = d
        else: break
    return prev


def compound(rets):
    g = 1.0
    for r in rets:
        if r is not None: g *= (1 + r)
    return g - 1.0


def carino_link(slice_rows, key):
    """Carino-linked contribution of `key` (a per-row contribution dict field name)."""
    if not slice_rows: return None, 0.0
    acum = 1.0
    for r in slice_rows: acum *= (1 + r["actual"])
    acum -= 1.0
    K = math.log(1 + acum) / acum if abs(acum) > 1e-12 else 1.0
    out = 0.0
    for r in slice_rows:
        k = math.log(1 + r["actual"]) / r["actual"] if abs(r["actual"]) > 1e-12 else 1.0
        out += (k / K) * r[key]
    return acum, out


def _p(v, nd=1): return round(100 * v, nd) if v is not None else None


def build():
    series, idx, pdates = load_performance()
    if "R2KG" not in idx:
        raise SystemExit("!! R2000G index row not found in performance file")
    R = idx["R2KG"]
    hr = find(["*[Rr]ussell*[Gg]rowth*[Hh]olding*.xlsx"])
    hs = find(["*[Ss][Pp]*600*[Gg]rowth*[Hh]olding*.xlsx", "*600*[Gg]rowth*[Hh]olding*.xlsx"])
    if not hr: raise SystemExit("!! R2000G holdings not found")
    hold_r = load_monthly_holdings(hr, verbose=False)
    hold_s = load_monthly_holdings(hs, verbose=False) if hs else None
    facts = norm_facts(load_fundamentals())
    base, temporal = load_maps(); tmap = ticker_cik_map(base, temporal)
    def hcik(h): return h["cik"] or tmap.get(h["nt"])

    by_cik, by_nt = {}, {}
    for rec in series:
        m = rec["meta"]
        if m["cik"]: by_cik[m["cik"]] = rec
        if m["nt"]: by_nt.setdefault(m["nt"], rec)
    def ret_for(h, d):
        rec = by_cik.get(hcik(h)) or by_nt.get(h["nt"])
        return rec["ret"].get(d) if rec else None

    spine_r = annual_spine(hold_r); spine_s = annual_spine(hold_s) if hold_s else {}
    years = sorted(spine_r)

    wb = openpyxl.Workbook(); wb.remove(wb.active)

    # ---- Biotech Weight & Quality ----
    wq = wb.create_sheet("Biotech Weight & Quality")
    wq.cell(1, 1, "Biotech weight & quality -- R2000G vs S&P 600 Growth").font = TITLE
    _hdr(wq, 3, ["Year", "R2KG biotech wt%", "R2KG biotech #", "Biotech %unprofitable (NI)",
                 "Biotech %no-revenue", "600G biotech wt%", "600G biotech #", "Wt diff (R2KG-600G)"])
    r = 4
    for y in years:
        snap = hold_r[spine_r[y]]; tw = sum(h["weight"] for h in snap) or 1e-9
        bio = [h for h in snap if is_bio(h)]
        bw = sum(h["weight"] for h in bio) / tw * 100
        # quality within biotech (needs fundamentals)
        snap_dt = spine_r[y]; un_w = nr_w = cov_w = 0.0
        for h in bio:
            cf = fund_for(facts, hcik(h))
            if not cf: continue
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is None: continue
            cm = company_metrics(cf, fy0); cov_w += h["weight"]
            if cm["prof_ni"] is False: un_w += h["weight"]
            if not cm["has_rev"]: nr_w += h["weight"]
        un = 100 * un_w / cov_w if cov_w else None
        nr = 100 * nr_w / cov_w if cov_w else None
        sb = sw = None
        if hold_s and y in spine_s:
            ss = hold_s[spine_s[y]]; stw = sum(h["weight"] for h in ss) or 1e-9
            sbio = [h for h in ss if is_bio(h)]
            sw = sum(h["weight"] for h in sbio) / stw * 100; sb = len(sbio)
        row = [y, round(bw, 1), len(bio), round(un, 1) if un is not None else None,
               round(nr, 1) if nr is not None else None,
               round(sw, 1) if sw is not None else None, sb,
               round(bw - sw, 1) if sw is not None else None]
        for c, v in enumerate(row, 1): wq.cell(r, c, v)
        r += 1
    wq.cell(r + 1, 1, "Biotech flagged from Morningstar Industry. %unprofitable / %no-revenue are within the "
            "covered biotech names, by weight -- biotech is overwhelmingly pre-earnings, which is why the S&P 600 "
            "earnings screen excludes most of it.")

    # ---- Biotech in the Tail (strict biotech + broad life-sciences companion) ----
    wt = wb.create_sheet("Biotech in the Tail")
    wt.cell(1, 1, "How much of R2000G's low-quality tail is biotech / life sciences").font = TITLE
    _hdr(wt, 3, ["Year", "R2KG unprofitable wt%", "Biotech (pts)", "Biotech share of unprofitable %",
                 "Life-sci (pts)", "Life-sci share of unprofitable %",
                 "Never-prof wt%", "Biotech share of never-prof %", "Life-sci share of never-prof %"])
    r = 4
    for y in years:
        snap = hold_r[spine_r[y]]; tw = sum(h["weight"] for h in snap) or 1e-9; snap_dt = spine_r[y]
        un_w = un_bio = un_ls = nev_w = nev_bio = nev_ls = 0.0
        for h in snap:
            cf = fund_for(facts, hcik(h))
            if not cf: continue
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is None: continue
            cm = company_metrics(cf, fy0)
            if cm["prof_ni"] is False:
                un_w += h["weight"]
                if is_bio(h): un_bio += h["weight"]
                if is_lifesci(h): un_ls += h["weight"]
            if cm["cohort"] == "never_profitable":
                nev_w += h["weight"]
                if is_bio(h): nev_bio += h["weight"]
                if is_lifesci(h): nev_ls += h["weight"]
        row = [y, round(100*un_w/tw, 1), round(100*un_bio/tw, 1),
               round(100*un_bio/un_w, 1) if un_w else None,
               round(100*un_ls/tw, 1), round(100*un_ls/un_w, 1) if un_w else None,
               round(100*nev_w/tw, 1), round(100*nev_bio/nev_w, 1) if nev_w else None,
               round(100*nev_ls/nev_w, 1) if nev_w else None]
        for c, v in enumerate(row, 1): wt.cell(r, c, v)
        r += 1
    wt.cell(r + 1, 1, "Biotech = strict Morningstar 'Biotechnology'. Life-sci = biotech + pharma + diagnostics + "
            "medical devices/instruments (the broader unprofitable healthcare cut). Shares = of all unprofitable weight.")

    # ---- Unprofitable Tail Composition (by theme + by industry) ----
    comp_ind, comp_thm = {}, {}
    for y in years:
        snap = hold_r[spine_r[y]]; snap_dt = spine_r[y]; ind_w = {}; thm_w = {}
        for h in snap:
            cf = fund_for(facts, hcik(h))
            if not cf: continue
            fy0 = pick_fy0(cf, snap_dt)
            if fy0 is None: continue
            if company_metrics(cf, fy0)["prof_ni"] is not False: continue   # unprofitable only
            ind = (h.get("ms_industry") or "Unknown").strip() or "Unknown"
            ind_w[ind] = ind_w.get(ind, 0.0) + h["weight"]
            t = theme_of(ind); thm_w[t] = thm_w.get(t, 0.0) + h["weight"]
        comp_ind[y], comp_thm[y] = ind_w, thm_w

    theme_order = [lab for lab, _ in THEMES] + ["Other"]
    wct = wb.create_sheet("Unprofitable by Theme")
    wct.cell(1, 1, "What makes up R2000G's unprofitable weight -- by theme (% of unprofitable, by year)").font = TITLE
    _hdr(wct, 3, ["Year"] + theme_order)
    rr = 4
    for y in years:
        tot = sum(comp_thm[y].values()) or 1e-9
        row = [y] + [round(100 * comp_thm[y].get(t, 0) / tot, 1) for t in theme_order]
        for c, v in enumerate(row, 1): wct.cell(rr, c, v)
        rr += 1
    wct.cell(rr + 1, 1, "Themes mapped from Morningstar Industry. Shows the unprofitable tail rotating over time "
             "(e.g. biotech + software in 2021 -> biotech + hardware/electrical/semis in 2026).")
    ny = len(years)
    ch = LineChart(); ch.title = "Unprofitable tail composition by theme"; ch.height, ch.width = 9, 20
    ch.add_data(Reference(wct, min_col=2, max_col=6, min_row=3, max_row=3 + ny), titles_from_data=True)
    ch.set_categories(Reference(wct, min_col=1, min_row=4, max_row=3 + ny)); wct.add_chart(ch, "A" + str(rr + 4))

    tot_by_ind = {}
    for y in years:
        for ind, w in comp_ind[y].items(): tot_by_ind[ind] = tot_by_ind.get(ind, 0) + w
    topinds = [i for i, _ in sorted(tot_by_ind.items(), key=lambda x: -x[1])[:14]]
    wci = wb.create_sheet("Unprofitable by Industry")
    wci.cell(1, 1, "Unprofitable weight by Morningstar Industry (% of that year's unprofitable weight)").font = TITLE
    _hdr(wci, 3, ["Year"] + topinds + ["All other"])
    rr = 4
    for y in years:
        tot = sum(comp_ind[y].values()) or 1e-9
        shown = [comp_ind[y].get(i, 0) for i in topinds]
        row = [y] + [round(100 * w / tot, 1) for w in shown] + [round(100 * (tot - sum(shown)) / tot, 1)]
        for c, v in enumerate(row, 1): wci.cell(rr, c, v)
        rr += 1
    wci.cell(rr + 1, 1, "Columns = the 14 industries with the most unprofitable weight across all years; "
             "the rest rolled into 'All other'. Values are % of each year's total unprofitable weight.")
    wci.freeze_panes = "B4"

    # ---- monthly biotech attribution ----
    hdates = sorted(hold_r)
    months = [d for d in pdates if R["ret"].get(d) is not None and nearest_prior(hdates, d)]
    rows = []
    for d in months:
        snap = hold_r[nearest_prior(hdates, d)]; traw = sum(h["weight"] for h in snap) or 1.0
        bio_c = non_c = recon = 0.0; exbio = {"w": 0.0, "rw": 0.0}; bioonly = {"w": 0.0, "rw": 0.0}
        biow = 0.0
        for h in snap:
            wf = h["weight"] / traw
            if is_bio(h): biow += wf
            r_ = ret_for(h, d)
            if r_ is None: continue
            recon += wf * r_
            if is_bio(h):
                bio_c += wf * r_; bioonly["w"] += h["weight"]; bioonly["rw"] += h["weight"] * r_
            else:
                non_c += wf * r_; exbio["w"] += h["weight"]; exbio["rw"] += h["weight"] * r_
        actual = R["ret"][d]
        rows.append({"d": d, "actual": actual, "bio": bio_c, "non": non_c,
                     "residual": actual - recon, "biow": biow,
                     "exbio_ret": (exbio["rw"]/exbio["w"]) if exbio["w"] else None,
                     "bio_ret": (bioonly["rw"]/bioonly["w"]) if bioonly["w"] else None})
    if WINDOW_START:
        ws0 = date.fromisoformat(WINDOW_START); win = [x for x in rows if x["d"] >= ws0]
    else:
        win = rows[max(0, len(rows) - WINDOW_MONTHS):]

    # ---- Biotech Contribution ----
    wc = wb.create_sheet("Biotech Contribution")
    wc.cell(1, 1, "Biotech contribution to R2000G's return (Carino-linked)").font = TITLE
    _hdr(wc, 3, ["Segment", "Full period contrib %", "Avg weight %", "Window contrib %", "Avg weight % (window)"])
    f_cum, f_bio = carino_link(rows, "bio"); _, f_non = carino_link(rows, "non"); _, f_res = carino_link(rows, "residual")
    w_cum, w_bio = carino_link(win, "bio"); _, w_non = carino_link(win, "non"); _, w_res = carino_link(win, "residual")
    awt = sum(x["biow"] for x in rows)/len(rows) if rows else 0
    wwt = sum(x["biow"] for x in win)/len(win) if win else 0
    data = [("Biotech", _p(f_bio), _p(awt), _p(w_bio), _p(wwt)),
            ("Non-biotech", _p(f_non), _p(1-awt), _p(w_non), _p(1-wwt)),
            ("Unexplained (no return)", _p(f_res), None, _p(w_res), None),
            ("TOTAL = index return", _p(f_cum), 100.0, _p(w_cum), 100.0)]
    for i, row in enumerate(data, 4):
        for c, v in enumerate(row, 1): wc.cell(i, c, v)
    wc.cell(10, 1, "Biotech's contribution share vs its weight share shows whether biotech punched above its weight "
            "in driving the benchmark (a return an earnings-disciplined manager would have missed).")

    # ---- Biotech Counterfactual ----
    wcf = wb.create_sheet("Biotech Counterfactual")
    wcf.cell(1, 1, "Ex-biotech counterfactual on R2000G's own names (growth of $1)").font = TITLE
    _hdr(wcf, 3, ["Month", "R2000G index", "Ex-biotech", "Biotech-only"])
    gI = gE = gB = 1.0; r = 4
    for x in rows:
        gI *= (1 + x["actual"])
        if x["exbio_ret"] is not None: gE *= (1 + x["exbio_ret"])
        if x["bio_ret"] is not None: gB *= (1 + x["bio_ret"])
        for c, v in enumerate([f"{x['d']:%Y-%m-%d}", round(gI, 4), round(gE, 4), round(gB, 4)], 1):
            wcf.cell(r, c, v)
        r += 1
    def cum(seq, key):
        g = 1.0
        for x in seq:
            v = x["actual"] if key == "actual" else x[key]
            if v is not None: g *= (1 + v)
        return g - 1.0
    wcf.cell(r + 1, 1, "Cumulative return:").font = Font(bold=True)
    _hdr(wcf, r + 2, ["Window", "R2000G index", "Ex-biotech", "Biotech-only"])
    for k, (lab, seq) in enumerate([("Full period", rows),
                                    (f"Manager window ({WINDOW_MONTHS}m)" if not WINDOW_START else f"From {WINDOW_START}", win)]):
        for c, v in enumerate([lab, _p(cum(seq, "actual")), _p(cum(seq, "exbio_ret")), _p(cum(seq, "bio_ret"))], 1):
            wcf.cell(r + 3 + k, c, v)
    wcf.freeze_panes = "A4"

    # ---- Charts ----
    cs = wb.create_sheet("Charts"); ny = len(years)
    ch = LineChart(); ch.title = "Biotech weight: R2000G vs S&P 600 Growth"; ch.height, ch.width = 8, 18
    ch.add_data(Reference(wq, min_col=2, max_col=2, min_row=3, max_row=3+ny), titles_from_data=True)
    ch.add_data(Reference(wq, min_col=6, max_col=6, min_row=3, max_row=3+ny), titles_from_data=True)
    ch.set_categories(Reference(wq, min_col=1, min_row=4, max_row=3+ny)); cs.add_chart(ch, "A1")
    n = len(rows)
    ch2 = LineChart(); ch2.title = "Ex-biotech counterfactual (growth of $1)"; ch2.height, ch2.width = 8, 18
    ch2.add_data(Reference(wcf, min_col=2, max_col=4, min_row=3, max_row=3+n), titles_from_data=True)
    ch2.set_categories(Reference(wcf, min_col=1, min_row=4, max_row=3+n)); cs.add_chart(ch2, "A18")

    # ---- Notes ----
    nd = wb.create_sheet("Notes")
    for i, ln in enumerate([
        "R2000G biotech deep-dive.",
        f"Biotech = Morningstar Industry containing any of {BIO_KEYWORDS} (set BIOTECH_KEYWORDS to broaden).",
        "Weight & Quality: biotech weight in each index + %unprofitable / %no-revenue within R2000G biotech.",
        "In the Tail: strict biotech AND broad life-sciences (biotech+pharma+diagnostics+devices) share of the",
        "   unprofitable / never-profitable weight -- strict biotech understates the full unprofitable healthcare cut.",
        "Unprofitable by Theme / by Industry: what makes up the unprofitable tail over time -- it rotates",
        "   (biotech + software in 2021 -> biotech + hardware/electrical/semis in 2026).",
        "Contribution: biotech vs non-biotech contribution to R2000G's return (Carino-linked to the index).",
        "Counterfactual: R2000G's own names with biotech removed (reweighted) vs the index, and biotech-only.",
        "Weight/return tabs need no fundamentals; quality/tail tabs join fundamentals by CIK then ticker.",
    ], 1): nd.cell(i, 1, ln)
    nd.column_dimensions["A"].width = 115

    wb.save(OUT)
    print(f"  DONE -> {OUT.name}")
    if years:
        ly = years[-1]
        b = [h for h in hold_r[spine_r[ly]] if is_bio(h)]
        tw = sum(h["weight"] for h in hold_r[spine_r[ly]]) or 1
        print(f"  {ly}: R2000G biotech weight {100*sum(h['weight'] for h in b)/tw:.1f}% ({len(b)} names)")
    print(f"  window: biotech contrib {_p(w_bio)}% of index {_p(w_cum)}%  (avg biotech weight {_p(wwt)}%)")
    print(f"  window cumulative: index {_p(cum(rows[-len(win):],'actual'))}%  ex-biotech {_p(cum(win,'exbio_ret'))}%")


if __name__ == "__main__":
    build()
