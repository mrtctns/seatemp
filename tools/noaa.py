# encoding: utf-8
"""Shared access to the NOAA CoastWatch satellite grids.

Deliberately one small module, used by both the one-off database build and the
daily feed job, so the cell the app was pinned to at build time and the cell it
is read from every morning can never drift apart.

Product choice, and why it is not the highest-resolution one available:

  * noaacwBLENDEDsstDNDaily is a GHRSST L4 *analysis* — gap-free, because it
    interpolates through cloud. An L3 product like ACSPO is finer (0.02° vs
    0.05°) but the satellite cannot see through cloud, so a beach goes blank on
    exactly the overcast days people most want to check. "No data today" is not
    a state a consumer app can afford.

  * L4 analyses report foundation temperature — the bulk water a swimmer feels.
    Infrared L3 products report skin temperature, the top few microns, which
    runs cooler and is not the number anyone is asking for.

  * The mirror matters. coastwatch.pfeg.noaa.gov and upwell.pfeg.noaa.gov both
    503 and time out under load; coastwatch.noaa.gov returns the whole Turkish
    coast in about three seconds. Same data, different servers.
"""
import gzip
import json
import math
import time
import urllib.request

BASE = "https://coastwatch.noaa.gov/erddap/griddap"
DATASET = "noaacwBLENDEDsstDNDaily"   # Geo-polar Blended, day+night, L4, ~0.05°
VARIABLE = "analysed_sst"
ATTRIBUTION = "NOAA CoastWatch · NASA JPL"


def fetch_grid(south, north, west, east, when="last", tries=6, timeout=240):
    """One bounding box of sea-surface temperature.

    Returns [(lat, lon, celsius)] for cells that carry a value; land and
    missing cells are dropped. Raises if every attempt fails — the caller
    decides whether a missing region is fatal.
    """
    t = "(last)" if when == "last" else f"({when})"
    url = (f"{BASE}/{DATASET}.json"
           f"?{VARIABLE}%5B{t}%5D"
           f"%5B({south}):({north})%5D"
           f"%5B({west}):({east})%5D")

    last = None
    for attempt in range(tries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "sea-temp/1.0",
                              "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            payload = json.loads(raw)
            table = payload["table"]
            unit = table["columnUnits"][3].lower()
            # The dataset has been served in both Kelvin and Celsius depending
            # on the mirror and the variant. Trust the declared unit rather
            # than a hard-coded assumption; a silent 273-degree error would be
            # obvious, but a silent swap between two Celsius-ish products
            # would not.
            to_c = (lambda v: v - 273.15) if unit.startswith("k") else (lambda v: v)
            cells, stamp = [], None
            for row in table["rows"]:
                if row[3] is None:
                    continue
                stamp = stamp or row[0]
                cells.append((row[1], row[2], to_c(row[3])))
            # An empty result is a legitimate answer, not a failure: a box that
            # is entirely land has no sea cells in it. Treating it as an error
            # meant every inland block over the United States burned the full
            # retry ladder — five minutes each — to re-confirm that Kansas is
            # not the sea.
            return cells, stamp
        except Exception as e:                      # noqa: BLE001
            last = e
            # ERDDAP returns transient 500s, 502s and 503s under load, and the
            # load varies by the hour: the same Turkey request measured 2.5 s
            # one afternoon and 16.9 s the next, with 502s in between while an
            # identical smaller request also failed. It is the server, not the
            # request. Backing off far enough to outlast a busy spell is the
            # documented behaviour; aborting leaves a region blank for the day.
            wait = min(15 * (attempt + 1), 90)
            print(f"    izgara denemesi {attempt + 1}/{tries} basarisiz "
                  f"({str(e)[:60]}), {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"izgara alinamadi: {last}")


# Largest box the endpoint will actually serve, in degrees per side. Measured,
# not guessed: a 6.4° × 16.4° request over Turkey (~47,000 cells) returns in
# three seconds, while a 25° × 10° strip over the United States (~100,000
# cells) comes back as a 502 from the proxy every time. Eight degrees square is
# about 26,000 cells, comfortably inside what works.
MAX_SPAN_DEG = 8.0


def fetch_grid_chunked(south, north, west, east, **kw):
    """Same as fetch_grid, but split into blocks the endpoint can serve.

    Split in both directions rather than in longitude strips alone: the United
    States box is 25 degrees tall, so a full-height strip is over the limit
    however narrow it is.

    A block that fails after its retries is skipped rather than fatal. Losing
    one block loses the beaches in it, which is worth saying out loud — the
    caller prints how many blocks are missing.
    """
    cells, stamp, failed, total = [], None, 0, 0
    lat = south
    while lat < north:
        lat_hi = min(lat + MAX_SPAN_DEG, north)
        lon = west
        while lon < east:
            lon_hi = min(lon + MAX_SPAN_DEG, east)
            total += 1
            try:
                part, part_stamp = fetch_grid(lat, lat_hi, lon, lon_hi, **kw)
                cells.extend(part)
                stamp = stamp or part_stamp
            except Exception as exc:                 # noqa: BLE001
                failed += 1
                print(f"    blok {lat:.0f},{lon:.0f} alinamadi "
                      f"({str(exc)[:40]})", flush=True)
            lon = lon_hi
        lat = lat_hi
    # Only a total network failure is fatal. No cells across blocks that all
    # answered simply means this box holds no sea, which the caller handles.
    if failed == total:
        raise RuntimeError(f"butun bloklar basarisiz ({total} blok)")
    if failed:
        print(f"    {total} blogun {failed} tanesi alinamadi", flush=True)
    return cells, stamp


def km_between(lat1, lon1, lat2, lon2):
    """Equirectangular distance. Accurate to well under a percent at the few-km
    scale this is used at, and far cheaper than haversine over millions of
    beach-to-cell comparisons."""
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(lat2 - lat1, (lon2 - lon1) * k) * 111.195


class SeaGrid:
    """Cells bucketed by whole degree, so finding the nearest sea cell to a
    beach touches a handful of candidates instead of all 80,000."""

    def __init__(self, cells):
        self.buckets = {}
        for lat, lon, c in cells:
            self.buckets.setdefault((int(math.floor(lat)), int(math.floor(lon))),
                                    []).append((lat, lon, c))
        self.count = len(cells)

    def nearest(self, lat, lon, max_km=30.0):
        """Nearest cell carrying a value, or None if the beach is further than
        max_km from any water the satellite reports.

        Widens the search ring by one degree at a time. A beach in a narrow bay
        can legitimately be several cells from the nearest resolved water; one
        beyond max_km is either mis-tagged in OpenStreetMap or on a lake, and is
        dropped rather than pinned to whatever distant sea happens to be closest.
        """
        best = None
        blat, blon = int(math.floor(lat)), int(math.floor(lon))
        for ring in range(0, 4):
            for dlat in range(-ring, ring + 1):
                for dlon in range(-ring, ring + 1):
                    # Only the newly added shell each time round.
                    if ring and max(abs(dlat), abs(dlon)) != ring:
                        continue
                    for clat, clon, c in self.buckets.get((blat + dlat, blon + dlon), ()):
                        d = km_between(lat, lon, clat, clon)
                        if best is None or d < best[0]:
                            best = (d, clat, clon, c)
            # A hit inside the current ring cannot be beaten by a further one
            # once the ring's inner edge is already further away than it.
            if best and best[0] <= ring * 111.195:
                break
        if best is None or best[0] > max_km:
            return None
        return best  # (km, lat, lon, celsius)
