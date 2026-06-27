"""
============================================================
r2k_dera_index.py  --  one-time index of every annual filing (10-K / 20-F / 40-F) in the SEC
DERA Financial Statement Data Sets, so the reconstruction engine can look up a filing by
(cik, fiscal-period) instantly instead of rescanning 60+ quarterly files every run.
============================================================
Scans each quarter's sub.txt ONCE (small file), keeps the annual filings for the target
universe, and records what we need to pick the right as-filed report:
    cik, name, sic, country, form, fye, period, fy, fp, filed, accepted, prevrpt, detail,
    adsh, quarter, is_ifrs?(guessed by form), is_original (earliest-filed, non-amended 10-K/20-F
    for that period -- the point-in-time filing we extract from)

TARGET UNIVERSE (whose filings to keep)
    default: every CIK in securities_crosswalk.csv (the Morningstar-derived universe).
    override with R2KG_CIK_FILE = a text/csv file with one CIK per line (or a 'cik' column),
    or R2KG_INDEX_ALL=1 to index ALL annual filers (much larger).

INPUTS
    DERA_DIR (default financial_statement_data_sets/zips) containing 2009q1, 2009q2 ... each an
    extracted folder OR a <quarter>.zip, with sub.txt inside.
OUTPUT
    dera_filing_index.csv

RUN:  python r2k_dera_index.py
============================================================
"""
from pathlib import Path
import os, re, io, csv, sys, zipfile
from collections import defaultdict

DERA_DIR = Path(os.environ.get("DERA_DIR", "financial_statement_data_sets/zips"))
BASE = Path(os.environ.get("R2KG_BASE", "."))
XWALK = BASE / "securities_crosswalk.csv"
CIK_FILE = os.environ.get("R2KG_CIK_FILE", "")
INDEX_ALL = os.environ.get("R2KG_INDEX_ALL", "") not in ("", "0", "false")
OUT = BASE / "dera_filing_index.csv"

ANNUAL_FORMS = {"10-K", "10-K/A", "10-KT", "10-KT/A", "20-F", "20-F/A", "40-F", "40-F/A"}
IFRS_FORMS = {"20-F", "20-F/A", "40-F", "40-F/A"}
QUARTER_RE = re.compile(r"^\d{4}q[1-4]$", re.IGNORECASE)


def load_targets():
    if INDEX_ALL:
        return None
    cs = set()
    # priority: explicit R2KG_CIK_FILE -> universe_ciks.csv (R2000G+600G, from r2k_build_maps.py)
    # -> the Morningstar crosswalk. The universe file makes step 6's 600G comparison work.
    src = CIK_FILE if (CIK_FILE and Path(CIK_FILE).exists()) else None
    if src is None and (BASE / "universe_ciks.csv").exists():
        src = str(BASE / "universe_ciks.csv")
    if src:
        print(f"  target universe from: {Path(src).name}")
        for line in open(src, encoding="utf-8-sig"):
            t = line.strip().split(",")[0].strip().strip('"')
            if t.lower() == "cik":
                continue
            if t.isdigit():
                cs.add(str(int(t)))
    elif XWALK.exists():
        print(f"  target universe from: {XWALK.name} (crosswalk) -- run r2k_build_maps.py to "
              f"include S&P 600 Growth names for step 6")
        for r in csv.DictReader(open(XWALK, encoding="utf-8")):
            c = (r.get("cik") or "").strip()
            if c.isdigit():
                cs.add(str(int(c)))
    return cs or None


def find_quarters():
    out = []
    if not DERA_DIR.exists():
        raise SystemExit(f"!! DERA dir not found: {DERA_DIR} (set DERA_DIR).")
    for p in sorted(DERA_DIR.iterdir()):
        if p.is_dir() and QUARTER_RE.match(p.name):
            out.append(p.name)
        elif p.suffix.lower() == ".zip" and QUARTER_RE.match(p.stem):
            out.append(p.stem)
    return sorted(set(out))


def open_sub(quarter):
    folder = DERA_DIR / quarter / "sub.txt"
    if folder.exists():
        return open(folder, encoding="utf-8", errors="replace")
    for zp in (DERA_DIR / f"{quarter}.zip", DERA_DIR / quarter / f"{quarter}.zip"):
        if zp.exists():
            zf = zipfile.ZipFile(zp)
            inner = next((n for n in zf.namelist() if n.lower().endswith("sub.txt")), None)
            if inner:
                return io.TextIOWrapper(zf.open(inner), encoding="utf-8", errors="replace")
    return None


WANT = ["adsh", "cik", "name", "sic", "countryba", "form", "fye", "period", "fy", "fp",
        "filed", "accepted", "prevrpt", "detail"]


def main():
    targets = load_targets()
    quarters = find_quarters()
    print(f"  DERA dir: {DERA_DIR}")
    print(f"  quarters found: {len(quarters)} ({quarters[0]}..{quarters[-1]})" if quarters else "  no quarters!")
    print(f"  universe: {'ALL annual filers' if targets is None else f'{len(targets)} target CIKs'}\n")

    rows = []
    for i, q in enumerate(quarters, 1):
        fh = open_sub(q)
        if fh is None:
            print(f"  [{i}/{len(quarters)}] {q}: sub.txt not found, skipped"); continue
        with fh:
            header = fh.readline().rstrip("\n").rstrip("\r").split("\t")
            ix = {c: j for j, c in enumerate(header)}
            kept = 0
            for line in fh:
                p = line.rstrip("\n").split("\t")
                if "form" not in ix or ix["form"] >= len(p):
                    continue
                if p[ix["form"]] not in ANNUAL_FORMS:
                    continue
                cik = p[ix["cik"]].strip() if "cik" in ix else ""
                cikn = str(int(cik)) if cik.isdigit() else cik
                if targets is not None and cikn not in targets:
                    continue
                rec = {k: (p[ix[k]] if k in ix and ix[k] < len(p) else "") for k in WANT}
                rec["cik"] = cikn
                rec["quarter"] = q
                rec["is_ifrs"] = "Y" if rec["form"] in IFRS_FORMS else ""
                rows.append(rec); kept += 1
        print(f"  [{i}/{len(quarters)}] {q}: kept {kept} annual filings", flush=True)

    # mark the ORIGINAL filing per (cik, period): a non-amended (prevrpt!=1, form has no '/A'),
    # earliest 'filed'. This is the point-in-time as-filed report we extract from.
    bykey = defaultdict(list)
    for r in rows:
        bykey[(r["cik"], r["period"])].append(r)
    for r in rows:
        r["is_original"] = ""
    n_orig = 0
    for key, lst in bykey.items():
        orig = [r for r in lst if r["prevrpt"] != "1" and not r["form"].endswith("/A")]
        pool = orig or lst
        pool.sort(key=lambda r: r["filed"])
        pool[0]["is_original"] = "Y"; n_orig += 1

    cols = WANT + ["quarter", "is_ifrs", "is_original"]
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(rows)

    uciks = len({r["cik"] for r in rows})
    ifrs = sum(1 for r in rows if r["is_ifrs"])
    print(f"\n  -> {OUT.name}: {len(rows):,} annual filings | {uciks:,} unique companies | "
          f"{n_orig:,} original (point-in-time) filings | {ifrs:,} IFRS (20-F/40-F)")
    yrs = sorted({r["period"][:4] for r in rows if r["period"][:4].isdigit()})
    if yrs:
        print(f"  fiscal-period years span: {yrs[0]}-{yrs[-1]}")


# --------------------------------------------------------------------------- selftest
def selftest():
    import tempfile
    d = Path(tempfile.mkdtemp()) / "zips"; (d / "2025q1").mkdir(parents=True)
    hdr = "adsh\tcik\tname\tsic\tcountryba\tform\tfye\tperiod\tfy\tfp\tfiled\taccepted\tprevrpt\tdetail\n"
    body = ("0001\t1664703\tBLOOM\t3690\tUS\t10-K\t1231\t20241231\t2024\tFY\t20250227\t2025-02-27\t0\t1\n"
            "0002\t1664703\tBLOOM\t3690\tUS\t10-K/A\t1231\t20241231\t2024\tFY\t20250401\t2025-04-01\t1\t1\n"
            "0003\t9999999\tFOREIGNCO\t2834\tIL\t20-F\t1231\t20241231\t2024\tFY\t20250315\t2025-03-15\t0\t1\n"
            "0004\t5555\tSKIPME\t1000\tUS\t10-Q\t0930\t20240930\t2024\tQ3\t20241101\t2024-11-01\t0\t1\n")
    (d / "2025q1" / "sub.txt").write_text(hdr + body, encoding="utf-8")
    global DERA_DIR, XWALK, OUT, INDEX_ALL
    DERA_DIR = d; INDEX_ALL = True; OUT = d / "idx.csv"
    main()
    got = list(csv.DictReader(open(OUT, encoding="utf-8")))
    forms = {r["form"] for r in got}
    orig = {(r["cik"], r["form"]) for r in got if r["is_original"] == "Y"}
    ifrs = {r["cik"] for r in got if r["is_ifrs"] == "Y"}
    ok = (len(got) == 3 and "10-Q" not in forms                      # quarterly excluded
          and ("1664703", "10-K") in orig                            # original = the non-amended 10-K
          and ("1664703", "10-K/A") not in orig                      # not the amendment
          and "9999999" in ifrs)                                     # 20-F flagged IFRS
    print(f"\n  SELFTEST: annual-only={'10-Q' not in forms}  original=non-amended 10-K={('1664703','10-K') in orig}  "
          f"ifrs-flag={'9999999' in ifrs}  -> {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
