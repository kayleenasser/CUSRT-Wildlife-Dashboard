"""Build data/collisions.json for the dashboard.

By default pulls every report inside Calgary from the Global Bird Collision Mapper's public layer
("Bird Collision Data - public", run by FLAP Canada). With --csv it reads the CUSRT data sheet instead.
Either way each find is normalized to the data contract in README.md and written as a flat JSON array:

    [{"date": "2025-09-14", "animal": "Bird", "status": "Dead", "species": "Dark-eyed Junco", "source": "CUSRT"}, ...]

`source` is "CUSRT" for the team's reports and "Public" for everything else. Only those five fields are
written, so names, emails, notes and GPS never reach the site.

Usage:
    python scripts/build_data.py                                   # BirdMapper, all Calgary reports
    python scripts/build_data.py --group "Calgary Urban Species Response Team"
    python scripts/build_data.py --csv "CUSRT Data Sheet (Responses) - Form Responses 1.csv"

Standard library only.
"""

import argparse
import csv
import io
import json
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "collisions.json"
ALPHA_CODES = Path(__file__).resolve().parent / "alpha_codes.csv"

GBCM_LAYER = ("https://services3.arcgis.com/Fl8N7M7DU9xVkwl3/arcgis/rest/services/"
              "Bird_Collision_Data_Map_view/FeatureServer/0")
GBCM_FIELDS = ("objectid,dateutc,status,species,alpha_code,count_dead,count_alive,count_unknown,scavenged,notes,"
               "groupname,third_party_involvement")
CALGARY_BBOX = "-114.32,50.84,-113.86,51.22"
CALGARY_TZ = ZoneInfo("America/Edmonton")
CUSRT_GROUP = "Calgary Urban Species Response Team"  # GBCM groupname; everything else counts as "Public"
# A report in the CUSRT group counts as "CUSRT" only if a team member handled the animal or found it on a
# survey. Reports a CUSRT member only documented, with no intervention, count as "Public" (CUSRT, Oct 2026):
#  - entered from iNaturalist ("Reported to ... project on iNaturalist", "documentation from iNat")
#  - public window strike form / online form / Messenger / member of the public / friend
#  - documented by building security and passed on later
#  - staff reported the strike to the shift but nobody from CUSRT handled the bird (left for magpies, ...)
FROM_INATURALIST = re.compile(r"\b(reported|documentation)\b[^.]{0,80}\binat(uralist)?\b", re.I)
PUBLIC_SUBMISSION = re.compile(r"public (window strike )?(form|submission)|online strike form|via messenger|"
                               r"by (a )?member of (the )?public|reported by friend", re.I)
SECURITY_DOCUMENTED = re.compile(r"documented by security|reported to (cusrt|our team) (on|by security)", re.I)
STAFF_REPORTED = re.compile(r"reported (\d+ )?strikes? to cusrt|reported to cusrt (dawn|morning) shift|"
                            r"reported finding .{0,40} to cusrt", re.I)
CUSRT_HANDLED = re.compile(r"handed|\breceived\b|retrieved|picked up|\bto cwrs\b|\brescued\b|we intervened|for exam|"
                           r"(?<!not )collect", re.I)
SOURCE_OVERRIDES = {
    1018375: "Public",  # 2021-09-20: window washer saw a magpie take the bird, reported to a volunteer
    1176600: "Public",  # 2026-08-19: security guard's bird, gone when the team checked
    1168037: "Public",  # 2025-09-04: staff reported a grounded bat and called CWRS; bat couldn't be found
}

BAT_MENTION = re.compile(r"\bbats?\b", re.I)
# Bat mentions that aren't a bat find ("Calgary Birds and Bats Window Strike project", "out looking for bats")
BAT_NOT_A_FIND = re.compile(r"birds? and bats?|looking for bats", re.I)
# Reports with no Dead/Alive status are read from their notes, using CUSRT's rules (Oct 2026):
#  - patrols that found nothing and impacts left on glass (imprints, marks, feathers stuck to windows) are left out
#  - scavenged, crushed, feather piles, partial remains, or a bird "left" for the team: Dead
#  - otherwise the status is what was seen last (flew off, rescued): Alive
#  - anything else is left out and listed in the run report
NO_FIND_PATROL = re.compile(r"\bno finds?\b|\bzero finds?\b", re.I)
GLASS_IMPACT = re.compile(r"imprint|images? of birds?|impact marks?|powder mark|wing marks|stuck to the window|"
                          r"window strike with feathers", re.I)
DEAD_IN_NOTES = re.compile(r"scaveng|deceased|\bdead\b|crushed|feather pile|partial", re.I)
ALIVE_IN_NOTES = re.compile(r"\bflew\b|took off|\balive\b|rehab|rescued", re.I)
# One-off decisions by CUSRT for reports the rules can't settle, by GBCM objectid (None = leave out).
# Only used for reports without a Dead/Alive status: when BirdMapper says Dead or Alive, that's what counts.
OUTCOME_OVERRIDES = {
    1172536: "Alive",  # 2026-04-20: strike witnessed, no recovery flight seen, bird not found
    1163331: None,     # 2025-06-06: "Heard hit." only
    1148654: None,     # 2024-04-15: status Unknown, no notes
}
BAT_SPECIES = [
    (r"silver[- ]?hair", "Silver-haired Bat"),
    (r"big brown", "Big Brown Bat"),
    (r"hoary", "Hoary Bat"),
    (r"little brown|myotis", "Little Brown Myotis"),
    (r"red bat", "Eastern Red Bat"),
]

# Sheet headers are long form questions; match each column on how its header starts.
COLUMNS = {
    "find_id": "id of find",
    "date": "date",
    "found": "what did you find",
    "species": "species",
    "condition": "what condition was the animal in",
}

# Anything with these words is an uncertain ID and counts as "Unknown".
HEDGES = re.compile(r"\b(unknown|unknow|unkn|ukno|unk|unsure|unidentified|possible|possibly|poss|probable|probably|likely|"
                    r"maybe|suspected|mix|tbd|sp|spp|species|some kind|either|or|n/?a|none)\b|\?|^$", re.I)
# A group name with no species ("Sparrow", "A sparrow", "Woodpecker") also counts as "Unknown".
GROUP_ONLY = re.compile(r"(a )?(sparrow|warbler|woodpecker|gull|sapsucker|spizella|flycatcher|finch|thrush|"
                        r"nuthatch|duck|hawk|bird)s?", re.I)
# Finds that are neither birds nor bats are dropped.
NOT_BIRD_OR_BAT = re.compile(r"non-bird|porcupine|\bhare\b", re.I)

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
    "silver-haired": "Silver-haired Bat",
    "silver-haired bat": "Silver-haired Bat",
    "silver hair bat": "Silver-haired Bat",
    "big brown bat": "Big Brown Bat",
    "hoary bat": "Hoary Bat",
    "little brown bat": "Little Brown Myotis",
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


def read_csv(source):
    if re.match(r"https?://", source):
        with urllib.request.urlopen(source, timeout=60) as resp:
            text = resp.read().decode("utf-8-sig")
    else:
        text = Path(source).read_text(encoding="utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def resolve_columns(headers):
    resolved = {}
    for field, prefix in COLUMNS.items():
        match = next((h for h in headers if h.strip().lower().startswith(prefix)), None)
        if match is None:
            sys.exit(f"No column starting with {prefix!r}. Sheet headers: {headers}")
        resolved[field] = match
    return resolved


# ---- species ----

def name_key(s):
    """Loose key for comparing names: lower-case, Canadian spellings, no apostrophes or hyphens."""
    s = s.lower().replace("’", "'").replace("colour", "color").replace("grey", "gray")
    s = re.sub(r"'s\b", "", s)
    return re.sub(r"[^a-z/]+", " ", s).strip()


def load_alpha_codes():
    with ALPHA_CODES.open(encoding="utf-8") as f:
        codes = {r["code"]: r["english_name"] for r in csv.DictReader(f)}
    names = {name_key(n): n for n in codes.values()}
    names.update({name_key(k): v for k, v in SPECIES_FIXES.items()})
    return codes, names


def clean_species(raw):
    s = raw.replace("’", "'").strip()
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


# ---- date, condition, animal ----

def parse_find_date(find_id, fallback):
    """Date from the find ID (2023-09-15-B01-01); falls back to the Date column (9/15/2023)."""
    m = re.match(r"\s*(\d{2,4})[-.]?(\d{1,2})[-.]?(\d{1,2})(?:\D|$)", find_id or "")
    if m:
        y, mo, d = (int(g) for g in m.groups())
        y += 2000 if y < 100 else 0
        try:
            return date(y, mo, d).isoformat(), "id"
        except ValueError:
            pass
    m = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*$", fallback or "")
    if m:
        mo, d, y = (int(g) for g in m.groups())
        try:
            return date(y, mo, d).isoformat(), "date column"
        except ValueError:
            pass
    return None, None


def normalize_status(condition, found):
    """Dead or Alive from the condition column, or from "What did you find" when condition is blank."""
    c = condition.lower()
    if "deceased" in c or "died" in c:
        return "Dead"
    if "alive" in c or "escaped" in c:
        return "Alive"
    if not c.strip():
        f = found.lower()
        if "deceased" in f:
            return "Dead"
        if "alive" in f:
            return "Alive"
    return None


def normalize_animal(found, species, is_bird_name):
    """A named species decides; for "Unknown" fall back to the "What did you find" box."""
    if "bat" in species.lower().split() or species.endswith("Myotis"):
        return "Bat"
    if is_bird_name:
        return "Bird"
    parts = {p.strip().lower() for p in found.split(",")}
    return "Bat" if "bat" in parts and "bird" not in parts else "Bird"


def normalize(rows):
    if not rows:
        return [], {}
    cols = resolve_columns(list(rows[0].keys()))
    codes, names = load_alpha_codes()
    out, report = [], {"skipped": Counter(), "date from": Counter(), "id/date mismatch": [], "unmatched species": Counter()}
    for row in rows:
        get = lambda k: (row.get(cols[k]) or "").strip()
        raw_species = get("species")
        if NOT_BIRD_OR_BAT.search(raw_species):
            report["skipped"]["not a bird or bat"] += 1
            continue
        status = normalize_status(get("condition"), get("found"))
        if status is None:
            report["skipped"]["no dead/alive condition"] += 1
            continue
        found_on, source = parse_find_date(get("find_id"), get("date"))
        if found_on is None:
            report["skipped"]["no readable date"] += 1
            continue
        sheet_date, _ = parse_find_date("", get("date"))
        if source == "id" and sheet_date and sheet_date != found_on:
            if sheet_date[:4] != found_on[:4]:
                # Wrong year in the ID ("2014-05-21-..." entered 2024); the Date column matches the form timestamp
                found_on, source = sheet_date, "date column (year typo in ID)"
            else:
                report["id/date mismatch"].append((get("find_id"), get("date")))
        report["date from"][source] += 1
        species, matched = normalize_species(raw_species, codes, names)
        if not matched:
            report["unmatched species"][raw_species] += 1
        out.append({
            "date": found_on,
            "animal": normalize_animal(get("found"), species, matched and species != "Unknown"),
            "status": status,
            "species": species,
            "source": "CUSRT",
        })
    out.sort(key=lambda r: (r["date"], r["species"]))
    return out, report


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


def gbcm_bat(rec):
    """Bat species name if this report is a bat, else None.

    GBCM has no bat species, so teams log bats as "No Birds Found"/"Unknown" and describe them in the notes.
    """
    text = f"{rec.get('species') or ''} {rec.get('notes') or ''}"
    text = BAT_NOT_A_FIND.sub("", text)
    if not BAT_MENTION.search(text):
        return None
    for pattern, name in BAT_SPECIES:
        if re.search(pattern, text, re.I):
            return name
    return "Unknown"


def gbcm_bat_status(rec):
    if rec.get("status") in ("Dead", "Alive"):
        return rec["status"]
    notes = (rec.get("notes") or "").lower()
    if re.search(r"deceased|\bdead\b|crushed|scavenged", notes):
        return "Dead"
    if re.search(r"\blive\b|alive|rescued|cwrs|injured|grounded|released|treated", notes):
        return "Alive"
    return None


def gbcm_source(rec):
    """CUSRT or Public, per the rules above SOURCE_OVERRIDES."""
    if rec.get("groupname") != CUSRT_GROUP:
        return "Public"
    if rec.get("objectid") in SOURCE_OVERRIDES:
        return SOURCE_OVERRIDES[rec["objectid"]]
    notes = rec.get("notes") or ""
    if FROM_INATURALIST.search(notes) or PUBLIC_SUBMISSION.search(notes) or SECURITY_DOCUMENTED.search(notes):
        return "Public"
    handled = rec.get("third_party_involvement") == "Bird was handed to me" or CUSRT_HANDLED.search(notes)
    if STAFF_REPORTED.search(notes) and not handled:
        return "Public"
    return "CUSRT"


def gbcm_outcome_from_notes(rec):
    """(Dead/Alive/None, reason) for a bird report whose status isn't Dead/Alive. See the rules above OUTCOME_OVERRIDES."""
    notes = rec.get("notes") or ""
    if rec.get("objectid") in OUTCOME_OVERRIDES:
        outcome = OUTCOME_OVERRIDES[rec["objectid"]]
        return outcome, "CUSRT decision" if outcome else "left out by CUSRT decision"
    if NO_FIND_PATROL.search(notes):
        return None, "patrol with no find"
    if GLASS_IMPACT.search(notes):
        return None, "impact on glass"
    if rec.get("scavenged") or DEAD_IN_NOTES.search(notes):
        return "Dead", "dead/scavenged/remains in notes"
    if ALIVE_IN_NOTES.search(notes):
        return "Alive", "last seen alive in notes"
    return None, "no dead/alive in notes"


def normalize_gbcm(records):
    codes, names = load_alpha_codes()
    out, report = [], {"skipped": Counter(), "date from": Counter(), "unmatched species": Counter(), "bats from notes": 0,
                       "outcome from notes": Counter(), "unresolved": []}
    for rec in records:
        raw = (rec.get("species") or "").strip()
        if raw.lower() in ("", "unknown", "none", "true", "false"):
            raw = (rec.get("alpha_code") or "").strip()
        species, matched = normalize_species(raw, codes, names)
        is_bird_name = matched and species != "Unknown"
        bat = None if is_bird_name else gbcm_bat(rec)

        if bat:
            animal, species = "Bat", bat
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
                counts = {"Dead": max(dead, 1), "Alive": alive}
            elif status == "Alive":
                counts = {"Alive": max(alive, 1), "Dead": dead}
            elif status == "Multiple" and dead + alive:
                counts = {"Dead": dead, "Alive": alive}
            else:
                outcome, reason = gbcm_outcome_from_notes(rec)
                if outcome is None:
                    report["skipped"][reason] += 1
                    if reason == "no dead/alive in notes":
                        report["unresolved"].append(rec)
                    continue
                counts = {outcome: max(rec.get("count_unknown") or 0, 1)}
                species, matched = "Unknown", True
                report["outcome from notes"][f"{outcome} ({reason})"] += 1
            if not matched:
                report["unmatched species"][raw] += 1

        if not rec.get("dateutc"):
            report["skipped"]["no date"] += 1
            continue
        found_on = datetime.fromtimestamp(rec["dateutc"] / 1000, CALGARY_TZ).date().isoformat()
        source = gbcm_source(rec)
        report["date from"]["dateutc (Calgary time)"] += 1
        for st, n in counts.items():
            out += [{"date": found_on, "animal": animal, "status": st, "species": species, "source": source,
                     "report": rec["objectid"]}] * n
    out.sort(key=lambda r: (r["date"], r["species"]))
    report["reports"] = len(records)
    return out, report


def print_report(finds, report):
    if "reports" in report:
        print(f"{report['reports']} BirdMapper reports in the area; {report['bats from notes']} bats identified from notes")
    print(f"{len(finds)} finds  ({Counter(f['status'] for f in finds)}, {Counter(f['animal'] for f in finds)})")
    if report.get("outcome from notes"):
        print("Outcome read from notes or set by a CUSRT decision:", dict(report["outcome from notes"]))
    print("Skipped:", dict(report["skipped"]))
    for rec in report.get("unresolved", []):
        print(f"  left out, needs a decision (add to OUTCOME_OVERRIDES): objectid {rec['objectid']}: "
              f"{(rec.get('notes') or '(no notes)')[:100]!r}")
    print("Date taken from:", dict(report["date from"]))
    if report.get("id/date mismatch"):
        print(f"{len(report['id/date mismatch'])} find IDs disagree with the Date column (ID was used):")
        for find_id, d in report["id/date mismatch"]:
            print(f"  {find_id!r:32} Date column: {d}")
    if report["unmatched species"]:
        print("Species not in the alpha-code list (kept as written; add to SPECIES_FIXES if needed):")
        for s, n in report["unmatched species"].most_common():
            print(f"  {n:3}  {s!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--csv", help="use the CUSRT sheet (file path or CSV URL) instead of BirdMapper")
    parser.add_argument("--group", help='only BirdMapper reports from this team, e.g. "Calgary Urban Species Response Team"')
    parser.add_argument("--bbox", default=CALGARY_BBOX, help=f"lon/lat area to pull (default Calgary: {CALGARY_BBOX})")
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    if args.csv:
        finds, report = normalize(read_csv(args.csv))
    else:
        finds, report = normalize_gbcm(fetch_gbcm(args.bbox, args.group))
    if not finds:
        sys.exit("No finds after normalization; refusing to overwrite the existing data.")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(finds, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    print_report(finds, report)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
