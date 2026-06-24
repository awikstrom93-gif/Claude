"""
============================================================
r2k_refresh_charts_data.py  --  push fresh data from the step-7 output INTO your charted
workbook, updating only the cell values and leaving every chart byte-for-byte untouched.
============================================================
Re-running step 7 rebuilds the workbook and round-trips charts through openpyxl, which
drops some chart formatting. This avoids that entirely: it opens your charted file as a
zip, rewrites only the cell VALUES inside each worksheet's XML to match the fresh step-7
output, and copies every other part (charts, drawings, styles, themes) verbatim. The
charts never pass through a chart library, so they are preserved exactly.

INPUTS  (R2KG_BASE)
    R2000G_SmallCapGrowth_Benchmark_Review.xlsx   the fresh step-7 output (DATA source)
    R2000G_charts_backup.xlsx                      your charted workbook (CHARTS to keep)
                                                   override with R2KG_CHARTS_FILE
OUTPUT
    R2000G_Benchmark_Review_charted.xlsx           fresh data + your exact charts
                                                   override with R2KG_CHARTS_OUT

Only cells that exist in BOTH files (same tab, same A1 ref) are updated -- so the charted
file's layout must match the step-7 layout (it will, since it started as a step-7 output).
Any extra rows in the fresh data (e.g. new constituents) beyond the charted file's range
are reported, not inserted. Cells you turned into formulas are left alone only if they
aren't data cells in the step-7 output.

RUN: python r2k_refresh_charts_data.py
============================================================
"""
from pathlib import Path
from xml.sax.saxutils import escape, unescape
import os, re, zipfile, shutil

import openpyxl

BASE = Path(os.environ.get("R2KG_BASE", "."))
DATA_FILE = BASE / "R2000G_SmallCapGrowth_Benchmark_Review.xlsx"
CHARTS_FILE = BASE / os.environ.get("R2KG_CHARTS_FILE", "R2000G_charts_backup.xlsx")
OUT = BASE / os.environ.get("R2KG_CHARTS_OUT", "R2000G_Benchmark_Review_charted.xlsx")


def col_letter(c):
    s = ""
    while c > 0:
        c, rem = divmod(c - 1, 26); s = chr(65 + rem) + s
    return s


def build_cell(ref, style, val):
    st = f' s="{style}"' if style else ""
    if isinstance(val, bool):
        return f'<c r="{ref}"{st} t="b"><v>{1 if val else 0}</v></c>'
    if isinstance(val, (int, float)):
        v = ("%d" % val) if (isinstance(val, int) or float(val).is_integer()) else repr(val)
        return f'<c r="{ref}"{st}><v>{v}</v></c>'
    return f'<c r="{ref}"{st} t="inlineStr"><is><t xml:space="preserve">{escape(str(val))}</t></is></c>'


CELL_RE = re.compile(r'<c r="([A-Z]+\d+)"([^>]*?)(/>|>.*?</c>)', re.DOTALL)
STYLE_RE = re.compile(r'\bs="(\d+)"')


def update_worksheet_xml(xml_text, fresh):
    """fresh: {A1ref: value}. Replace matching cells in place; report which refs were not found."""
    seen = set()
    def repl(m):
        ref = m.group(1)
        if ref not in fresh:
            return m.group(0)
        seen.add(ref)
        sm = STYLE_RE.search(m.group(2))            # preserve the cell's style (number format etc.)
        return build_cell(ref, sm.group(1) if sm else None, fresh[ref])
    new_text = CELL_RE.sub(repl, xml_text)
    missing = [r for r in fresh if r not in seen]
    return new_text, len(seen), missing


def main():
    if not DATA_FILE.exists(): raise SystemExit(f"!! {DATA_FILE.name} not found (run step 7 first).")
    if not CHARTS_FILE.exists(): raise SystemExit(f"!! {CHARTS_FILE.name} not found "
                                                  f"(run r2k_snapshot_charts.py, or set R2KG_CHARTS_FILE).")

    # 1. fresh values from the step-7 output, keyed by sheet -> {A1ref: value}
    src = openpyxl.load_workbook(DATA_FILE, data_only=True)
    fresh = {}
    for sn in src.sheetnames:
        ws = src[sn]; cells = {}
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    cells[f"{col_letter(c.column)}{c.row}"] = c.value
        fresh[sn] = cells
    src.close()

    # 2. map sheet name -> worksheet xml path in the charts file
    with zipfile.ZipFile(CHARTS_FILE) as z:
        wbxml = z.read("xl/workbook.xml").decode("utf-8")
        relsxml = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    rid_target = {}
    for rel in re.findall(r"<Relationship\b[^>]*/>", relsxml):
        i = re.search(r'Id="([^"]+)"', rel); t = re.search(r'Target="([^"]+)"', rel)
        if i and t: rid_target[i.group(1)] = t.group(1)
    name_to_path = {}
    for s in re.findall(r"<sheet\b[^>]*/>", wbxml):
        nm = re.search(r'name="([^"]+)"', s); rid = re.search(r'r:id="([^"]+)"', s)
        if nm and rid and rid.group(1) in rid_target:
            tgt = rid_target[rid.group(1)]
            path = tgt[1:] if tgt.startswith("/") else "xl/" + tgt
            name_to_path[unescape(nm.group(1))] = path.replace("xl/xl/", "xl/")

    # 3. update each matching worksheet's XML in place
    updated_parts = {}; report = []
    for sn, path in name_to_path.items():
        if sn not in fresh: continue
        with zipfile.ZipFile(CHARTS_FILE) as z:
            xml = z.read(path).decode("utf-8")
        new_xml, n_set, missing = update_worksheet_xml(xml, fresh[sn])
        updated_parts[path] = new_xml.encode("utf-8")
        report.append((sn, n_set, len(missing)))

    in_charts = set(name_to_path); in_data = set(fresh)
    only_data = sorted(in_data - in_charts)
    only_charts = sorted(in_charts - in_data)

    # 4. re-zip: replace updated worksheet parts, copy everything else (charts!) verbatim
    if OUT.exists(): OUT.unlink()
    with zipfile.ZipFile(CHARTS_FILE) as zin, zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = updated_parts.get(item.filename) or zin.read(item.filename)
            zout.writestr(item, data)

    print(f"  data source : {DATA_FILE.name}")
    print(f"  charts file : {CHARTS_FILE.name}")
    print(f"  -> {OUT.name}  (charts copied verbatim; only cell values refreshed)\n")
    print(f"  {'tab':<26}{'cells updated':>14}{'not-found':>11}")
    for sn, n, miss in report:
        flag = "  <- has extra fresh rows" if miss else ""
        print(f"  {sn[:26]:<26}{n:>14}{miss:>11}{flag}")
    if only_data:
        print(f"\n  tabs in the fresh data but NOT in your charts file (won't be added): {only_data}")
    if only_charts:
        print(f"  tabs in your charts file but not in the fresh data (left as-is): {only_charts}")
    print("\n  'not-found' = fresh cells with no matching cell in the charts file (extra rows). If a charted")
    print("  tab shows many, its layout drifted from step 7 -- re-snapshot that tab's charts from a current run.")


if __name__ == "__main__":
    main()
