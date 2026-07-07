"""
extract_revgaps.py -- pull ONLY the revenue-relevant as-filed rows for a set of CIKs out of the (huge)
dera_facts.csv, into a small file you can send for tag diagnosis. Streams the big file, so its size is
irrelevant. RUN: python extract_revgaps.py   ->  dera_facts_revgaps.csv

CURRENT TARGET: the R2000G biotech names flagged NO-REVENUE in any 2023-2026 snapshot. Most are
genuinely pre-revenue (clinical-stage, no product / no collaboration income) and will produce NO rows
here -- that's the point: the ones that DO show a revenue-ish tag are the capture gaps (a collaboration
/ royalty / product-revenue tag the classifier isn't adopting yet, e.g. a company that tags under a
non-standard concept). Send the output back and those specific tags get whitelisted in validated_tags.
"""
import csv

# R2000G biotech CIKs flagged no-revenue (2023-2026). Add/remove freely.
CIKS = {
    "1042074",  # CymaBay Therapeutics Inc
    "1069530",  # Cassava Sciences Inc
    "1133416",  # Galectin Therapeutics Inc
    "1133869",  # Capricor Therapeutics Inc
    "1157601",  # Madrigal Pharmaceuticals Inc
    "1160308",  # Savara Inc
    "1211583",  # Fennec Pharmaceuticals Inc
    "1274792",  # Merrimack Pharmaceuticals Inc
    "1281895",  # Rocket Pharmaceuticals Inc
    "1285819",  # Omeros Corp
    "1314052",  # Anavex Life Sciences Corp
    "1341235",  # Aldeyra Therapeutics Inc
    "1348911",  # KalVista Pharmaceuticals Inc
    "1356090",  # Precigen Inc
    "1357459",  # Palisade Bio Inc
    "1374690",  # Larimar Therapeutics Inc
    "1390478",  # SELLAS Life Sciences Group Inc
    "1395937",  # Syndax Pharmaceuticals Inc
    "1400118",  # Sagimet Biosciences Inc
    "1401040",  # DiaMedica Therapeutics Inc
    "1404281",  # Eledon Pharmaceuticals Inc
    "1410098",  # Cormedix Inc
    "1410939",  # IVERIC bio Inc
    "1419041",  # Forte Biosciences Inc
    "1454789",  # Astria Therapeutics Inc
    "1468748",  # Kodiak Sciences Inc
    "1472091",  # PDS Biotechnology Corp
    "1484565",  # Soleno Therapeutics Inc
    "1498382",  # TuHURA Biosciences Inc
    "1501697",  # X4 Pharmaceuticals Inc
    "1501796",  # Aura Biosciences Inc
    "1506251",  # Citius Pharmaceuticals Inc
    "1509261",  # Rezolute Inc
    "1528115",  # Annexon Inc
    "1553643",  # Relmada Therapeutics Inc
    "1563880",  # Trevi Therapeutics Inc
    "1580149",  # BioVie Inc
    "1582313",  # Xenon Pharmaceuticals Inc
    "1583648",  # Palvella Therapeutics Inc
    "1598646",  # Minerva Neurosciences Inc
    "1599298",  # Summit Therapeutics Inc
    "1601485",  # Elicio Therapeutics Inc
    "1603454",  # Celcuity Inc
    "1604950",  # scPharmaceuticals Inc
    "1607678",  # Viking Therapeutics Inc
    "1622229",  # Cogent Biosciences Inc
    "1626971",  # Corvus Pharmaceuticals Inc
    "1636282",  # Spyre Therapeutics Inc
    "1637715",  # Reneo Pharmaceuticals Inc
    "1645469",  # Monopar Therapeutics Inc
    "1645569",  # DICE Therapeutics Inc
    "1649094",  # Vaxcyte Inc
    "1649989",  # Outlook Therapeutics Inc
    "1652935",  # Actuate Therapeutics Inc
    "1664710",  # Keros Therapeutics Inc
    "1672619",  # Enliven Therapeutics Inc
    "1673772",  # RAPT Therapeutics Inc
    "1681087",  # Tectonic Therapeutic Inc
    "1682639",  # Eyenovia Inc
    "1683553",  # Spruce Biosciences Inc
    "1689548",  # Praxis Precision Medicines Inc
    "1691082",  # LB Pharmaceuticals Inc
    "1707502",  # Solid Biosciences Inc
    "1710072",  # Edgewise Therapeutics Inc
    "1711279",  # Krystal Biotech Inc
    "1714899",  # Denali Therapeutics Inc
    "1724979",  # Rain Oncology Inc
    "1725160",  # Zentalis Pharmaceuticals Inc
    "1727196",  # Scholar Rock Holding Corp
    "1738021",  # Compass Therapeutics Inc
    "1744659",  # Akero Therapeutics Inc
    "1750149",  # Inhibikase Therapeutics Inc
    "1750284",  # Olema Pharmaceuticals inc
    "1759138",  # Cabaletta Bio Inc
    "1761918",  # Erasca Inc
    "1764013",  # Immunovant Inc
    "1766140",  # Unicycive Therapeutics Inc
    "1768446",  # Climb Bio Inc
    "1770069",  # MapLight Therapeutics Inc
    "1770121",  # Sana Biotechnology Inc
    "1773427",  # SpringWorks Therapeutics Inc
    "1776111",  # MBX Biosciences Inc
    "1778922",  # SpyGlass Pharma Inc
    "1783183",  # Phathom Pharmaceuticals Inc
    "1785173",  # 89bio Inc
    "1786205",  # Arcellx Inc
    "1790340",  # Immuneering Corp
    "1796280",  # ORIC Pharmaceuticals Inc
    "1798749",  # Jade Biosciences Inc
    "1799788",  # Greenwich LifeSciences Inc
    "1800315",  # Damora Therapeutics Inc
    "1805387",  # Cerevel Therapeutics Holdings Inc
    "1807120",  # Design Therapeutics Inc
    "1808805",  # Nautilus Biotechnology Inc
    "1808898",  # Benitec Biopharma Inc
    "1810182",  # ALX Oncology Holdings Inc
    "1813814",  # Definium Therapeutics Inc
    "1814114",  # Orchestra BioMed Holdings Inc
    "1815776",  # LENZ Therapeutics Inc
    "1816736",  # Disc Medicine Inc
    "1817229",  # Vor Biopharma Inc
    "1817241",  # Artiva Biotherapeutics Inc
    "1818382",  # Humacyte Inc
    "1818794",  # Dyne Therapeutics Inc
    "1827401",  # Bright Minds Biosciences Inc
    "1827635",  # Veradermics Inc
    "1831363",  # Terns Pharmaceuticals Inc
    "1831828",  # Vera Therapeutics Inc
    "1832168",  # Longboard Pharmaceuticals Inc
    "1835597",  # PepGen Inc
    "1840439",  # Biomea Fusion Inc
    "1841387",  # Candel Therapeutics Inc
    "1842295",  # Maze Therapeutics Inc
    "1842952",  # Context Therapeutics Inc
    "1845337",  # Day One Biopharmaceuticals Inc
    "1850270",  # ProKidney Corp
    "1851194",  # Ventyx Biosciences Inc
    "1851657",  # Vaxxinity Inc
    "1855644",  # Zura Bio Ltd
    "1860871",  # Tevogen Bio Holdings Inc
    "1861560",  # Nuvalent Inc
    "1863127",  # Tyra Biosciences Inc
    "1868279",  # ArriVent BioPharma Inc
    "1873835",  # Immix Biopharma Inc
    "1875558",  # Nuvectis Pharma Inc
    "1880438",  # AN2 Therapeutics Inc
    "1885522",  # Neumora Therapeutics Inc
    "1888012",  # HilleVax Inc
    "1894562",  # Prime Medicine Inc
    "1907108",  # Lexeo Therapeutics Inc
    "1933414",  # Mineralys Therapeutics Inc
    "1935979",  # Biohaven Ltd
    "1962918",  # Acelyrin Inc
    "1966494",  # Cargo Therapeutics Inc
    "1971543",  # Mural Oncology PLC
    "1974640",  # Apogee Therapeutics Inc
    "1994702",  # Kyverna Therapeutics Inc
    "1999480",  # Alto Neuroscience Inc
    "2012593",  # Rapport Therapeutics Inc
    "2023658",  # Bicara Therapeutics Inc
    "2036042",  # Sionna Therapeutics Inc
    "2040807",  # Metsera Inc
    "318306",  # Abeona Therapeutics Inc
    "886744",  # Geron Corp
    "907654",  # Oruka Therapeutics Inc
    "949858",  # Achieve Life Sciences Inc
}
# a fact is "revenue-relevant" if its tag looks like a top line. Broad on purpose -- we want to SEE
# whatever revenue-ish tag the filer used (collaboration / license / grant / milestone / royalty /
# product revenue included), which is exactly where clinical-stage names hide non-standard tags.
KEEP = ("revenu", "sales", "netinterestincome", "interestanddividend", "premium", "fees",
        "netoperating", "totalrevenu", "collab", "license", "grant", "milestone", "royalt",
        "contractwith", "productsales", "otherincome")

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
# quick per-cik tag summary to eyeball which names actually have a revenue tag (the gaps)
from collections import defaultdict
bycik = defaultdict(set)
for r in rows:
    bycik[(r.get("cik") or "").lstrip("0")].add(r.get("tag"))
print(f"  -> dera_facts_revgaps.csv : {len(rows)} revenue-ish rows for {len(bycik)}/{len(CIKS)} CIKs with any hit")
for c, tags in sorted(bycik.items()):
    print(f"     cik {c}: {sorted(tags)[:6]}")
