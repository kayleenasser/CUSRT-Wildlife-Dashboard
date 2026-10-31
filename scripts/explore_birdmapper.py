#!/usr/bin/env python3
"""
Explore the Global Bird Collision Mapper (birdmapper.org) Search API.

Goal: find out where the actual collision records live, what fields they have
(species, dead/alive status, date, location), and whether they can be queried
for Calgary, AB.

Standard library only. Usage:

    python scripts/explore_birdmapper.py
    python scripts/explore_birdmapper.py --query "bird collision" --out ./birdmapper_explore
    python scripts/explore_birdmapper.py --bbox -114.32,50.84,-113.86,51.22

Everything it fetches is saved as JSON under --out so you can inspect it.
"""

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://www.birdmapper.org"
SEARCH = "/api/search/v1"
# Rough Calgary bounding box: west, south, east, north (WGS84 lon/lat)
CALGARY_BBOX = "-114.32,50.84,-113.86,51.22"

SPECIES_HINTS = ("species", "common", "bird", "taxon", "name")
STATUS_HINTS = ("status", "condition", "alive", "dead", "fate", "outcome")
DATE_HINTS = ("date", "time", "created")

OUT_DIR = Path("birdmapper_explore")  # set from --out in main()


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def get_json(url, params=None, label=None):
    """GET a URL and return parsed JSON (or None on failure). Saves raw JSON."""
    if params:
        url = f"{url}?{urllib.parse.urlencode(params, safe=',()=*')}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "birdmapper-explorer/0.1", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        print(f"   ! HTTP {e.code} for {url}")
        return None
    except Exception as e:  # network, DNS, timeout...
        print(f"   ! {type(e).__name__}: {e} for {url}")
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        print(f"   ! Response was not JSON ({len(raw)} bytes) for {url}")
        return None

    if label:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)[:120]
        (OUT_DIR / f"{safe}.json").write_text(json.dumps(data, indent=2))
    return data


def heading(text):
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def guess_field(fields, hints):
    """Return field names whose name/alias contains any hint."""
    hits = []
    for f in fields:
        blob = f"{f.get('name', '')} {f.get('alias', '')}".lower()
        if any(h in blob for h in hints):
            hits.append(f["name"])
    return hits


def find_urls(obj, found=None):
    """Recursively collect strings that look like ArcGIS service URLs."""
    if found is None:
        found = set()
    if isinstance(obj, dict):
        for v in obj.values():
            find_urls(v, found)
    elif isinstance(obj, list):
        for v in obj:
            find_urls(v, found)
    elif isinstance(obj, str):
        if re.search(r"/(FeatureServer|MapServer)(/\d+)?/?$", obj, re.I) or "/rest/services/" in obj:
            found.add(obj)
    return found


# --------------------------------------------------------------------------- #
# steps
# --------------------------------------------------------------------------- #
def step_landing(base):
    heading("1. API landing page and collections")
    root = get_json(base + SEARCH, label="01_root")
    if root:
        print(f"   title: {root.get('title')}")
        for link in root.get("links", [])[:10]:
            print(f"   link: {link.get('rel'):<14} {link.get('href')}")

    cols = get_json(base + SEARCH + "/collections", label="02_collections")
    ids = []
    if cols:
        for c in cols.get("collections", []):
            ids.append(c["id"])
            print(f"   collection: id={c['id']!r} title={c.get('title')!r} itemType={c.get('itemType')}")
    if not ids:
        ids = ["dataset"]  # the spec's own example
        print("   (no collections listed; falling back to 'dataset')")
    return ids


def step_search_items(base, collection_ids, query):
    heading(f"2. Searching catalog items for q={query!r}")
    candidates = []  # (collection_id, item_id, title, type, service_urls)
    for cid in collection_ids:
        print(f"\n   -- collection {cid!r}")
        data = get_json(
            f"{base}{SEARCH}/collections/{cid}/items",
            {"q": query, "limit": 25},
            label=f"03_items_{cid}",
        )
        if not data:
            continue
        print(f"   matched: {data.get('numberMatched')}  returned: {data.get('numberReturned')}")
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            title = props.get("title") or props.get("name") or "(untitled)"
            itype = props.get("type")
            urls = sorted(find_urls(feat))
            print(f"   * {title}  [{itype}]  id={feat.get('id')}")
            for u in urls[:3]:
                print(f"       service: {u}")
            candidates.append((cid, feat.get("id"), title, itype, urls))
    return candidates


def step_hub_layer(base, cid):
    """Try the Hub 'geoservice' façade for a collection."""
    heading(f"3a. Hub FeatureServer façade for collection {cid!r}")
    fs = f"{base}{SEARCH}/rest/services/collections/{cid}/geoservice/FeatureServer"
    info = get_json(fs, {"f": "json"}, label=f"04_hub_featureserver_{cid}")
    if info:
        print(f"   layers: {[(l.get('id'), l.get('name')) for l in info.get('layers', [])]}")
    layer = get_json(f"{fs}/0", {"f": "json"}, label=f"05_hub_layer0_{cid}")
    if layer and layer.get("fields"):
        return fs + "/0", layer["fields"]
    print("   (no fields returned; this façade probably describes catalog items, not collisions)")
    return fs + "/0", []


def describe_fields(fields):
    print(f"   {len(fields)} fields:")
    for f in fields:
        print(f"     - {f['name']:<30} {f.get('type', ''):<22} alias={f.get('alias')!r}")
    species_fields = guess_field(fields, SPECIES_HINTS)
    status_fields = guess_field(fields, STATUS_HINTS)
    date_fields = guess_field(fields, DATE_HINTS)
    print(f"\n   likely species fields: {species_fields or 'none found'}")
    print(f"   likely status fields : {status_fields or 'none found'}")
    print(f"   likely date fields   : {date_fields or 'none found'}")
    return species_fields, status_fields, date_fields


def step_query_layer(layer_url, fields, bbox, label):
    heading(f"4. Sampling records from {layer_url}")
    species_fields, status_fields, _ = describe_fields(fields)

    base_params = {
        "f": "json",
        "where": "1=1",
        "outFields": "*",
        "returnGeometry": "true",
        "resultRecordCount": 5,
        "geometry": bbox,
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "outSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }

    print("\n   -- total count (all of world)")
    total = get_json(f"{layer_url}/query", {"f": "json", "where": "1=1", "returnCountOnly": "true"},
                     label=f"06_count_all_{label}")
    if total:
        print(f"   {total}")

    print("\n   -- total count inside bbox (Calgary)")
    cnt = get_json(f"{layer_url}/query", {**base_params, "returnCountOnly": "true"},
                   label=f"07_count_bbox_{label}")
    if cnt:
        print(f"   {cnt}")

    print("\n   -- 5 sample records inside bbox")
    sample = get_json(f"{layer_url}/query", base_params, label=f"08_sample_{label}")
    if sample:
        for ft in sample.get("features", []):
            print("   ", json.dumps(ft.get("attributes", {}), default=str)[:300])

    # distinct values for the status / species fields (what the dashboard filters need)
    for kind, names in (("status", status_fields), ("species", species_fields)):
        for name in names[:2]:
            print(f"\n   -- distinct {kind} values in field {name!r} (Calgary, with counts)")
            stats = get_json(
                f"{layer_url}/query",
                {
                    **{k: v for k, v in base_params.items() if k not in ("resultRecordCount", "outFields")},
                    "groupByFieldsForStatistics": name,
                    "outStatistics": json.dumps([
                        {"statisticType": "count", "onStatisticField": name,
                         "outStatisticFieldName": "n"}
                    ]),
                    "orderByFields": "n DESC",
                    "returnGeometry": "false",
                },
                label=f"09_distinct_{kind}_{name}_{label}",
            )
            if stats and stats.get("features"):
                for ft in stats["features"][:25]:
                    a = ft["attributes"]
                    print(f"      {a.get(name)!s:<40} {a.get('n')}")
            else:
                print("      (no result; field may not support statistics or isn't queryable)")


def step_direct_services(candidates, bbox):
    """Try any ArcGIS FeatureServer URLs discovered in item metadata."""
    heading("3b. Direct ArcGIS service URLs found in item metadata")
    tried = set()
    for _, iid, title, _, urls in candidates:
        for u in urls:
            m = re.match(r"(.*?/FeatureServer)(/\d+)?/?$", u, re.I)
            if not m:
                continue
            layer = m.group(1) + (m.group(2) or "/0")
            if layer in tried:
                continue
            tried.add(layer)
            print(f"\n   Trying {layer}   (from item: {title})")
            info = get_json(layer, {"f": "json"}, label=f"10_direct_{iid}")
            if info and info.get("fields"):
                step_query_layer(layer, info["fields"], bbox, str(iid))
            else:
                print("   (no fields; might need a token or a different layer index)")
    if not tried:
        print("   none found")


# --------------------------------------------------------------------------- #
def main():
    global OUT_DIR
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default=BASE, help="site root (default %(default)s)")
    ap.add_argument("--query", default="collision", help="catalog search text (default %(default)s)")
    ap.add_argument("--bbox", default=CALGARY_BBOX, help="west,south,east,north in lon/lat")
    ap.add_argument("--out", default=str(OUT_DIR), help="folder for raw JSON dumps")
    args = ap.parse_args()
    OUT_DIR = Path(args.out)

    cols = step_landing(args.base)
    candidates = step_search_items(args.base, cols, args.query)

    # Try the Hub façade on each collection; only query if it exposes real fields
    for cid in cols:
        layer_url, fields = step_hub_layer(args.base, cid)
        if fields:
            step_query_layer(layer_url, fields, args.bbox, f"hub_{cid}")

    step_direct_services(candidates, args.bbox)

    heading("Done")
    print(f"Raw responses saved in: {OUT_DIR.resolve()}")
    print("Next: look at 03_items_*.json for the item that holds collision records,")
    print("then rerun with --query set to its title if the first pass missed it.")


if __name__ == "__main__":
    main()
