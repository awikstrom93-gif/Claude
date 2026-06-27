"""
============================================================
r2k_dera_extract.py  --  reconstruct every filing's financial statements from the DERA sets,
at scale, into one cached file the identity classifier then consumes.
============================================================
Uses dera_filing_index.csv (from r2k_dera_index.py) to know each ORIGINAL (point-in-time)
annual filing and which quarter it lives in. Processes one quarter at a time: streams that
quarter's num.txt and pre.txt ONCE, keeping only the target filings' CONSOLIDATED (no-segment),
CURRENT-period facts, and joins presentation (statement / report / line / label) to values.

AS-FILED / POINT-IN-TIME: each fiscal year's numbers come from THAT year's own original 10-K
(current-period facts only) -- never a later filing's restated comparative.

OUTPUT  dera_facts.csv   one row per presented statement line:
    cik, fiscal_year, period, form, taxonomy(usgaap|ifrs|other), stmt, report, line, tag,
    version, qtrs, uom, value, negating, plabel
  (taxonomy is detected from the tags actually used, so a 20-F filing that reports in US GAAP
   is labelled usgaap, not ifrs.)

CONFIG
    DERA_DIR (default financial_statement_data_sets/zips)
    R2KG_YEARS   fiscal-period year range, default 2010-2026
    R2KG_SAMPLE_CIKS  comma CIKs -> only those (fast pilot run)
RUN:  python r2k_dera_extract.py            (full; long, shows progress)
      R2KG_SAMPLE_CIKS=1664703,36029 python r2k_dera_extract.py   (pilot)
SELFTEST: python r2k_dera_extract.py --selftest
============================================================
"""
from pathlib import Path
import os, io, csv, sys, zipfile
from collections import defaultdict

DERA_DIR = Path(os.environ.get("DERA_DIR", "financial_statement_data_sets/zips"))
BASE = Path(os.environ.get("R2KG_BASE", "."))
INDEX = BASE / "dera_filing_index.csv"
OUT = BASE / "dera_facts.csv"
Y0, Y1 = (int(x) for x in os.environ.get("R2KG_YEARS", "2010-2026").split("-"))
SAMPLE = {c.strip() for c in os.environ.get("R2KG_SAMPLE_CIKS", "").split(",") if c.strip()}
KEEP_QTRS = {"0", "4"}     # instants (BS) + annual durations (IS/CF); comparatives excluded by ddate


def open_member(quarter, name):
    folder = DERA_DIR / quarter / name
    if folder.exists():
        return open(folder, encoding="utf-8", errors="replace")
    for zp in (DERA_DIR / f"{quarter}.zip", DERA_DIR / quarter / f"{quarter}.zip"):
        if zp.exists():
            zf = zipfile.ZipFile(zp)
            inner = next((n for n in zf.namelist() if n.lower().endswith(name)), None)
            if inner:
                return io.TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace")
    return None


def header_ix(fh):
    cols = fh.readline().rstrip("\n").rstrip("\r").split("\t")
    return {c: i for i, c in enumerate(cols)}


def taxonomy_of(versions):
    """detect the reporting taxonomy from the tag versions actually used in the face statements."""
    us = sum(1 for v in versions if v.startswith("us-gaap"))
    ifrs = sum(1 for v in versions if v.startswith("ifrs"))
    if ifrs > us and ifrs > 0:
        return "ifrs"
    if us > 0:
        return "usgaap"
    return "other"


def load_targets():
    if not INDEX.exists():
        raise SystemExit(f"!! {INDEX.name} not found -- run r2k_dera_index.py first.")
    by_q = defaultdict(dict)     # quarter -> {adsh: meta}
    n = 0
    for r in csv.DictReader(open(INDEX, encoding="utf-8")):
        if r.get("is_original") != "Y":
            continue
        per = r.get("period", "")
        yr = per[:4]
        if not (yr.isdigit() and Y0 <= int(yr) <= Y1):
            continue
        if SAMPLE and r["cik"] not in SAMPLE:
            continue
        by_q[r["quarter"]][r["adsh"]] = r
        n += 1
    return by_q, n


def process_quarter(quarter, targets):
    """targets: {adsh: meta}. Returns list of output rows for this quarter's filings."""
    adsh_set = set(targets)
    # ---- num.txt: consolidated current-period values, keyed (adsh,tag,version,qtrs) ----
    fh = open_member(quarter, "num.txt")
    if fh is None:
        return [], f"{quarter}: num.txt missing"
    vals = defaultdict(dict)     # adsh -> {(tag,version,qtrs): value}
    with fh:
        ix = header_ix(fh)
        seg_i = ix.get("segments"); uom_i = ix.get("uom")
        tag_i, ver_i, dd_i, q_i, val_i = ix["tag"], ix["version"], ix["ddate"], ix["qtrs"], ix["value"]
        for line in fh:
            cut = line.find("\t")
            if cut < 0 or line[:cut] not in adsh_set:
                continue
            p = line.rstrip("\n").split("\t")
            if seg_i is not None and seg_i < len(p) and p[seg_i]:
                continue                                    # drop dimensional breakdowns
            adsh = p[0]; per = targets[adsh]["period"]
            if dd_i >= len(p) or p[dd_i] != per:
                continue                                    # current period only
            q = p[q_i] if q_i < len(p) else ""
            if q not in KEEP_QTRS:
                continue
            uom = p[uom_i] if (uom_i is not None and uom_i < len(p)) else ""
            v = p[val_i] if val_i < len(p) else ""
            if v == "":
                continue
            vals[adsh][(p[tag_i], p[ver_i], q)] = (v, uom)
    # ---- pre.txt: presentation; join to values ----
    fh = open_member(quarter, "pre.txt")
    if fh is None:
        return [], f"{quarter}: pre.txt missing"
    out = []
    tax_versions = defaultdict(list)
    with fh:
        ix = header_ix(fh)
        cstmt, crep, cline = ix.get("stmt"), ix.get("report"), ix.get("line")
        ctag, cver, cpl, cneg = ix["tag"], ix["version"], ix.get("plabel"), ix.get("negating")
        rowsbuf = defaultdict(list)
        for line in fh:
            cut = line.find("\t")
            if cut < 0 or line[:cut] not in adsh_set:
                continue
            p = line.rstrip("\n").split("\t")
            adsh = p[0]
            stmt = p[cstmt] if cstmt is not None and cstmt < len(p) else ""
            if stmt not in ("IS", "BS", "CF"):
                continue
            tag = p[ctag] if ctag < len(p) else ""
            ver = p[cver] if cver < len(p) else ""
            # pick the value: prefer qtrs 4 for IS/CF, 0 for BS; fall back to the other
            qpref = ("0", "4") if stmt == "BS" else ("4", "0")
            val = uom = None
            for q in qpref:
                hit = vals[adsh].get((tag, ver, q))
                if hit:
                    val, uom = hit; break
            if val is not None and ver.startswith(("us-gaap", "ifrs")):
                tax_versions[adsh].append(ver)
            rowsbuf[adsh].append([stmt,
                                  p[crep] if crep is not None and crep < len(p) else "",
                                  p[cline] if cline is not None and cline < len(p) else "",
                                  tag, ver, (val if val is not None else ""),
                                  (uom or ""), (p[cneg] if cneg is not None and cneg < len(p) else ""),
                                  (p[cpl] if cpl is not None and cpl < len(p) else "")])
    for adsh, meta in targets.items():
        tax = taxonomy_of(tax_versions.get(adsh, []))
        yr = meta["period"][:4]
        for (stmt, rep, ln, tag, ver, val, uom, neg, pl) in rowsbuf.get(adsh, []):
            out.append([meta["cik"], yr, meta["period"], meta["form"], tax, stmt, rep, ln,
                        tag, ver, "", uom, val, neg, pl])
    return out, None


def main():
    by_q, n = load_targets()
    print(f"  index: {INDEX.name}  | target original filings {n:,} in {Y0}-{Y1}"
          f"{' (SAMPLE '+','.join(sorted(SAMPLE))+')' if SAMPLE else ''}")
    print(f"  quarters to scan: {len(by_q)}\n")
    cols = ["cik", "fiscal_year", "period", "form", "taxonomy", "stmt", "report", "line",
            "tag", "version", "qtrs", "uom", "value", "negating", "plabel"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(cols)
        done = 0; total_rows = 0
        for q in sorted(by_q):
            rows, err = process_quarter(q, by_q[q])
            if err:
                print(f"  [{done+1}/{len(by_q)}] {err}")
            else:
                w.writerows(rows); total_rows += len(rows)
            done += 1
            print(f"  [{done}/{len(by_q)}] {q}: {len(by_q[q])} filings -> {len(rows):,} lines", flush=True)
    print(f"\n  -> {OUT.name}: {total_rows:,} statement lines from {n:,} filings")


# --------------------------------------------------------------------------- selftest
def selftest():
    import tempfile
    d = Path(tempfile.mkdtemp()); zips = d / "zips"; (zips / "2025q1").mkdir(parents=True)
    (zips / "2025q1" / "num.txt").write_text(
        "adsh\ttag\tversion\tddate\tqtrs\tuom\tsegments\tcoreg\tvalue\tfootnote\n"
        "0001\tRevenues\tus-gaap/2024\t20241231\t4\tUSD\t\t\t1000\t\n"
        "0001\tRevenues\tus-gaap/2024\t20231231\t4\tUSD\t\t\t900\t\n"          # prior-year comparative (drop)
        "0001\tRevenues\tus-gaap/2024\t20241231\t4\tUSD\tProduct\t\t600\t\n"   # segment (drop)
        "0001\tCostOfRevenue\tus-gaap/2024\t20241231\t4\tUSD\t\t\t700\t\n"
        "0001\tAssets\tus-gaap/2024\t20241231\t0\tUSD\t\t\t5000\t\n", encoding="utf-8")
    (zips / "2025q1" / "pre.txt").write_text(
        "adsh\treport\tline\tstmt\tinpth\trfile\ttag\tversion\tplabel\tnegating\n"
        "0001\t2\t1\tIS\t0\tR\tRevenues\tus-gaap/2024\tTotal revenue\t0\n"
        "0001\t2\t2\tIS\t0\tR\tCostOfRevenue\tus-gaap/2024\tCost of revenue\t0\n"
        "0001\t4\t1\tBS\t0\tR\tAssets\tus-gaap/2024\tTotal assets\t0\n"
        "0001\t9\t1\tCP\t0\tR\tDocumentType\tdei/2024\tdoc\t0\n", encoding="utf-8")  # non-statement (drop)
    idx = d / "dera_filing_index.csv"
    idx.write_text("adsh,cik,name,sic,countryba,form,fye,period,fy,fp,filed,accepted,prevrpt,detail,"
                   "quarter,is_ifrs,is_original\n"
                   "0001,1664703,BLOOM,3690,US,10-K,1231,20241231,2024,FY,20250227,x,0,1,2025q1,,Y\n",
                   encoding="utf-8")
    global DERA_DIR, INDEX, OUT
    DERA_DIR = zips; INDEX = idx; OUT = d / "facts.csv"
    main()
    got = list(csv.DictReader(open(OUT, encoding="utf-8")))
    byt = {r["tag"]: r for r in got}
    ok = (len(got) == 3                                  # IS rev + cogs, BS assets; CP dropped
          and byt["Revenues"]["value"] == "1000"         # current period, not 900 comparative, not 600 segment
          and byt["CostOfRevenue"]["value"] == "700"
          and byt["Assets"]["value"] == "5000" and byt["Assets"]["stmt"] == "BS"
          and all(r["taxonomy"] == "usgaap" for r in got))
    for r in got:
        print(f"   {r['stmt']:<3} {r['tag']:<16} = {r['value']:<6} tax={r['taxonomy']}")
    print(f"\n  SELFTEST: comparative+segment+nonstatement excluded, current-period kept -> "
          f"{'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
