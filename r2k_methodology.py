"""
============================================================
r2k_methodology.py  --  generate METHODOLOGY.md, a concise standalone reader's guide to the
benchmark-review workbook: WHAT each tab shows, HOW the data was built, and the conventions/caveats
behind every number -- so anyone can talk through the analysis without opening 42 tabs.
============================================================
The "how it was done" narrative (data foundation, pipeline, conventions, key adjustments) is stable
process knowledge and lives here as static prose. The tab-by-tab guide and the glossary are pulled
LIVE from the workbook's own `Reading Guide` and `Glossary` tabs, so they can never drift from what
the workbook actually contains -- and a coverage check warns if any tab is documented nowhere.

Deliberately FIGURE-LIGHT: results live in the memo (r2k_memo.py) and the workbook. This document is
about method, so re-running it after a data refresh changes only the tab list, never a headline
number that could fall out of sync.

RUN:      python r2k_methodology.py            (writes METHODOLOGY.md next to the workbook)
SELFTEST: python r2k_methodology.py --selftest (parse + coverage check, write nothing)
Override with R2KG_WORKBOOK / R2KG_BASE / R2KG_METHODOLOGY_OUT.
============================================================
"""
from pathlib import Path
import os, sys

def _esc(s):
    """Escape pipes so workbook text (e.g. 'R2000G | 600G | Diff') can't break a markdown table."""
    return str(s).replace("|", "\\|").strip()


BASE = Path(os.environ.get("R2KG_BASE", "."))
WB = BASE / os.environ.get("R2KG_WORKBOOK", "R2000G_SmallCapGrowth_Benchmark_Review.xlsx")
OUT = BASE / os.environ.get("R2KG_METHODOLOGY_OUT", "METHODOLOGY.md")

# Front-matter tabs the workbook carries but the Reading Guide doesn't describe. One-liners live here;
# the builder verifies each named tab is actually present and warns on any workbook tab covered neither
# here nor in the Reading Guide. (The panel-native exhibit tabs -- Quality Factor Spreads, Solvency
# Tail, Cohort Persistence, Valuation of the Tail -- are documented in the Reading Guide itself, so they
# are intentionally NOT repeated here.)
SUPPORT_TABS = {
    "Executive Summary": "The one-page version of the whole review: the question, the three-part answer, and the bottom line, each pointing to the tabs that prove it.",
    "Reading Guide": "What each analytical tab shows and how to read it (the source of the tab guide below).",
    "Glossary": "Plain-language definition of every metric and convention used in the workbook.",
    "Contents": "The tab index, grouped by section.",
    "Key Charts": "The exhibit charts (Growth of $1, % unprofitable by weight, the earnings-screen counterfactual) in one place.",
    "Data Reliability": "How trustworthy the reconstructed fundamentals are, by weight: the share of index weight whose three statements tie out cleanly vs. flagged for review, so a reader can weight the conclusions accordingly.",
}

# The methodology narrative -- static process knowledge. (Markdown; headings are '## ...'.)
NARRATIVE = r"""
## What this review covers

This is a study of what the Russell 2000 Growth index is actually made of, and how that composition has
shifted since 2012, read against the S&P SmallCap 600 Growth as a foil. The two indices draw from the
same asset class but part ways on one rule: the S&P 600 admits only companies with positive trailing
GAAP earnings, while the Russell index screens for nothing. That single difference turns the pair into a
natural experiment — hold them side by side and the effect of an earnings discipline on a small-cap
growth portfolio comes into view.

The tabs build the portrait across four dimensions and follow each through the years. The profitability
mix: how much of the index earns nothing, and how that tail has grown. The pre-commercial edge: the
weight in companies with no revenue at all, and the biotech book that drives it. Concentration and
breadth: how top-heavy the index has become and how narrow its leadership. And the return footprint of
all three — what the makeup has meant for performance. The companion memo carries the current figures;
this document explains how each is built and what each tab shows.

The work began with a practical question — why earnings-disciplined managers trail the Russell benchmark
in certain windows — and that thread runs underneath what follows. But the subject here is the index
itself: its makeup, and how it has changed.

## How the fundamentals were built

Every fundamental here is rebuilt from the SEC filings themselves rather than pulled from a vendor feed,
so each figure traces back to a specific as-filed 10-K.

The source is the SEC's DERA (XBRL Financial Statement) data sets. For each quarter, the structured
facts from every 10-K, 20-F, and 40-F are indexed and extracted, and a classification engine reassembles
the income statement, balance sheet, and cash flow for each company-year.

Values are read as originally reported in each 10-K, with no restatement or blending of vintages — what
the market saw at the time is what the analysis uses. Selection is point-in-time: a constituent's fiscal
year at any snapshot is the latest 10-K filed before that date, never numbers that were not yet public.
Membership is historical and survivorship-free, so a name appears in a given year only if it was actually
in the index then.

The engine does more than copy tags across. It enforces the accounting identities — the income statement
has to foot to net income, the balance sheet has to balance, cash-flow D&A has to tie to the income
statement — and accepts a value only once the statement ties out. Where a non-standard XBRL tag leaves a
blank, the gap is filled first from the identities (reconstructing what must be true) and only then from
validated as-filed tags, which closes the hole at the source instead of patching in a number.

A few adjustments are made deliberately, in the cases where the raw filing would distort an index
aggregate. A commodity or securities broker-dealer such as StoneX reports "revenue" grossed up by
pass-through physical-commodity sales — tens of billions that bear no relation to an operating company's
revenue and would swamp any index total. Those filers are carried on a net operating-revenue basis, the
treatment banks and insurers already get, and only where a strict matched-book signature holds (gross and
net margins both near zero), so ordinary low-margin businesses are left alone.

Only the consolidated registrant is kept. Filers with public debt include Rule 3-10 guarantor schedules,
where a parent-only and a subsidiary column carry the same tags as the consolidated company; dropping the
co-registrant columns stops a parent holding company's small stand-alone figure from overwriting the
consolidated total.

Reliability is measured, not assumed. The Data Reliability tab reports, by index weight, how much of the
reconstruction ties out cleanly against how much is flagged for review, so a reader can weigh the
conclusions accordingly. The headline results rest on the high-confidence core.

## What "no revenue" means

The top line is taken from the consolidated figure as filed, under a broad set of tags — the standard
Revenues and RevenueFromContractWithCustomer concepts plus industry lines such as homebuilding, hospital
patient-service, marine, mining, fitness, and franchisor revenue — and it is read wherever the filer puts
it: on a standalone income statement, on a combined statement of operations and comprehensive income, or
on an uncategorized one. Two rules then govern what a blank top line actually means.

The first is that financial-sector filers are not counted as having no revenue. Banks, insurers, mortgage
REITs, asset managers, and BDCs lead with net interest income, premiums, or fees rather than a revenue
tag, so a blank there is a matter of definition, not a sign of a pre-commercial business. These names are
carried without a revenue figure and left out of the no-revenue cohort; otherwise a bank would sit
alongside a clinical-stage biotech. What remains in the cohort is genuinely pre-commercial weight, which
is overwhelmingly biotech and drives the step-up in 2026.

The second is a bounded limitation. A handful of filers tag their consolidated revenue only with a
business-segment dimension and never report an undimensioned company total — M.D.C. Holdings from 2012 to
2017 and Meritage Homes from 2018 on, among a few others. Rather than sum segment members into a total,
which risks double-counting nested sub-tiers or dropping inter-segment eliminations, these company-years
are left blank. The effect stays under about one percentage point of index weight in any year, sits
entirely in historical periods (mostly names no longer in the index), and does not touch the current 2026
reading. It is a deliberate call to favor accuracy over coverage: a blank says less than a reconstructed
total that might be wrong.

## Conventions in the analysis

Index-level figures are reported as dollar aggregates — the sum of numerators over the sum of
denominators, as though the index were one large company — which is the index-representative measure and
reconciles to FactSet. The per-name distribution views also carry a weight-weighted average and a median,
because a simple average is thrown off by tiny-revenue names posting large losses.

Each company is counted once in the dollar totals. A name can sit in the index under two share classes
(Central Garden's CENTA and CENT, for instance) or appear twice in a holdings file, and both rows carry
the same fundamentals. The dollar totals — index revenue, net income, the dollar-aggregate margins —
de-duplicate by company so nothing is counted twice, while the weight-based percentages keep every row,
since the split weights already add up to the company's true index weight.

Profitability cohorts are point-in-time labels drawn from each name's as-filed net-income history:
profitable (positive net income in the latest filed year), fallen (once profitable, no longer),
never-profitable (no profitable year on record), and unknown (net income not reported). The
never-profitable weight is the sharpest single expression of the earnings-screen gap.

Attribution is Carino-linked, so single-period cohort contributions sum exactly to the index's cumulative
multi-period return, leaving only a small, explicitly labeled residual for coverage and weight drift
between snapshots.

The counterfactual is the cleanest of the tests. Instead of comparing two different indices, it rebuilds
the Russell index's own constituents as a profitable-only (or ex-biotech) portfolio, reweighted monthly.
The distance between the real index path and this screened path is the realized cost or benefit of the
screen with the universe held fixed, and its full-period result lands close to the actual S&P 600 Growth
return — two independent constructions pointing at the same mechanism.

The manager window is taken from the data, not chosen. The Perf Window Proof tab locates where the
Russell index's cumulative excess over the S&P index troughed and began a sustained run, and lays out a
range of candidate windows so the conclusion does not rest on a single start month.

Ratios use average (opening and closing) denominators, following the CFA convention, and per-name ratios
are winsorized before any averaging.
""".strip("\n")

NARRATIVE_ARC = r"""
## Reading it in a minute

Start with Qual Comparison — the profitability mix. It sets the two indices side by side, year by year,
and the picture is consistent: the Russell benchmark carries many more points of unprofitable and
never-profitable weight in every year, and the gap has widened over the decade. Bio Weight & Quality
shows where most of that low-quality weight lives, in biotech, and how much more pre-commercial that
book has become.

Then Conc Weight and Conc Breadth — the shape of the index. A long stretch of unusually wide breadth
gives way to a sharp concentration in the last few years, with market leadership narrowing to a handful
of names.

Finish with Attr Contribution and Attr Counterfactual — what the makeup has meant. Splitting the return
by quality cohort shows the unprofitable tail carrying far more of it than its weight in the recent
rally; the counterfactual reruns the index on its own profitable names to size that effect against the
full cycle.

The through-line is that the two benchmarks are structurally different portfolios and have grown more so
— which is why the S&P SmallCap 600 Growth is often the better yardstick for a quality-disciplined
mandate.
""".strip("\n")


# --------------------------------------------------------------------------- workbook parsing
def _reading_guide(wb):
    """Parse the Reading Guide into [(section, [(tab, what, how), ...]), ...] preserving order.
    A section-header row has text in col A but no col-B description; a tab row has both."""
    ws = wb["Reading Guide"]
    sections, cur = [], None
    for r in ws.iter_rows(min_row=1, values_only=True):
        a = str(r[0]).strip() if r and r[0] is not None else ""
        b = str(r[1]).strip() if (r and len(r) > 1 and r[1] is not None) else ""
        c = str(r[2]).strip() if (r and len(r) > 2 and r[2] is not None) else ""
        if not a or a.lower().startswith("reading guide") or a == "Tab":
            continue
        if b:                                  # tab row (name + description)
            if cur is None:
                cur = ("", [])
                sections.append(cur)
            cur[1].append((a, b, c))
        else:                                  # section header (name only)
            cur = (a, [])
            sections.append(cur)
    return [(s, tabs) for s, tabs in sections if tabs]


def _glossary(wb):
    ws = wb["Glossary"]
    out = []
    for r in ws.iter_rows(min_row=1, values_only=True):
        a = str(r[0]).strip() if r and r[0] is not None else ""
        b = str(r[1]).strip() if (r and len(r) > 1 and r[1] is not None) else ""
        c = str(r[2]).strip() if (r and len(r) > 2 and r[2] is not None) else ""
        if not a or a.lower().startswith("glossary") or a == "Metric":
            continue
        if b:
            out.append((a, b, c))
    return out


def coverage(wb, sections):
    """Every workbook tab should be documented either in the Reading Guide or in SUPPORT_TABS.
    Returns (uncovered_tabs, stale_support) so the run can warn without failing."""
    guided = {t for _, tabs in sections for t, _, _ in tabs}
    present = set(wb.sheetnames)
    uncovered = [s for s in wb.sheetnames if s not in guided and s not in SUPPORT_TABS]
    stale = [s for s in SUPPORT_TABS if s not in present]
    return uncovered, stale


# --------------------------------------------------------------------------- build
def build(wb):
    sections = _reading_guide(wb)
    gloss = _glossary(wb)
    uncovered, stale = coverage(wb, sections)
    n_tabs = len(wb.sheetnames)

    P = []
    P.append("# Methodology & Reader's Guide")
    P.append("### Russell 2000 Growth vs. S&P SmallCap 600 Growth — benchmark review\n")
    P.append(f"*Companion to `{WB.name}` ({n_tabs} tabs) and `R2000G_SCG_Benchmark_Review_Memo.md`. "
             f"This guide is generated from the workbook's own Reading Guide and Glossary, so the tab "
             f"list and definitions below always match the workbook; the method narrative is stable "
             f"process documentation.*\n")
    P.append("---\n")
    P.append(NARRATIVE + "\n")
    P.append("---\n")
    P.append(NARRATIVE_ARC + "\n")
    P.append("---\n")

    # tab-by-tab guide, grouped by the Reading Guide's own sections
    P.append("## What each tab shows\n")
    for section, tabs in sections:
        if section:
            P.append(f"### {section}\n")
        P.append("| Tab | What it shows | How to read it |")
        P.append("|---|---|---|")
        for tab, what, how in tabs:
            P.append(f"| **{_esc(tab)}** | {_esc(what)} | {_esc(how)} |")
        P.append("")

    # supporting / front-matter / exhibit tabs
    support_present = [(t, d) for t, d in SUPPORT_TABS.items() if t in wb.sheetnames]
    if support_present:
        P.append("### Front matter & standalone exhibits\n")
        P.append("| Tab | What it shows |")
        P.append("|---|---|")
        for t, d in support_present:
            P.append(f"| **{_esc(t)}** | {_esc(d)} |")
        P.append("")

    if uncovered:
        P.append("> ⚠️ **Coverage note:** the following workbook tab(s) are documented neither in the "
                 "Reading Guide nor here, so this guide may be incomplete — add them to the Reading "
                 f"Guide or to `SUPPORT_TABS` in `r2k_methodology.py`: {', '.join(uncovered)}.\n")

    # glossary
    P.append("---\n")
    P.append("## Glossary\n")
    P.append("| Term | Definition | Notes |")
    P.append("|---|---|---|")
    for term, dfn, note in gloss:
        P.append(f"| **{_esc(term)}** | {_esc(dfn)} | {_esc(note)} |")
    P.append("")
    P.append("---\n")
    P.append("*Generated by `r2k_methodology.py` from the workbook. Re-run after any workbook change so "
             "the tab guide and glossary stay in sync.*")
    return "\n".join(P) + "\n", (uncovered, stale)


# --------------------------------------------------------------------------- entrypoints
def _load():
    import openpyxl
    if not WB.exists():
        raise SystemExit(f"!! workbook not found: {WB}  (run r2k_report.py first, or set R2KG_WORKBOOK)")
    return openpyxl.load_workbook(WB, read_only=True, data_only=True)


def main():
    wb = _load()
    doc, (uncovered, stale) = build(wb)
    OUT.write_text(doc, encoding="utf-8")
    sections = _reading_guide(wb)
    ntabs = sum(len(t) for _, t in sections)
    print(f"  -> {OUT.name}: {len(doc.splitlines())} lines  "
          f"({ntabs} analytical tabs from Reading Guide + {len(SUPPORT_TABS)} support/exhibit tabs, "
          f"{len(_glossary(wb))} glossary terms)")
    if uncovered:
        print(f"  !! {len(uncovered)} workbook tab(s) documented nowhere: {', '.join(uncovered)}")
    if stale:
        print(f"  !! SUPPORT_TABS names a tab not in the workbook (renamed?): {', '.join(stale)}")


def selftest():
    wb = _load()
    sections = _reading_guide(wb)
    gloss = _glossary(wb)
    uncovered, stale = coverage(wb, sections)
    doc, _ = build(wb)
    print(f"  sections: {len(sections)}  analytical tabs: {sum(len(t) for _,t in sections)}  "
          f"glossary terms: {len(gloss)}")
    print(f"  coverage: {'OK (every tab documented)' if not uncovered else 'GAPS -> '+', '.join(uncovered)}")
    if stale:
        print(f"  stale SUPPORT_TABS: {', '.join(stale)}")
    print(f"  build OK: {len(doc.splitlines())} lines")
    print("  SELFTEST:", "PASS" if not uncovered and not stale else "WARN")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
