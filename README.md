# Calgary Bird & Bat Collision Dashboard

Static dashboard of bird window collisions and bat rescues in Calgary. (Bats are called rescues, not
collisions: they're found grounded and the cause isn't always a collision.)

```
data/collisions.json            data the dashboard loads (built from BirdMapper)
site/index.html                 the dashboard (plain HTML + d3 v7, no build step)
scripts/build_data.py           BirdMapper (default) or the CUSRT sheet CSV -> data/collisions.json
scripts/alpha_codes.csv         4-letter bird codes -> common names (from the IBP PDF)
scripts/explore_birdmapper.py   probes a BirdMapper / GBCM FeatureServer
.github/workflows/refresh.yml   hourly: runs build_data.py and commits the result
```

## View it locally
The page fetches `../data/collisions.json`, so it has to be served rather than opened as a file:

```
python -m http.server
```
Then open http://localhost:8000/site/

## Refresh the data
```
python scripts/build_data.py                                                  # all Calgary reports
python scripts/build_data.py --group "Calgary Urban Species Response Team"    # only CUSRT's reports
```
Data comes from the Global Bird Collision Mapper's public layer, "Bird Collision Data - public" (FLAP
Canada), queried for the Calgary area (`--bbox`, default `-114.32,50.84,-113.86,51.22`). No account or
key is needed. Only `date`, `animal`, `status`, `species`, `source` ("CUSRT" when the report is
in the Calgary Urban Species Response Team group and a team member handled the animal or found it on a survey;
"Public" otherwise, including CUSRT-entered reports that were only documented: iNaturalist, public forms,
security/staff reports with no CUSRT handling. See `gbcm_source` in `build_data.py`) and `report` (the BirdMapper objectid,
shown in the species chart's click-through table) are written out; usernames, notes and
locations never reach the site. The script prints a report of anything it skipped or couldn't match.

How each field is read:
- **date**: the report's `dateutc` timestamp, converted to Calgary time.
- **status**: `Dead` / `Alive` as reported. "Multiple" reports use their dead/alive counts, and any report
  with counts above 1 becomes one row per animal. Reports with any other status ("Unknown", "None", "No
  Birds Found", "Multiple" with no counts) are read from their notes, species "Unknown", using CUSRT's rules:
  patrols with no find and impacts left on glass (imprints, marks, feathers stuck to windows) are left out;
  scavenged, crushed, feather piles, partial remains or a bird "left" for the team count as Dead; otherwise
  the status is what was seen last (flew off, rescued → Alive). Reports the rules can't settle are left out
  and listed in the run report; CUSRT's one-off decisions go in `OUTCOME_OVERRIDES` in `build_data.py`.
- **species**: the species field, or the 4-letter `alpha_code` when that's empty, mapped to the current
  AOS common name via `scripts/alpha_codes.csv`. Hedged or group-only IDs ("Sparrow sp.") become
  "Unknown". Older names and typos are fixed in `SPECIES_FIXES` in `build_data.py`.
- **animal**: GBCM has no bat species, so bats are logged as "No Birds Found"/"Unknown" with the bat
  described in the notes. Those reports become Bat finds, with species (Silver-haired, Big Brown, …) and
  dead/alive read from the notes.

The older CUSRT sheet path still works with `--csv <file>`; its rules are documented in `build_data.py`.

## Hourly refresh (GitHub)
`.github/workflows/refresh.yml` runs `build_data.py` every hour and commits `data/collisions.json` if it
changed. No secrets are needed.

## Style
Styled to match calgaryurbanspecies.ca: deep teal (`#035772`) banner, off-white page, Poppins, lime
(`#e0fd2c`) highlights, pill buttons. Light mode only, like the main site.

The four category colours (`--dead-bird`, `--live-bird`, `--dead-bat`, `--live-bat`) are set once at the
top of the `<style>` block in `site/index.html`, next to `--species-bird` / `--species-bat` for the species
chart bars (dark and light teal). The month chart uses the site teal. Every chart has a "Show as table" view.
