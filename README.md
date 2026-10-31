# CUSRT Wildlife Dashboard

A static dashboard of the birds and bats the Calgary Urban Species Response Team (CUSRT), the public and other
wildlife organizations have reported in Calgary. Birds are mostly window collisions; bats are found grounded,
which isn't always caused by a collision.

The data comes from the [Global Bird Collision Mapper](https://www.birdmapper.org/) (BirdMapper, run by FLAP
Canada). A GitHub Action pulls it every hour, so new reports show up on the dashboard without anyone touching it.

## What's in the repo

```text
site/index.html                 the dashboard (plain HTML + d3 v7, no build step)
site/style.css                  the dashboard's styles
data/collisions.json            the data the dashboard loads, built by build_data.py
scripts/build_data.py           pulls BirdMapper reports and writes data/collisions.json
.github/workflows/refresh.yml   runs build_data.py every hour and commits any changes

data/alpha_codes.csv            4-letter bird codes -> common and scientific names (built by make_alpha_codes.py)
data/ioc_families.csv           bird genus -> family, from the IOC World Bird List (built by make_ioc_families.py)
data/NABat_Species_Codes.csv    bat names, scientific names and 4-letter codes, from NABat
scripts/make_alpha_codes.py     rebuilds alpha_codes.csv from the IBP alpha-code PDF
scripts/make_ioc_families.py    rebuilds ioc_families.csv from the IOC master list
scripts/explore_birdmapper.py   probes BirdMapper's API (used to find the data layer in the first place)
```

## View it locally

The page loads `../data/collisions.json`, so it has to be served, not opened as a file. From the repo root:

```sh
python -m http.server
```

Then open <http://localhost:8000/site/>.

## Refresh the data

```sh
python scripts/build_data.py                                                  # every report in the Calgary area
python scripts/build_data.py --group "Calgary Urban Species Response Team"    # only reports in CUSRT's group
```

No account or API key is needed. The script reads BirdMapper's public layer ("Bird Collision Data - public")
inside a box around Calgary (`--bbox`, default `-114.32,50.84,-113.86,51.22`) and writes one record per animal
to `data/collisions.json`. Only standard-library Python is used.

After each run it prints a run report: how many reports were read, what was skipped and why, and anything that
needs a person to decide (see [When the run report asks for a decision](#when-the-run-report-asks-for-a-decision)).

### Privacy

Only the fields below are written out. Reporters' names and emails, notes and locations never reach the site.

### The fields

Each record looks like this:

```json
{"date": "2025-09-14", "animal": "Bird", "status": "Deceased", "species": "Dark-eyed Junco", "genus": "Junco",
 "family": "Passerellidae", "family_common": "New World Sparrows", "source": "CUSRT", "report": 1160123}
```

- **date**: when the animal was found, in Calgary time (from BirdMapper's `dateutc`).
- **animal**: `Bird` or `Bat`. BirdMapper has no bat species, so bats are logged as "No Birds Found" or
  "Unknown" with the bat described in the notes. The script spots those from the notes and matches the species
  against NABat's list by common name, scientific name or code (e.g. "LANO" for Silver-haired Bat). A bare
  "myotis" gives the genus only.
- **status**: `Deceased` or `Alive` (BirdMapper's "Dead" is shown as "Deceased").
  - A report with counts (e.g. 3 dead, 1 alive) becomes one record per animal.
  - Reports with no dead/alive status ("Unknown", "None", "No Birds Found") are read from their notes, using
    CUSRT's rules:
    - patrols with no find, and impacts left on glass (imprints, marks, feathers stuck to a window), are left out
    - scavenged, crushed, feather piles, partial remains, or a bird "left" for the team: Deceased
    - otherwise, whatever was seen last (flew off, rescued): Alive

    These records are listed with species "Unknown".
- **species**: BirdMapper's species field, or its 4-letter `alpha_code` when that's empty, turned into the
  current AOS common name using `data/alpha_codes.csv`. Uncertain IDs ("possible", "sp.", "?") and group-only
  IDs ("Sparrow") become "Unknown". Typos and older names are fixed in `SPECIES_FIXES` in `build_data.py`.
- **genus / family / family_common**: the genus is the first word of the species' scientific name; the family
  and its English name come from the IOC World Bird List (v15.2). Group-only IDs still get what they tell us,
  through `TAXON_GROUPS` ("Sparrow sp." → Passerellidae; "Unidentified Empidonax" → *Empidonax*, Tyrannidae).
  Bats get their family from `BAT_FAMILIES` (every bat in Alberta is a vesper bat, Vespertilionidae). These
  fields are empty (null) when not even the family is known.
- **source**: who reported the find.
  - `CUSRT`: the report came from the team's survey work. That includes strikes that building staff, cleaners,
    security or passersby reported to a CUSRT shift or volunteer, and team finds entered outside CUSRT's
    BirdMapper group ("CUSRT Dawn shift", or "CMSRT walk" from before the rebrand).
  - `Public`: everything else, including reports CUSRT entered for someone else: finds from iNaturalist and
    submissions to CUSRT's public window strike form.
  - `Other Wildlife Organization`: finds documented by another wildlife organization (Calgary Wildlife, AIWC,
    …). These are listed by hand in `WILDLIFE_ORG_REPORTS` once CUSRT has checked them, and also carry the
    organization's name in an `organization` field.

  The rules are in `gbcm_source` in `build_data.py`.
- **report**: the BirdMapper report number (objectid). It's shown in the dashboard's report pop-ups so a find
  can be looked up on BirdMapper.

### When the run report asks for a decision

Some reports can't be settled by the rules. They're left out (or left as they are) and listed in the run report
by report number until someone decides. Each decision goes in a list near the top of `build_data.py`, with the
date and a short reason:

| The run report says | Add the report number to |
| --- | --- |
| left out, needs a decision | `OUTCOME_OVERRIDES` (`"Deceased"`, `"Alive"` or `None` to leave it out) |
| possible wildlife organization find | `WILDLIFE_ORG_REPORTS` (the organization's name, or `None` if it isn't one) |
| species not in the alpha-code list | `SPECIES_FIXES` (the name as written, in lower case, and the correct one) |
| species with no IOC family | `SCIENTIFIC_FIXES`, or rerun `make_ioc_families.py` with a newer IOC list |

Reports that the rules put in the wrong source group go in `SOURCE_OVERRIDES`.

### Updating the reference lists

- **IOC families**: download the newest master list (.xlsx) from
  <https://www.worldbirdnames.org/new/ioc-lists/master-list-2/> and run
  `python scripts/make_ioc_families.py <file>`.
- **Alpha codes**: when the IBP publishes a new `Alpha_codes_eng.pdf`, put it in `data/` and run
  `python scripts/make_alpha_codes.py data/Alpha_codes_eng.pdf` (needs `pdftotext`, which comes with Git for
  Windows).

## Hourly refresh

`.github/workflows/refresh.yml` runs `build_data.py` every hour (at 17 minutes past) and commits
`data/collisions.json` if it changed. It needs no secrets. It can also be started by hand from the repo's
Actions tab.

## Look and feel

The dashboard matches calgaryurbanspecies.ca: a deep teal (`#035772`) banner, off-white page, Poppins font,
lime (`#e0fd2c`) highlights and pill-shaped buttons. Like the main site, it's light mode only.

All colours are set once at the top of `site/style.css`:

- `--deceased-bird`, `--live-bird`, `--deceased-bat`, `--live-bat`: the four status categories
- `--before-split`, `--after-split`: the two parts of each species bar (dark and lighter teal)
- `--id-known`, `--id-unknown`: the small "identified" donut on the species chart
- `--src-cusrt`, `--src-public`, `--src-wildlife`: the reporting source bar

Every chart has a "Show as table" view, and clicking any part of a chart opens a list of the reports behind it.

The species chart can show all animals, birds or bats, grouped by species, genus or family (`LEVELS` in
`site/index.html`). Each bar is split into records before July 15 and from July 15 on. To move that date,
change `SPLIT_DAY` and `SPLIT_LABELS` in `site/index.html` and the chart's key just above the chart in the same
file.

## Credits

Each reference list in `data/` credits its source in its first lines:

- `alpha_codes.csv`: Peter Pyle and David F. DeSante, The Institute for Bird Populations (alpha codes, 66th AOS
  Supplement, 2025), <https://www.birdpop.org/pages/birdSpeciesCodes.php>
- `ioc_families.csv`: IOC World Bird List (Gill, Donsker & Rasmussen, eds.), <https://www.worldbirdnames.org/>
- `NABat_Species_Codes.csv`: North American Bat Monitoring Program (NABat), <https://www.nabatmonitoring.org/species-codes>
