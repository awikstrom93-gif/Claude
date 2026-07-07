"""
extract_revgaps.py -- pull the revenue-relevant as-filed rows for a set of CIKs out of the (huge)
dera_facts.csv, into a small file for tag/period diagnosis. Streams the big file, so its size is
irrelevant. RUN: python extract_revgaps.py   ->  dera_facts_revgaps.csv

CURRENT TARGET: the R2000G/SP600G names with BLANK revenue but POSITIVE net income -- real operating
companies (homebuilders, hospitals, RV, midstream, packaged foods, leasing) that obviously HAVE
revenue, so a blank top line is a capture gap, not genuine no-revenue. Meritage (a plain homebuilder)
uses the STANDARD RevenueFromContractWithCustomerExcludingAssessedTax tag yet comes through blank, so
the question is not "which tag" but WHY the annual value isn't being adopted -- look at the printed
(tag, qtrs, period, value) per CIK: is the annual (qtrs=4) revenue row present at all, and under which
period/fiscal_year? That distinguishes an EXTRACT gap (row absent) from a CLASSIFY period-match bug
(row present, qtrs=4, but not adopted).
"""
import csv

# R2000G/SP600G CIKs with blank revenue + positive net income (from blank_rev_r2k_panel). Add/remove freely.
CIKS = {
    "38984",  # Real Industry Inc
    "40211",  # GATX Corp
    "46129",  # Allied Motion Technologies Inc
    "82166",  # Raven Industries Inc
    "83402",  # Resource America Inc Class A
    "107687",  # Winnebago Industries Inc
    "720672",  # Stifel Financial Corp.
    "773141",  # M D C Holdings Inc
    "783324",  # Vista Gold Corp
    "798783",  # Universal Health Realty Income Tru
    "809248",  # Carrols Restaurant Group Inc
    "833079",  # Meritage Homes Corp
    "845289",  # Harvest Natural Resources, Inc.
    "880117",  # John B Sanfilippo & Son Inc
    "890541",  # Banco Latinoamericano de Comercio 
    "899629",  # Acadia Realty Trust
    "946673",  # Banner Corp
    "1006269",  # Loral Space & Communications, Inc.
    "1030749",  # GulfMark Offshore, Inc.
    "1076195",  # Life Time Fitness, Inc.
    "1082754",  # Team Health Holdings Inc
    "1112920",  # GlobalSCAPE Inc
    "1285819",  # Omeros Corp
    "1289848",  # Huron Consulting Group Inc
    "1313918",  # CIFC Corp
    "1320414",  # Select Medical Holdings Corp
    "1364099",  # Innophos Holdings, Inc.
    "1389170",  # Targa Resources Corp
    "1401521",  # United Insurance Holdings Corp
    "1403853",  # Nuverra Environmental Solutions In
    "1409970",  # LendingClub Corp
    "1411906",  # Ampio Pharmaceuticals Inc
    "1413159",  # Textainer Group Holdings Ltd
    "1428205",  # ARMOUR Residential REIT Inc
    "1464343",  # Atlanticus Holdings Corp
    "1467760",  # Apollo Commercial Real Estate Fina
    "1476034",  # Metropolitan Bank Holding Corp
    "1498233",  # Cepton Inc
    "1510400",  # Tahoe Resources Inc
    "1581091",  # RE/MAX Holdings Inc Class A
    "1612720",  # NextDecade Corp
    "1631596",  # KKR Real Estate Finance Trust Inc
    "1643953",  # Purple Innovation Inc
    "1697152",  # ConvergeOne Holdings Inc A
    "1713952",  # Vivint Smart Home Inc Ordinary Sha
    "1720821",  # PAE Inc
    "1745431",  # StoneCo Ltd Class A
    "1766478",  # Angel Oak Mortgage REIT Inc Ordina
    "1788348",  # Brookfield Infrastructure Corp Ord
    "1802457",  # Origin Materials Inc Shs
    "1812173",  # Vicarious Surgical Inc
    "1818502",  # OppFi Inc Ordinary Shares - Class 
    "1820875",  # CXApp Inc Ordinary Shares - Class 
    "1822145",  # Presto Automation Inc
    "1838406",  # BKV Corp
    "1838615",  # AlTi Global Inc Ordinary Shares - 
    "1856031",  # Vivid Seats Inc Class A
    "1856236",  # European Wax Center Inc Ordinary S
    "1871130",  # Brookfield Business Corp Ordinary 
    "1883814",  # Southland Holdings Inc
}
KEEP = ("revenu", "sales", "netinterestincome", "interestanddividend", "premium", "fees",
        "netoperating", "totalrevenu", "homebuilding", "homesales", "contractrevenue")

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

# DECISIVE per-cik/per-year breakdown: does an annual (qtrs=4) revenue-ish row exist, and does it
# carry a VALUE?  That single fact separates the three failure modes:
#   VALUE  -> row present + nonblank  => CLASSIFY did not adopt it (REV-list / priority / plausibility)
#   BLANK  -> row present but value="" => EXTRACT dropped it (ddate!=period: 52/53-wk or period rounding)
#   (none) -> no annual revenue-ish row at all => not in the IS presentation, or a financial-sector
#             filer whose top line is interest/premium/fees (has_rev=False is then CORRECT, not a gap)
from collections import defaultdict
per_cy = defaultdict(lambda: {"val": [], "blank": []})   # (cik, fy) -> tags with value / blank
allq = defaultdict(set)                                    # cik -> set of qtrs seen on revenue-ish rows
for r in rows:
    cik = (r.get("cik") or "").lstrip("0")
    allq[cik].add(str(r.get("qtrs")))
    if str(r.get("qtrs")) in ("4", "4.0"):
        fy = r.get("fiscal_year")
        tag = r.get("tag")
        if (r.get("value") or "").strip():
            per_cy[(cik, fy)]["val"].append(tag)
        else:
            per_cy[(cik, fy)]["blank"].append(tag)
print(f"  -> dera_facts_revgaps.csv : {len(rows)} rows for "
      f"{len(set((r.get('cik') or '').lstrip('0') for r in rows))} CIKs\n")
ciks_seen = sorted(allq, key=lambda x: int(x))
noann = sorted(str(int(c)) for c in CIKS if str(int(c)) not in {str(int(x)) for x in ciks_seen})
for c in ciks_seen:
    yrs = sorted(fy for (cc, fy) in per_cy if cc == c)
    verdicts = []
    for fy in yrs:
        d = per_cy[(c, fy)]
        if d["val"]:
            verdicts.append(f"{fy}=VALUE({d['val'][0]})")
        elif d["blank"]:
            verdicts.append(f"{fy}=BLANK({d['blank'][0]})")
    tail = f"  qtrs_seen={sorted(allq[c])}" if not per_cy else ""
    print(f"     cik {c}: {'; '.join(verdicts) if verdicts else 'no annual revenue-ish row'}{tail}")
if noann:
    print(f"\n  CIKs with NO revenue-ish rows at all (financial-sector top line, or not indexed): {noann}")
