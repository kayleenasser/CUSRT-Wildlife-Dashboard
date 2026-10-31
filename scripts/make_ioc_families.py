"""Turn the IOC World Bird List master list into data/ioc_families.csv (genus, family, family_english).

Run once whenever a new IOC version comes out (download master_ioc_list_vX.Y.xlsx from
https://www.worldbirdnames.org/new/ioc-lists/master-list-2/):
    python scripts/make_ioc_families.py master_ioc_list_v15.2.xlsx
    
"""

import csv
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "ioc_families.csv"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def sheet_rows(path):
    """Each row of the first sheet as {column letter: text}."""
    with zipfile.ZipFile(path) as z:
        shared_xml, sheet_xml = z.read("xl/sharedStrings.xml"), z.read("xl/worksheets/sheet1.xml")
    # Text cells hold an index into the workbook's shared strings
    shared = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in ET.fromstring(shared_xml).iter(NS + "si")]
    for row in ET.fromstring(sheet_xml).iter(NS + "row"):
        cells = {}
        for c in row.iter(NS + "c"):
            v = c.find(NS + "v")
            if v is None:
                continue
            cells[re.match(r"[A-Z]+", c.get("r"))[0]] = (shared[int(v.text)] if c.get("t") == "s" else v.text).strip()
        yield cells


def main():
    xlsx = sys.argv[1] if len(sys.argv) > 1 else "master_ioc_list_v15.2.xlsx"
    rows = sheet_rows(xlsx)
    title = next(rows).get("C", "")     # "IOC WORLD BIRD LIST (15.2) ..."
    citation = next(rows).get("C", "")  # "Gill, F, ..."
    header = next(r for r in rows if r.get("D") == "Family (Scientific)")
    col = {name: letter for letter, name in header.items()}
    family = family_english = None
    genera = {}
    # Families and genera are on their own rows, each followed by its members
    for r in rows:
        if r.get(col["Family (Scientific)"]):
            family, family_english = r[col["Family (Scientific)"]], r.get(col["Family (English)"], "")
        elif r.get(col["Genus"]):
            genera[r[col["Genus"]]] = (family, family_english)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        version = re.search(r"\((\d+\.\d+)\)", title)
        f.write(f"# Genus -> family from the IOC World Bird List{f' v{version[1]}' if version else ''}: "
                "https://www.worldbirdnames.org/new/classification/family-index-2/\n")
        f.write(f"# Credit: {citation} Lines starting with # are notes.\n")
        w = csv.writer(f)
        w.writerow(["genus", "family", "family_english"])
        w.writerows([g, *fam] for g, fam in sorted(genera.items()))
    print(f"Wrote {len(genera)} genera in {len({fam for fam, _ in genera.values()})} families to {OUT}")


if __name__ == "__main__":
    main()
