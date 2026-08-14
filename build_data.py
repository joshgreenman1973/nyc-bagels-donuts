#!/usr/bin/env python3
"""Rebuild data.min.json from OpenStreetMap via the Overpass API.

Selects everything inside the New York City boundary that is tagged as a bagel
or doughnut shop, or that simply has "bagel"/"donut"/"doughnut" in its name --
the same net the map has always described in its footer.

A place can qualify as both (plenty of New York counters sell both), in which
case it appears on both lists.

Fails loudly: an Overpass error, an unparseable response, an empty result, or a
count that has collapsed against the committed file all abort before
data.min.json is touched. A silent rewrite to an empty map is worse than a
stale one, because nothing on screen says anything is wrong.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data.min.json")
QUERY_FILE = os.path.join(HERE, "overpass-query.txt")

ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# NYC's OSM relation (3600175905 = relation 175905, the five boroughs).
# Brand and the bare "Dunkin'" name both matter: the chain dropped "Donuts"
# from its signage, and 557 of its New York shops are tagged with the short
# name alone -- a name-only regex for "donut" silently loses most of them.
QUERY = """[out:json][timeout:180];
area(3600175905)->.nyc;
(
  nwr["shop"="bagel"](area.nyc);
  nwr["shop"="doughnut"](area.nyc);
  nwr["cuisine"~"bagel|donut|doughnut",i](area.nyc);
  nwr["name"~"bagel|donut|doughnut|dunkin|krispy kreme",i](area.nyc);
  nwr["brand"~"dunkin|krispy kreme",i](area.nyc);
);
out center tags;
"""

# What the footer claims the chain flag catches. Kept explicit rather than
# guessed from location counts, so the page and the code say the same thing.
CHAINS = re.compile(r"dunkin|krispy\s*kreme", re.I)

MIN_RETAINED_SHARE = 0.70   # OSM churns more than a government feed


def fetch(query):
    last = None
    for endpoint in ENDPOINTS:
        for attempt in range(3):
            try:
                req = urllib.request.Request(
                    endpoint,
                    data=urllib.parse.urlencode({"data": query}).encode(),
                    headers={"User-Agent": "nyc-bagels-donuts/1.0 (github.com/joshgreenman1973)"},
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    payload = json.loads(r.read())
                if "elements" not in payload:
                    raise RuntimeError(f"no elements key: {str(payload)[:200]}")
                return payload
            except Exception as e:                  # noqa: BLE001 - retry anything
                last = e
                wait = 10 * (attempt + 1)
                print(f"  {endpoint} attempt {attempt + 1} failed ({e}); "
                      f"retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
    raise RuntimeError(f"Overpass unavailable: {last}")


def coords(el):
    if "center" in el:
        return el["center"]["lat"], el["center"]["lon"]
    if "lat" in el:
        return el["lat"], el["lon"]
    return None, None


def record(el, tags, lat, lon):
    addr = " ".join(p for p in (tags.get("addr:housenumber"),
                                tags.get("addr:street")) if p)
    name = tags.get("name") or tags.get("name:en") or ""
    brand = tags.get("brand") or ""
    return {
        "n": name,
        "la": round(lat, 5),
        "lo": round(lon, 5),
        "a": addr,
        "c": tags.get("addr:city") or tags.get("addr:suburb") or "",
        "h": tags.get("opening_hours") or "",
        "w": tags.get("website") or tags.get("contact:website") or "",
        "ch": bool(CHAINS.search(name) or CHAINS.search(brand)),
    }


def main():
    query = QUERY
    if os.path.exists(QUERY_FILE):
        query = open(QUERY_FILE).read()

    print("querying Overpass...")
    payload = fetch(query)
    print(f"  {len(payload['elements'])} elements")

    bagels, donuts, seen = [], [], set()
    for el in payload["elements"]:
        tags = el.get("tags", {})
        lat, lon = coords(el)
        if lat is None:
            continue
        key = (el.get("type"), el.get("id"))
        if key in seen:
            continue
        seen.add(key)

        name = (tags.get("name") or "").lower()
        shop = (tags.get("shop") or "").lower()
        cuisine = (tags.get("cuisine") or "").lower()
        hay = f"{name} {cuisine}"

        brand = (tags.get("brand") or "").lower()
        is_bagel = shop == "bagel" or "bagel" in hay
        is_donut = (shop == "doughnut" or "donut" in hay or "doughnut" in hay
                    or CHAINS.search(name) or CHAINS.search(brand))
        if not (is_bagel or is_donut):
            continue

        row = record(el, tags, lat, lon)
        if is_bagel:
            bagels.append(row)
        if is_donut:
            donuts.append(row)

    if not bagels or not donuts:
        raise SystemExit(f"FAILED: empty result (bagels={len(bagels)}, "
                         f"donuts={len(donuts)}) — refusing to overwrite")

    if os.path.exists(OUT):
        old = json.load(open(OUT))
        for key, new in (("bagels", bagels), ("donuts", donuts)):
            was = len(old.get(key, []))
            if was and len(new) < was * MIN_RETAINED_SHARE:
                raise SystemExit(
                    f"FAILED: {key} fell to {len(new)} from {was} "
                    f"(< {MIN_RETAINED_SHARE:.0%}) — looks like a bad Overpass "
                    f"response, not real churn")

    bagels.sort(key=lambda r: (r["n"].lower(), r["la"]))
    donuts.sort(key=lambda r: (r["n"].lower(), r["la"]))

    out = {
        "bagels": bagels,
        "donuts": donuts,
        # Dates the data itself so the footer stops carrying a hand-typed date.
        "generated": time.strftime("%Y-%m-%d", time.gmtime()),
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(out, fh, separators=(",", ":"))
    os.replace(tmp, OUT)
    print(f"wrote data.min.json: {len(bagels)} bagel, {len(donuts)} donut places "
          f"({sum(1 for r in donuts if r['ch'])} chain)")


if __name__ == "__main__":
    main()
