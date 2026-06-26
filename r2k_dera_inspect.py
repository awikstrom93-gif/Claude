"""
============================================================
r2k_dera_inspect.py  --  inspect the SEC DERA Financial Statement Data Sets and prototype
statement reconstruction for a handful of pilot companies, so we verify the real file schema
(no guessing) before building the identity engine.
============================================================
Reads ONE quarter's sub/num/pre(/tag) files (from extracted folders OR the quarterly .zip),
finds the pilot companies' 10-K filings, and for each prints the income statement / balance
sheet / cash flow as the filer PRESENTED them (pre.txt line order x num.txt current-period
value). It is schema-ADAPTIVE: it reads each file's header row and maps columns by name, then
prints what it found -- including whether a segment/dimension column exists and how often it
is populated (this decides how we isolate the consolidated face value).

CONFIG (env or edit below)
    DERA_DIR     folder that holds the quarters. Default: financial_statement_data_sets/zips
                 Works whether each quarter is an extracted folder (2025q1/sub.txt ...) or a
                 zip (2025q1.zip containing sub.txt ...).
    DERA_QUARTER which quarter to inspect. Default: 2025q1  (a Q1 holds the most 10-Ks)
    DERA_CIKS    optional comma-separated CIKs to override the pilot set

OUTPUT
    console summary + dera_inspect_<quarter>.txt   (small -- upload this back)
    dera_inspect_<quarter>.csv                     (reconstructed lines, all pilot filings)

RUN:  python r2k_dera_inspect.py
============================================================
"""
from pathlib import Path
import os, sys, io, zipfile, csv

DERA_DIR = Path(os.environ.get("DERA_DIR", "financial_statement_data_sets/zips"))
QUARTER = os.environ.get("DERA_QUARTER", "2025q1")

# pilot: cik -> (ticker, type)
PILOT = {
    "1664703": ("BE", "industrial"), "1807794": ("CRDO", "tech"), "1861560": ("NUVL", "biotech"),
    "36029": ("FFIN", "bank"), "230557": ("SIGI", "insurance"), "1040829": ("RHP", "REIT"),
    "913760": ("SNEX", "capital_markets"), "1334933": ("UEC", "oil_gas"),
    "840489": ("FCFS", "credit"), "703351": ("EAT", "retail"),
}
if os.environ.get("DERA_CIKS"):
    PILOT = {c.strip(): (c.strip(), "custom") for c in os.environ["DERA_CIKS"].split(",") if c.strip()}
PILOT_CIKS = {str(int(c)) for c in PILOT}     # normalize (DERA stores cik as integer, no zero pad)

OUT_TXT = Path(f"dera_inspect_{QUARTER}.txt")
OUT_CSV = Path(f"dera_inspect_{QUARTER}.csv")
STMT_NAME = {"IS": "INCOME STATEMENT", "BS": "BALANCE SHEET", "CF": "CASH FLOW",
             "EQ": "EQUITY", "CI": "COMPREHENSIVE INCOME", "CP": "COVER PAGE", "UN": "UNCLASSIFIED"}
_log = []


def say(s=""):
    print(s); _log.append(s)


def open_member(name):
    """open <name> (e.g. 'sub.txt') from the quarter, whether it's an extracted folder or a zip."""
    folder = DERA_DIR / QUARTER / name
    if folder.exists():
        return open(folder, encoding="utf-8", errors="replace")
    for zp in (DERA_DIR / f"{QUARTER}.zip", DERA_DIR / QUARTER / f"{QUARTER}.zip"):
        if zp.exists():
            zf = zipfile.ZipFile(zp)
            inner = next((n for n in zf.namelist() if n.lower().endswith(name)), None)
            if inner:
                return io.TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace")
    raise SystemExit(f"!! could not find {name} for {QUARTER} under {DERA_DIR} "
                     f"(looked for {QUARTER}/{name} and {QUARTER}.zip). Set DERA_DIR / DERA_QUARTER.")


def read_header(fh):
    line = fh.readline().rstrip("\n").rstrip("\r")
    cols = line.split("\t")
    return cols, {c: i for i, c in enumerate(cols)}


def main():
    say(f"DERA inspect -- quarter {QUARTER}, dir {DERA_DIR}")
    say(f"pilot CIKs: {sorted(PILOT_CIKS)}\n")

    # ---- sub.txt: find the pilot 10-K filings ----
    with open_member("sub.txt") as fh:
        cols, ix = read_header(fh)
        say(f"sub.txt columns ({len(cols)}): {cols}")
        need = ["adsh", "cik", "name", "form", "period", "fy", "fp", "filed"]
        miss = [c for c in need if c not in ix]
        if miss:
            say(f"  !! sub.txt missing expected columns {miss} -- schema differs, will adapt.")
        filings = {}   # adsh -> meta
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if len(p) <= ix.get("cik", 0):
                continue
            cik = p[ix["cik"]].strip()
            if cik not in PILOT_CIKS:
                continue
            form = p[ix["form"]] if "form" in ix else ""
            if not form.startswith("10-K"):
                continue
            adsh = p[ix["adsh"]]
            meta = {k: (p[ix[k]] if k in ix and ix[k] < len(p) else "") for k in need}
            meta["ticker_type"] = PILOT.get(cik, PILOT.get(str(int(cik)), ("?", "?")))
            filings[adsh] = meta
    say(f"\nfound {len(filings)} pilot 10-K filings in {QUARTER}:")
    for adsh, m in filings.items():
        say(f"   {m['ticker_type'][0]:<6} {m['ticker_type'][1]:<14} cik {m['cik']:<8} "
            f"period {m['period']}  fy{m['fy']}{m['fp']}  filed {m['filed']}  {adsh}")
    if not filings:
        say("\n  (no pilot filings this quarter -- try a different DERA_QUARTER, e.g. a Q1 a year "
            "later/earlier; calendar-year 10-Ks cluster in Q1.)")
        OUT_TXT.write_text("\n".join(_log), encoding="utf-8"); return
    adsh_set = set(filings)

    # ---- pre.txt: presentation (which statement / line / label each tag is on) ----
    with open_member("pre.txt") as fh:
        cols, ix = read_header(fh)
        say(f"\npre.txt columns ({len(cols)}): {cols}")
        pre = {a: [] for a in adsh_set}
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if not p or p[0] not in adsh_set:
                continue
            def g(c): return p[ix[c]] if c in ix and ix[c] < len(p) else ""
            pre[p[0]].append(dict(stmt=g("stmt"), line=g("line"), tag=g("tag"),
                                  version=g("version"), plabel=g("plabel"), negating=g("negating")))

    # ---- num.txt: values. Detect the segment/dimension column (key schema question) ----
    seg_col = None
    with open_member("num.txt") as fh:
        cols, ix = read_header(fh)
        say(f"\nnum.txt columns ({len(cols)}): {cols}")
        for cand in ("segments", "dimn", "dimh", "coreg"):
            if cand in ix:
                seg_col = cand if cand in ("segments", "dimn", "dimh") else seg_col
        say(f"  -> segment/dimension column detected: {seg_col!r}  "
            f"(rows with this populated are NON-consolidated breakdowns we must exclude)")
        num = {a: {} for a in adsh_set}      # adsh -> {(tag,ddate,qtrs): value}
        seg_populated = 0; seg_total = 0
        for line in fh:
            p = line.rstrip("\n").split("\t")
            if not p or p[0] not in adsh_set:
                continue
            def g(c): return p[ix[c]] if c in ix and ix[c] < len(p) else ""
            seg_total += 1
            seg = g(seg_col) if seg_col else ""
            if seg:
                seg_populated += 1
                continue                      # keep only consolidated (no-segment) facts
            uom = g("uom")
            if uom and uom != "USD":
                continue
            key = (g("tag"), g("ddate"), g("qtrs"))
            val = g("value")
            if val != "":
                num[p[0]][key] = val
        if seg_total:
            say(f"  -> of pilot-filing num rows, {seg_populated}/{seg_total} carried a segment "
                f"value (excluded); kept consolidated facts.")

    # ---- reconstruct each filing's statements ----
    rows_out = []
    for adsh, m in filings.items():
        period = m["period"]
        say("\n" + "=" * 78)
        say(f"{m['ticker_type'][0]} ({m['ticker_type'][1]})  cik {m['cik']}  period {period}  {adsh}")
        bystmt = {}
        for r in pre[adsh]:
            bystmt.setdefault(r["stmt"], []).append(r)
        for stmt in ("IS", "BS", "CF"):
            lines = bystmt.get(stmt, [])
            if not lines:
                say(f"  [{stmt}] (no lines tagged to this statement)"); continue
            say(f"  [{STMT_NAME.get(stmt, stmt)}]  ({len(lines)} presented lines)")
            try:
                lines = sorted(lines, key=lambda r: (int(r["line"]) if r["line"].isdigit() else 9999))
            except Exception:
                pass
            qprefs = ["0"] if stmt == "BS" else ["4", "1"]   # BS=instant; IS/CF=annual(4) else quarter
            shown = 0
            for r in lines:
                tag = r["tag"]
                val = None
                for q in qprefs:
                    val = num[adsh].get((tag, period, q))
                    if val is not None:
                        break
                if val is None:   # any period for this tag (comparatives) -- mark it
                    alt = [(k, v) for k, v in num[adsh].items() if k[0] == tag]
                    val = f"(no {period} value; {len(alt)} other-period facts)" if alt else "(no value)"
                disp = r["plabel"][:46]
                say(f"      {r['line']:>3} {tag:<48} {str(val):>20}   {disp}")
                rows_out.append([m["ticker_type"][0], m["cik"], stmt, r["line"], tag,
                                 r["version"], r["plabel"], r["negating"], val])
                shown += 1
                if shown >= 40:
                    say(f"      ... ({len(lines)-shown} more lines, full set in {OUT_CSV.name})"); break

    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "cik", "stmt", "line", "tag", "version", "plabel", "negating", "value_or_note"])
        w.writerows(rows_out)
    OUT_TXT.write_text("\n".join(_log), encoding="utf-8")
    say(f"\n  -> wrote {OUT_TXT.name} (upload this) and {OUT_CSV.name}")


if __name__ == "__main__":
    main()
