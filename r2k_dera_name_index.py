"""
============================================================
r2k_dera_name_index.py  --  a universe-INDEPENDENT company-name -> CIK index built from every
DERA sub.txt, so a holding that arrives with NO CIK (the delisted-biotech problem: ADRO/Aduro,
ADXS/Advaxis, ALVR/AlloVir, ANGN/Angion, AVRO/AVROBIO, CARA, ...) can be resolved by its own
point-in-time NAME instead of falling into the "Unknown (no fundamentals)" cohort.
============================================================
WHY A SEPARATE INDEX (not dera_filing_index.csv):
  dera_filing_index.csv is FILTERED to the resolved target universe, so by construction it can
  never contain a name we FAILED to resolve. This index is the opposite -- it scans EVERY filer
  in sub.txt (no universe filter), so the missing biotechs (which all filed 10-Ks) are present.
  It runs ONCE, before r2k_build_maps.py, and rarely needs rebuilding (only when new DERA
  quarters are added).

POINT-IN-TIME: sub.txt's `name` is the filer's name AS OF that filing, so a company that renamed
  (Aduro -> Chinook, Array Biopharma -> ...) appears under EVERY historical name. We index all
  name variants -> CIK, so a holdings row from any era matches the name it carried then.

CONSERVATIVE BY DESIGN: only names that map to exactly ONE cik (that has at least one ANNUAL
  filing, i.e. real fundamentals) are auto-resolvable. A normalized name shared by >1 cik is
  written with ambiguous=Y and is NEVER auto-resolved -- it lands in the review file instead.
  We diagnose, we don't guess.

INPUTS   DERA_DIR (default financial_statement_data_sets/zips), same layout r2k_dera_index reads.
OUTPUT   dera_name_index.csv   norm_name, cik, display_name, sic, has_annual, n_filings, ambiguous

RUN:      python r2k_dera_name_index.py
SELFTEST: python r2k_dera_name_index.py --selftest
============================================================
"""
from pathlib import Path
import os, csv, sys, re
from collections import defaultdict

# reuse the EXACT same sub.txt reader as the filing index (single source for how DERA is read)
import r2k_dera_index as di

BASE = Path(os.environ.get("R2KG_BASE", "."))
OUT = BASE / "dera_name_index.csv"

# corporate designators / share-class noise stripped from the END of a name (repeatedly), plus
# "the" from the front. Kept deliberately small so distinct businesses stay distinct
# ("X Therapeutics" != "X Pharmaceuticals"); collisions that DO occur are flagged, not merged.
_SUFFIX = {
    "inc", "incorporated", "corp", "corporation", "co", "company", "companies", "ltd", "limited",
    "llc", "lp", "llp", "plc", "sa", "nv", "ag", "ab", "oyj", "spa", "se", "asa", "as",
    "holdings", "holding", "group", "grp", "adr", "ads", "new", "cl", "class", "a", "b", "c",
    "i", "ii", "iii", "series", "common", "stock", "shares", "share", "ordinary", "units",
    "unit", "wt", "warrants", "rights", "trust",
}
_PAREN = re.compile(r"\([^)]*\)")
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def norm_name(raw):
    """Normalize a company name to a comparison key. Deterministic and shared by the resolver
    (r2k_build_maps) so both sides normalize IDENTICALLY -- the single source of the rule."""
    if not raw:
        return ""
    s = str(raw).lower().replace("&", " and ")
    s = _PAREN.sub(" ", s)          # drop parentheticals: "Foo (Delaware)" -> "Foo"
    s = _PUNCT.sub(" ", s)          # punctuation -> space
    toks = s.split()
    while toks and toks[0] == "the":
        toks.pop(0)
    while toks and toks[-1] in _SUFFIX:
        toks.pop()
    return " ".join(toks)


def build(quarters):
    """Fold every sub.txt row into per-CIK identity. Returns {cik: {names,sic,has_annual,n}}."""
    per_cik = defaultdict(lambda: {"names": set(), "sic": "", "has_annual": False, "n": 0})
    for i, q in enumerate(quarters, 1):
        fh = di.open_sub(q)
        if fh is None:
            print(f"  [{i}/{len(quarters)}] {q}: sub.txt not found, skipped"); continue
        with fh:
            header = fh.readline().rstrip("\n").rstrip("\r").split("\t")
            ix = {c: j for j, c in enumerate(header)}
            if "cik" not in ix or "name" not in ix:
                print(f"  [{i}/{len(quarters)}] {q}: no cik/name column, skipped"); continue
            n = 0
            for line in fh:
                p = line.rstrip("\n").split("\t")
                def cell(col):
                    j = ix.get(col)
                    return p[j].strip() if (j is not None and j < len(p)) else ""
                cik = cell("cik")
                if not cik.isdigit():
                    continue
                cikn = str(int(cik))
                nm = cell("name")
                if not nm:
                    continue
                rec = per_cik[cikn]
                rec["names"].add(nm)
                rec["n"] += 1
                if cell("form") in di.ANNUAL_FORMS:
                    rec["has_annual"] = True
                sic = cell("sic")
                if sic and not rec["sic"]:
                    rec["sic"] = sic
                n += 1
        print(f"  [{i}/{len(quarters)}] {q}: {n} filer-rows", flush=True)
    return per_cik


def invert(per_cik):
    """{norm_name: [(cik, rec)]} across all name variants, then flatten with an ambiguous flag."""
    by_norm = defaultdict(dict)          # norm -> {cik: rec}  (dedup a cik seen via >1 raw name)
    for cik, rec in per_cik.items():
        for raw in rec["names"]:
            nn = norm_name(raw)
            if nn:
                by_norm[nn][cik] = rec
    rows = []
    for nn, cikmap in by_norm.items():
        ambiguous = len(cikmap) > 1
        for cik, rec in cikmap.items():
            display = sorted(rec["names"])[0]
            rows.append({
                "norm_name": nn, "cik": cik, "display_name": display, "sic": rec["sic"],
                "has_annual": "Y" if rec["has_annual"] else "", "n_filings": rec["n"],
                "ambiguous": "Y" if ambiguous else "",
            })
    rows.sort(key=lambda r: (r["norm_name"], r["cik"]))
    return rows


def load_name_index(path=None, full=False):
    """Consumer helper (used by r2k_build_maps): {norm_name: cik} for UNAMBIGUOUS names that have
    a real annual filing. Ambiguous / annual-less names are intentionally excluded -- callers that
    want to inspect them read the CSV directly. With full=True, values are the full record
    {cik, display_name, sic} so a caller can enrich a review file for eyeballing."""
    path = Path(path) if path else OUT
    if not path.exists():
        return {}
    out = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("ambiguous") == "Y" or r.get("has_annual") != "Y":
            continue
        nn = r.get("norm_name", "")
        if nn:
            out[nn] = ({"cik": r["cik"], "display_name": r.get("display_name", ""),
                        "sic": r.get("sic", "")} if full else r["cik"])
    return out


def main():
    quarters = di.find_quarters()
    print(f"  DERA dir: {di.DERA_DIR}")
    print(f"  quarters found: {len(quarters)}" +
          (f" ({quarters[0]}..{quarters[-1]})" if quarters else " -- none!") + "\n")
    per_cik = build(quarters)
    rows = invert(per_cik)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["norm_name", "cik", "display_name", "sic",
                                          "has_annual", "n_filings", "ambiguous"])
        w.writeheader(); w.writerows(rows)
    n_names = len({r["norm_name"] for r in rows})
    n_amb = len({r["norm_name"] for r in rows if r["ambiguous"] == "Y"})
    n_resolvable = len(load_name_index())
    print(f"\n  -> {OUT.name}: {len(per_cik):,} unique CIKs | {n_names:,} normalized names")
    print(f"     {n_amb:,} names are ambiguous (shared by >1 CIK -- never auto-resolved)")
    print(f"     {n_resolvable:,} names are auto-resolvable (unambiguous + has an annual filing)")
    print(f"\n  Next: re-run r2k_build_maps.py -- it now uses this as the NAME fallback so no-CIK")
    print(f"        holdings (delisted biotechs) resolve by name, then re-run the DERA pipeline.")


# --------------------------------------------------------------------------- selftest
def selftest():
    assert norm_name("Aduro Biotech, Inc.") == "aduro biotech"
    assert norm_name("The Descartes Systems Group Inc.") == "descartes systems"
    assert norm_name("AlloVir, Inc. Class A Common Stock") == "allovir"
    assert norm_name("Angion Biomedica Corp.") == "angion biomedica"
    assert norm_name("Foo Pharmaceuticals (Delaware)") == "foo pharmaceuticals"
    # distinct businesses stay distinct
    assert norm_name("X Therapeutics Inc") != norm_name("X Pharmaceuticals Inc")

    per_cik = {
        "100": {"names": {"Aduro Biotech, Inc."}, "sic": "2836", "has_annual": True, "n": 5},
        "200": {"names": {"AlloVir, Inc."}, "sic": "2836", "has_annual": True, "n": 3},
        # two different CIKs sharing a normalized name -> ambiguous, must NOT auto-resolve
        "300": {"names": {"Nexus Inc"}, "sic": "1234", "has_annual": True, "n": 2},
        "301": {"names": {"Nexus Inc."}, "sic": "5678", "has_annual": True, "n": 2},
        # unambiguous but no annual filing -> excluded from auto-resolve
        "400": {"names": {"OnlyQuarterly LP"}, "sic": "6798", "has_annual": False, "n": 4},
    }
    rows = invert(per_cik)
    import tempfile
    p = Path(tempfile.mkdtemp()) / "n.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["norm_name", "cik", "display_name", "sic",
                                          "has_annual", "n_filings", "ambiguous"])
        w.writeheader(); w.writerows(rows)
    idx = load_name_index(p)
    ok = (idx.get("aduro biotech") == "100" and idx.get("allovir") == "200"
          and "nexus" not in idx                       # ambiguous excluded
          and "onlyquarterly" not in idx)              # no-annual excluded
    print(f"  SELFTEST norm+invert+load (ambiguous & annual-less excluded): {'PASS' if ok else 'FAIL'}")
    if not ok:
        raise SystemExit("  name-index selftest FAILED")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
