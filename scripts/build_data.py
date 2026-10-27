"""Build data/collisions.json for the dashboard.

By default pulls every report inside Calgary from the Global Bird Collision Mapper's public layer
("Bird Collision Data - public", run by FLAP Canada). Each find is normalized to the data contract in README.md and written as a flat JSON array:

    [{"date": "2025-09-14", "animal": "Bird", "status": "Deceased", "species": "Dark-eyed Junco", "genus": "Junco",
      "family": "Passerellidae", "family_common": "New World Sparrows", "source": "CUSRT", "report": 1160123}, ...]

`source` is "CUSRT", "Public" or "Other Wildlife Organization" (see the rules above WILDLIFE_ORG_REPORTS). `genus` and `family` follow the
IOC World Bird List; they're also filled in for group-only IDs ("Sparrow sp." -> Passerellidae) and are null
when not even the family is known. Only those fields are written, so names, emails, notes and GPS never reach
the site.

Usage:
    python scripts/build_data.py                                   # BirdMapper, all Calgary reports
    python scripts/build_data.py --group "Calgary Urban Species Response Team"

Standard library only.
"""

import argparse
import csv
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "collisions.json"
ALPHA_CODES = ROOT / "data" / "alpha_codes.csv"
IOC_FAMILIES = ROOT / "data" / "ioc_families.csv"
NABAT_CODES = ROOT / "data" / "NABat_Species_Codes.csv"

GBCM_LAYER = ("https://services3.arcgis.com/Fl8N7M7DU9xVkwl3/arcgis/rest/services/"
              "Bird_Collision_Data_Map_view/FeatureServer/0")
GBCM_FIELDS = ("objectid,dateutc,status,species,alpha_code,count_dead,count_alive,count_unknown,scavenged,notes,"
               "groupname,third_party_involvement")
CALGARY_BBOX = "-114.32,50.84,-113.86,51.22"
CALGARY_TZ = ZoneInfo("America/Edmonton")
CUSRT_GROUP = "Calgary Urban Species Response Team"  # GBCM groupname; everything else counts as "Public"
# Who reported a find: "CUSRT", "Public" or "Other Wildlife Organization" (CUSRT, Oct 2026).
# A report in the CUSRT group counts as "CUSRT" when it came from the team's survey work. That includes strikes
# building staff, cleaners, security or passersby reported to a CUSRT shift or volunteer (also when security passed
# them on days later), even if nobody from the team handled the bird, since they wouldn't have been reported without
# the survey. Reports a CUSRT member only entered count as "Public":
#  - entered from iNaturalist ("Reported to ... project on iNaturalist", "documentation from iNat")
#  - submissions to CUSRT's public window strike form (entered by the team) / Messenger / member of the public / friend
FROM_INATURALIST = re.compile(r"\b(reported|documentation)\b[^.]{0,80}\binat(uralist)?\b", re.I)
PUBLIC_SUBMISSION = re.compile(r"\b(strike|submission|public) form|form submission|public (record|window strike|submission)|"
                               r"via messenger|by (a )?member of (the )?public|reported by friend", re.I)
# Team finds entered outside the CUSRT group ("CMSRT walk", "CUSRT Dawn shift"). CMSRT is CUSRT's name before the
# rebrand. These go through the same rules as reports in the group.
TEAM_FIND = re.compile(r"\bc[mu]srt\b[^.;]{0,15}\b(shift|walk|find|survey)\b|collected by our team", re.I)
# Finds documented by a wildlife organization (Calgary Wildlife / CWRS, AIWC, ...), by GBCM objectid. CUSRT checks
# each one, and that the organization is a certified one, before it's added. None = checked, not an org find.
# The name is written to the find's `organization` field and shown in the dashboard's report table.
# Reports that look like one are listed in the run report until they're added here.
WILDLIFE_ORG_REPORTS = {
    598790: "Calgary Wildlife",  # 2019-10-04: "Photo credit: Calgary Wildlife Rehabilitation Society staff"
    323529: "FLAP",  # 2018-05-11: filed under the FLAP Canada group (FLAP's 2023 import)
}
# "documented by CWRS", "Photo credit: Calgary Wildlife ... staff"; not "taken to Calgary Wildlife"
WILDLIFE_ORG_MENTION = re.compile(r"\b((documented|reported|submitted|found) by|photo credit:?)\s+(the\s+)?(cwrs|calgary wildlife|"
                                  r"aiwc|alberta institute for wildlife|[a-z ]{0,30}wildlife (rehab|rescue|society|centre|center))",
                                  re.I)
# One-off decisions by CUSRT (Oct 26, 2026), by GBCM objectid
SOURCE_OVERRIDES = {
    1018375: "CUSRT",  # 2021-09-20: window washer saw a magpie take the bird, reported to a volunteer
    1176600: "CUSRT",  # 2026-08-19: security guard's bird, gone when the team checked
    1168037: "CUSRT",  # 2025-09-04: staff reported a grounded bat to the shift and called CWRS; bat couldn't be found
    # Entered outside the CUSRT group by a team member
    1142997: "CUSRT",  # 2023-09-19: passerby reported a bird around 5 am; the team went, bird was gone
    1139005: "CUSRT",  # 2023-04-29: a team member's own find
}

BAT_MENTION = re.compile(r"\bbats?\b", re.I)
# Bat mentions that aren't a bat find ("Calgary Birds and Bats Window Strike project", "out looking for bats")
BAT_NOT_A_FIND = re.compile(r"birds? and bats?|looking for bats", re.I)
# Reports with no Dead/Alive status are read from their notes, using CUSRT's rules (Oct 2026):
#  - patrols that found nothing and impacts left on glass (imprints, marks, feathers stuck to windows) are left out
#  - scavenged, crushed, feather piles, partial remains, or a bird "left" for the team: Deceased
#  - otherwise the status is what was seen last (flew off, rescued): Alive
#  - anything else is left out and listed in the run report
NO_FIND_PATROL = re.compile(r"\bno finds?\b|\bzero finds?\b", re.I)
GLASS_IMPACT = re.compile(r"imprint|images? of birds?|impact marks?|powder mark|wing marks|stuck to the window|"
                          r"window strike with feathers", re.I)
DEAD_IN_NOTES = re.compile(r"scaveng|deceased|\bdead\b|crushed|feather pile|partial", re.I)
ALIVE_IN_NOTES = re.compile(r"\bflew\b|took off|\balive\b|rehab|rescued", re.I)
# One-off decisions by CUSRT for reports the rules can't settle, by GBCM objectid (None = leave out).
# Only used for reports without a Dead/Alive status: when BirdMapper says Dead or Alive, that's what counts.
# Values are what the dashboard shows: "Deceased", "Alive" or None.
OUTCOME_OVERRIDES = {
    1172536: "Alive",  # 2026-04-20: strike witnessed, no recovery flight seen, bird not found
    1163331: None,     # 2025-06-06: "Heard hit." only
    1148654: None,     # 2024-04-15: status Unknown, no notes
}
# Bat species come from NABat's species codes (data/NABat_Species_Codes.csv): common name, scientific name or
# 4-letter code in the report. These shorter forms also turn up in notes (NABat code of the species they mean).
BAT_ALIASES = {
    r"silver[- ]?hair": "LANO",
    r"hoary": "LACI",
    r"red bat": "LABO",
}
# A bare "myotis" names only the genus
BAT_GENUS_ONLY = re.compile(r"\bmyotis\b", re.I)
# The IOC list is birds only, so bat families are set here by genus. Every bat in Alberta is a vesper bat, so
# that's also the family of bats not identified to genus.
BAT_FAMILY = ("Vespertilionidae", "Vesper Bats")
BAT_FAMILIES = {
    **dict.fromkeys(["Eumops", "Molossus", "Nyctinomops", "Tadarida"], ("Molossidae", "Free-tailed Bats")),
    **dict.fromkeys(["Artibeus", "Brachyphylla", "Choeronycteris", "Diphylla", "Leptonycteris", "Macrotus",
                     "Stenoderma"], ("Phyllostomidae", "Leaf-nosed Bats")),
    "Mormoops": ("Mormoopidae", "Ghost-faced Bats"),
    "Noctilio": ("Noctilionidae", "Bulldog Bats"),
}

# Anything with these words is an uncertain ID and counts as "Unknown".
HEDGES = re.compile(r"\b(unknown|unknow|unkn|ukno|unk|unsure|unidentified|possible|possibly|poss|probable|probably|likely|"
                    r"maybe|suspected|mix|tbd|sp|spp|species|some kind|either|or|n/?a|none)\b|\?|^$", re.I)
# A group name with no species ("Sparrow", "A sparrow", "Woodpecker") also counts as "Unknown", but still
# tells us the genus or family (IOC names) for those charts. Groups are read as they'd be meant in Calgary:
# "sparrow" is a New World sparrow (House Sparrows are always named), "flycatcher" a tyrant flycatcher.
TAXON_GROUPS = {
    "sparrow": (None, "Passerellidae"),
    "spizella": ("Spizella", "Passerellidae"),
    "warbler": (None, "Parulidae"),
    "woodpecker": (None, "Picidae"),
    "sapsucker": ("Sphyrapicus", "Picidae"),
    "gull": (None, "Laridae"),
    "flycatcher": (None, "Tyrannidae"),
    "empidonax": ("Empidonax", "Tyrannidae"),
    "pewee": ("Contopus", "Tyrannidae"),
    "finch": (None, "Fringillidae"),
    "thrush": (None, "Turdidae"),
    "nuthatch": ("Sitta", "Sittidae"),
    "duck": (None, "Anatidae"),
    "hawk": (None, "Accipitridae"),
    "pigeon/dove": (None, "Columbidae"),
    "pigeon": (None, "Columbidae"),
    "dove": (None, "Columbidae"),
    "bird": (None, None),
}
GROUP_ONLY = re.compile(r"(a )?({})s?".format("|".join(map(re.escape, TAXON_GROUPS))), re.I)

# Spellings and names the alpha-code list won't match on its own (keys are lower-case).
SPECIES_FIXES = {
    "pine sisken": "Pine Siskin",
    "reb-breasted nuthatch": "Red-breasted Nuthatch",
    "ovenbrid": "Ovenbird",
    "tennesse warbler": "Tennessee Warbler",
    "yellow-rupmed warbler": "Yellow-rumped Warbler",
    "golden crown kinglet": "Golden-crowned Kinglet",
    "townsend warbler": "Townsend's Warbler",
    "western palm warbler": "Palm Warbler",
    "mallard duck": "Mallard",
    "coot": "American Coot",
    "pigeon": "Rock Pigeon",
    "rock dove": "Rock Pigeon",
    "feral pigeon": "Rock Pigeon",
    "feral pigeon/rock dove": "Rock Pigeon",
    "rock pigeon, domestic/racing": "Rock Pigeon",
    "chipping": "Chipping Sparrow",
    "clay coloured": "Clay-colored Sparrow",
    "swainsons thrush": "Swainson's Thrush",
    "house wren": "Northern House Wren",
    "wts": "White-throated Sparrow",
    "budgie": "Budgerigar",
    "gold crowned warbler": "Unknown",  # could be Golden-crowned Kinglet or Orange-crowned Warbler
    # Older names still used in BirdMapper, mapped to the current AOS name for Calgary
    "common redpoll": "Redpoll",
    "american magpie": "Black-billed Magpie",
    "warbling vireo": "Western Warbling-Vireo",
    "barn owl": "American Barn Owl",
}
# Scientific names for species the alpha-code list doesn't have (escaped pets)
SCIENTIFIC_FIXES = {
    "Budgerigar": "Melopsittacus undulatus",
}


# ---- species ----

def name_key(s):
    """Loose key for comparing names: lower-case, Canadian spellings, no apostrophes or hyphens."""
    s = s.lower().replace("’", "'").replace("colour", "color").replace("grey", "gray")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"[^a-z/]+", " ", s).strip()


def read_reference(path):
    """Rows of a data/ reference CSV as dicts, skipping the "#" credit lines at the top."""
    with path.open(encoding="utf-8-sig") as f:
        return list(csv.DictReader(line for line in f if not line.startswith("#") and line.strip()))


def load_alpha_codes():
    codes = {r["code"]: r["english_name"] for r in read_reference(ALPHA_CODES)}
    names = {name_key(n): n for n in codes.values()}
    names.update({name_key(k): v for k, v in SPECIES_FIXES.items()})
    return codes, names


class Taxonomy:
    """Genus and family for a find: genus from the alpha-code list's scientific names, family from IOC."""

    def __init__(self):
        self.scientific = {r["english_name"]: r["scientific_name"] for r in read_reference(ALPHA_CODES)}
        self.scientific.update(SCIENTIFIC_FIXES)
        rows = read_reference(IOC_FAMILIES)
        self.families = {r["genus"]: r["family"] for r in rows}
        self.common = {r["family"]: r["family_english"] for r in rows}
        self.common.update(dict([BAT_FAMILY, *BAT_FAMILIES.values()]))
        self.missing = Counter()

    def of(self, species, animal, raw, bat_genus=None):
        """{"genus", "family", "family_common"}; raw is the text the species came from (for "Sparrow sp.")."""
        if animal == "Bat":
            genus, family = bat_genus, BAT_FAMILIES.get(bat_genus, BAT_FAMILY)[0]
        elif species != "Unknown":
            sci = self.scientific.get(species, "")
            genus = sci.split()[0] if sci else None
            family = self.families.get(genus)
            if family is None:
                self.missing[f"{species} ({sci or 'no scientific name'})"] += 1
        else:
            genus, family = group_taxa(raw)
        return {"genus": genus, "family": family, "family_common": self.common.get(family)}


def group_taxa(raw):
    """(genus, family) for a group-only ID ("Sparrow sp.", "Unidentified Empidonax"), else (None, None)."""
    s = HEDGES.sub(" ", clean_species(raw)).strip(" .,;-")
    m = GROUP_ONLY.fullmatch(re.sub(r"\s+", " ", s))
    return TAXON_GROUPS[m[2].lower()] if m else (None, None)


def clean_species(raw):
    s = raw.replace("’", "'").strip()
    s = re.sub(r"[<>]", " ", s)                                        # public text is shown on the site; no markup
    s = re.sub(r"\([^)]*\)", " ", s)                                   # "(confirmed)", "(yellow-shafted)"
    s = re.split(r"\.\s|\s-\s", s)[0]                                  # "X. ID confirmed on iNaturalist", "AMRO - wings"
    s = re.sub(r"[.,\-]?\s*\b(not |un)?(confirmed|confimed|conf)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\b(juvenile|adult|male|female)\b", " ", s, flags=re.I)
    s = re.sub(r"\s*,\s*(yellow[- ]shafted|male|female)\s*$", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip(" .,;-")


def normalize_species(raw, codes, names):
    """Return (species, matched). Unmatched names are kept as cleaned text and reported."""
    s = clean_species(raw)
    if HEDGES.search(s):
        return "Unknown", True
    # Try the whole name, then the part before a comma ("Budgie, domestic/pet", "gull, scavenged")
    for part in dict.fromkeys([s, s.split(",")[0].strip()]):
        if GROUP_ONLY.fullmatch(part):
            return "Unknown", True
        if re.fullmatch(r"[A-Za-z]{4}", part) and part.upper() in codes:
            return codes[part.upper()], True
        if name_key(part) in names:
            return names[name_key(part)], True
    return s[:1].upper() + s[1:], False


# ---- Global Bird Collision Mapper (birdmapper.org) ----

def fetch_gbcm(bbox=CALGARY_BBOX, group=None):
    """All GBCM reports inside bbox (lon/lat), paging past the service's 2000-record limit."""
    where = "1=1" if not group else "groupname = '{}'".format(group.replace("'", "''"))
    records, offset = [], 0
    while True:
        params = urllib.parse.urlencode({
            "where": where, "geometry": bbox, "geometryType": "esriGeometryEnvelope", "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects", "outFields": GBCM_FIELDS, "returnGeometry": "false",
            "orderByFields": "objectid", "resultOffset": offset, "resultRecordCount": 2000, "f": "json",
        })
        with urllib.request.urlopen(f"{GBCM_LAYER}/query?{params}", timeout=120) as resp:
            page = json.loads(resp.read().decode("utf-8"))
        if "error" in page:
            sys.exit(f"BirdMapper query failed: {page['error']}")
        batch = [f["attributes"] for f in page.get("features", [])]
        records += batch
        if not batch or not page.get("exceededTransferLimit"):
            return records
        offset += len(batch)


def load_bat_species():
    """[(pattern, species, genus, alone)] from NABat's species codes, most specific first.

    alone: the match is enough on its own to call the report a bat (scientific names, and NABat codes that aren't
    also bird codes, e.g. not CORA = Common Raven). Common names and BAT_ALIASES need "bat"/"myotis" in the report too.
    """
    bird_codes = {r["code"] for r in read_reference(ALPHA_CODES)}
    by_code, names = {}, []
    for r in read_reference(NABAT_CODES):
        common, scientific, code = r["Common name"], r["Scientific name"], r["Code"]
        common = re.sub(r"\s+", " ", common.split(";")[-1]).strip()  # "ʻōpeʻapeʻa; Hawaiian hoary bat"
        scientific = re.sub(r"\s+", " ", scientific).strip()
        code = code.strip()
        by_code[code] = (" ".join(w[:1].upper() + w[1:] for w in common.split()),  # "Silver-haired Bat"
                         scientific.split()[0])
        # "silver-haired bat" also matches "silver haired bat" and "silver-haired"; one-word names ("gray bat",
        # "evening bat") keep "bat" so they don't match ordinary words
        words = re.split(r"[-\s]+", common.lower())
        if words[-1] == "bat" and (len(words) > 2 or "-" in common):
            words = words[:-1]
        sep = r"[-\s]?"
        names.append((len(common), sep.join(map(re.escape, words)) + rf"(?:{sep}bat)?", code, False))
        names.append((len(scientific), re.escape(scientific), code, True))
    patterns = [(re.compile(rf"\b{p}\b", re.I), *by_code[code], alone)
                for _, p, code, alone in sorted(names, key=lambda t: -t[0])]
    patterns += [(re.compile(rf"\b{p}", re.I), *by_code[code], False) for p, code in BAT_ALIASES.items()]
    patterns += [(re.compile(rf"\b{code}\b"), *species, code not in bird_codes)  # codes only in capitals
                 for code, species in by_code.items()]
    return patterns


BAT_SPECIES = load_bat_species()


def gbcm_bat(rec):
    """(species, genus) if this report is a bat, else None; ("Unknown", None) when the bat isn't named.

    GBCM has no bat species, so teams log bats as "No Birds Found"/"Unknown" and describe them in the notes.
    """
    text = f"{rec.get('species') or ''} {rec.get('notes') or ''}"
    text = BAT_NOT_A_FIND.sub("", text)
    says_bat = BAT_MENTION.search(text) or BAT_GENUS_ONLY.search(text)
    for pattern, species, genus, alone in BAT_SPECIES:
        if pattern.search(text) and (alone or says_bat):
            return species, genus
    if says_bat:
        return "Unknown", "Myotis" if BAT_GENUS_ONLY.search(text) else None
    return None


def gbcm_bat_status(rec):
    if rec.get("status") in ("Dead", "Alive"):
        return "Deceased" if rec["status"] == "Dead" else "Alive"
    notes = (rec.get("notes") or "").lower()
    if re.search(r"deceased|\bdead\b|crushed|scavenged", notes):
        return "Deceased"
    if re.search(r"\blive\b|alive|rescued|cwrs|injured|grounded|released|treated", notes):
        return "Alive"
    return None


def gbcm_source(rec):
    """CUSRT, Public or Other Wildlife Organization, per the rules above WILDLIFE_ORG_REPORTS."""
    if WILDLIFE_ORG_REPORTS.get(rec.get("objectid")):
        return "Other Wildlife Organization"
    if rec.get("objectid") in SOURCE_OVERRIDES:
        return SOURCE_OVERRIDES[rec["objectid"]]
    notes = rec.get("notes") or ""
    if rec.get("groupname") != CUSRT_GROUP and not TEAM_FIND.search(notes):
        return "Public"
    if FROM_INATURALIST.search(notes) or PUBLIC_SUBMISSION.search(notes):
        return "Public"
    return "CUSRT"


def possible_wildlife_org(rec):
    """What makes this look like a wildlife organization's find, if it hasn't been checked yet (else None)."""
    if rec.get("objectid") in WILDLIFE_ORG_REPORTS:
        return None
    if rec.get("groupname") not in (CUSRT_GROUP, None):
        return f"BirdMapper group {rec['groupname']!r}"
    m = WILDLIFE_ORG_MENTION.search(rec.get("notes") or "")
    return f"notes name {m[4].strip()!r}" if m else None


def gbcm_outcome_from_notes(rec):
    """(Deceased/Alive/None, reason) for a bird report whose status isn't Dead/Alive. See the rules above OUTCOME_OVERRIDES."""
    notes = rec.get("notes") or ""
    if rec.get("objectid") in OUTCOME_OVERRIDES:
        outcome = OUTCOME_OVERRIDES[rec["objectid"]]
        return outcome, "CUSRT decision" if outcome else "left out by CUSRT decision"
    if NO_FIND_PATROL.search(notes):
        return None, "patrol with no find"
    if GLASS_IMPACT.search(notes):
        return None, "impact on glass"
    if rec.get("scavenged") or DEAD_IN_NOTES.search(notes):
        return "Deceased", "dead/scavenged/remains in notes"
    if ALIVE_IN_NOTES.search(notes):
        return "Alive", "last seen alive in notes"
    return None, "no dead/alive in notes"


def normalize_gbcm(records):
    codes, names = load_alpha_codes()
    taxonomy = Taxonomy()
    out, report = [], {"skipped": Counter(), "date from": Counter(), "unmatched species": Counter(), "bats from notes": 0,
                       "outcome from notes": Counter(), "unresolved": [], "wildlife org to check": []}
    for rec in records:
        raw = (rec.get("species") or "").strip()
        if raw.lower() in ("", "unknown", "none", "true", "false"):
            raw = (rec.get("alpha_code") or "").strip()
        species, matched = normalize_species(raw, codes, names)
        is_bird_name = matched and species != "Unknown"
        bat = None if is_bird_name else gbcm_bat(rec)
        bat_genus = None

        if bat:
            animal, (species, bat_genus) = "Bat", bat
            status = gbcm_bat_status(rec)
            if status is None:
                report["skipped"]["bat with no dead/alive in notes"] += 1
                report["unresolved"].append(rec)
                continue
            counts = {status: 1}
            report["bats from notes"] += rec.get("status") not in ("Dead", "Alive")
        else:
            animal = "Bird"
            dead, alive, status = rec.get("count_dead") or 0, rec.get("count_alive") or 0, rec.get("status")
            # One row per animal: "Multiple" reports (and some Dead/Alive ones) carry counts
            if status == "Dead":
                counts = {"Deceased": max(dead, 1), "Alive": alive}
            elif status == "Alive":
                counts = {"Alive": max(alive, 1), "Deceased": dead}
            elif status == "Multiple" and dead + alive:
                counts = {"Deceased": dead, "Alive": alive}
            else:
                outcome, reason = gbcm_outcome_from_notes(rec)
                if outcome is None:
                    report["skipped"][reason] += 1
                    if reason == "no dead/alive in notes":
                        report["unresolved"].append(rec)
                    continue
                counts = {outcome: max(rec.get("count_unknown") or 0, 1)}
                species, matched, raw = "Unknown", True, ""
                report["outcome from notes"][f"{outcome} ({reason})"] += 1
            if not matched:
                report["unmatched species"][raw] += 1

        if not rec.get("dateutc"):
            report["skipped"]["no date"] += 1
            continue
        found_on = datetime.fromtimestamp(rec["dateutc"] / 1000, CALGARY_TZ).date().isoformat()
        source = gbcm_source(rec)
        if why := possible_wildlife_org(rec):
            report["wildlife org to check"].append((rec["objectid"], found_on, why))
        taxa = taxonomy.of(species, animal, raw, bat_genus)
        org = {"organization": WILDLIFE_ORG_REPORTS[rec["objectid"]]} if source == "Other Wildlife Organization" else {}
        report["date from"]["dateutc (Calgary time)"] += 1
        for st, n in counts.items():
            out += [{"date": found_on, "animal": animal, "status": st, "species": species, **taxa, "source": source,
                     **org, "report": rec["objectid"]}] * n
    out.sort(key=lambda r: (r["date"], r["species"]))
    report["reports"] = len(records)
    report["no family"] = taxonomy.missing
    return out, report


def print_report(finds, report):
    if "reports" in report:
        print(f"{report['reports']} BirdMapper reports in the area; {report['bats from notes']} bats identified from notes")
    print(f"{len(finds)} finds  ({Counter(f['status'] for f in finds)}, {Counter(f['animal'] for f in finds)})")
    if report.get("outcome from notes"):
        print("Outcome read from notes or set by a CUSRT decision:", dict(report["outcome from notes"]))
    print("Skipped:", dict(report["skipped"]))
    for rec in report.get("unresolved", []):
        # No notes: they can name volunteers, and the hourly job's log is public if the repo is. Look the report up
        # in BirdMapper by its objectid.
        found_on = datetime.fromtimestamp(rec["dateutc"] / 1000, CALGARY_TZ).date() if rec.get("dateutc") else "no date"
        print(f"  left out, needs a decision (add to OUTCOME_OVERRIDES): objectid {rec['objectid']} ({found_on}, "
              f"BirdMapper status {rec.get('status')!r})")
    for objectid, found_on, why in report.get("wildlife org to check", []):
        print(f"  possible wildlife organization find, needs a CUSRT check (add to WILDLIFE_ORG_REPORTS): "
              f"objectid {objectid} ({found_on}): {why}")
    print("Date taken from:", dict(report["date from"]))
    if report["unmatched species"]:
        print("Species not in the alpha-code list (kept as written; add to SPECIES_FIXES if needed):")
        for s, n in report["unmatched species"].most_common():
            print(f"  {n:3}  {s!r}")
    if report["no family"]:
        print("Species with no IOC family (add to SCIENTIFIC_FIXES, or rerun make_ioc_families.py):")
        for s, n in report["no family"].most_common():
            print(f"  {n:3}  {s}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--group", help='only BirdMapper reports from this team, e.g. "Calgary Urban Species Response Team"')
    parser.add_argument("--bbox", default=CALGARY_BBOX, help=f"lon/lat area to pull (default Calgary: {CALGARY_BBOX})")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    finds, report = normalize_gbcm(fetch_gbcm(args.bbox, args.group))
    if not finds:
        sys.exit("No finds after normalization; refusing to overwrite the existing data.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(finds, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print_report(finds, report)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
