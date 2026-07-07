"""
extract_revgaps.py -- pull ONLY the revenue-relevant as-filed rows for the capture-gap CIKs out of
the (huge) dera_facts.csv, into a small file you can send for tag diagnosis. Streams the big file, so
its size is irrelevant. RUN: python extract_revgaps.py   ->  dera_facts_revgaps.csv
"""
import csv

# capture-gap CIKs: R2000G constituents with net income but BLANK revenue (real companies, not
# genuine pre-revenue startups). Add/remove freely.
CIKS = {
    "1082754",  # Team Health (staffing)
    "1320414",  # Select Medical
    "1076195",  # Life Time Fitness
    "1745431",  # StoneCo (IFRS / Cayman)
    "1389170",  # Targa Resources (midstream)
    "107687",   # Winnebago
    "1364099",  # Innophos (chemicals)
    "720672",   # Stifel Financial (mis-detected financial)
    "1289848",  # Huron Consulting
    "1410384",  # Q2 Holdings (software)
    "880117",   # John B Sanfilippo (packaged foods)
    "773141",   # MDC Holdings / Sekisui (homebuilder)
    "1056386",  # Internap
    "1237746",  # Endurance International
    "1788348",  # Brookfield Infrastructure (IFRS)
}
# a fact is "revenue-relevant" if its tag looks like a top line. Broad on purpose -- we want to SEE
# whatever revenue-ish tag the filer used (that's the whole point of the diagnosis).
KEEP = ("revenu", "sales", "netinterestincome", "interestanddividend", "premium",
        "fees", "netoperating", "totalrevenu")

rows, cols = [], None
with open("dera_facts.csv", encoding="utf-8", errors="replace") as f:
    rdr = csv.DictReader(f)
    cols = rdr.fieldnames
    for r in rdr:
        cik = (r.get("cik") or "").lstrip("0")
        tag = (r.get("tag") or "").lower()
        if cik in CIKS and any(k in tag for k in KEEP):
            rows.append(r)

with open("dera_facts_revgaps.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=cols)
    w.writeheader(); w.writerows(rows)
print(f"  -> dera_facts_revgaps.csv : {len(rows)} revenue-ish rows for {len(CIKS)} CIKs")
