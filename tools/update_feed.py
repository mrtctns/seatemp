#!/usr/bin/env python3
# encoding: utf-8
"""Build the daily temperature feed the app reads.

Runs on a schedule, not on a phone. The satellite analysis behind it updates
once a day, so a per-user request would fetch an identical number thousands of
times over, from a free public research endpoint that returns 503 under load.
Retry logic belongs in a cron job; a phone at the beach gets a static file off a
CDN.

One file per country, so a user in Turkey never downloads Brazil.

    python3 tools/update_feed.py                 # every country in the catalogue
    python3 tools/update_feed.py TR GR           # just these
"""
import json
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from noaa import ATTRIBUTION, SeaGrid, fetch_grid  # noqa: E402

ROOT = os.path.join(HERE, "..")
CATALOGUE = os.path.join(ROOT, "beaches.json")
FEED = os.path.join(ROOT, "v1")

# Same regions as the database build, so a beach is read from the same water it
# was pinned to. Generous boxes: a coastline clipped at the edge loses beaches
# silently.
REGIONS = [
    ("Akdeniz-dogu", 30.0, 43.0, 25.0, 43.0),
    ("Akdeniz-bati", 30.0, 46.0, -7.0, 25.5),
    ("Karadeniz", 40.0, 48.0, 27.0, 42.5),
    ("Kuzey-Avrupa", 47.0, 62.0, -12.0, 32.0),
    ("Atlantik-Iberya", 34.0, 48.0, -20.0, 0.0),
    ("Kizildeniz-Korfez", 11.0, 31.0, 32.0, 60.0),
    ("Kuzey-Amerika", 14.0, 50.0, -132.0, -60.0),
    ("Guney-Amerika", -35.0, 14.0, -82.0, -33.0),
    ("Guneydogu-Asya", -11.0, 24.0, 92.0, 128.0),
    ("Avustralya", -45.0, -9.0, 110.0, 155.0),
]

# A beach was pinned to a cell at build time; the daily read should land on that
# same cell. Anything past this means the grid moved or the pin is wrong, and no
# reading is better than a reading from somewhere else.
MAX_DRIFT_KM = 12.0


def main():
    with open(CATALOGUE, encoding="utf-8") as f:
        beaches = json.load(f)
    wanted = {c.upper() for c in sys.argv[1:]}
    if wanted:
        beaches = [b for b in beaches if b["cc"] in wanted]
    if not beaches:
        print("katalogda eslesen plaj yok")
        return 1
    print(f"{len(beaches)} plaj, {len({b['cc'] for b in beaches})} ulke\n")

    grids, stamps = [], []
    for name, s, n, w, e in REGIONS:
        if not any(s <= b["qlat"] <= n and w <= b["qlon"] <= e for b in beaches):
            continue
        print(f"izgara: {name} ...", flush=True)
        try:
            cells, stamp = fetch_grid(s, n, w, e)
        except Exception as exc:                     # noqa: BLE001
            # A region that fails leaves its beaches without a reading today.
            # The app shows the cached value and marks it stale rather than
            # showing nothing, so this is degraded, not broken.
            print(f"  ATLANDI: {exc}")
            continue
        print(f"  {len(cells)} deniz hucresi ({stamp[:10]})")
        grids.append(SeaGrid(cells))
        stamps.append(stamp)

    if not grids:
        print("hicbir izgara alinamadi — besleme yazilmadi")
        return 1

    observed = max(stamps)
    by_country, missed = {}, 0
    for b in beaches:
        best = None
        for g in grids:
            hit = g.nearest(b["qlat"], b["qlon"], MAX_DRIFT_KM)
            if hit and (best is None or hit[0] < best[0]):
                best = hit
        if best is None:
            missed += 1
            continue
        by_country.setdefault(b["cc"], {})[b["id"]] = {
            "c": round(best[3], 1),
            "t": observed,
        }

    os.makedirs(FEED, exist_ok=True)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    total_bytes = 0
    for cc, readings in sorted(by_country.items()):
        payload = {"u": now, "s": ATTRIBUTION, "r": readings}
        path = os.path.join(FEED, f"{cc}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        total_bytes += os.path.getsize(path)
        print(f"  {cc}: {len(readings)} okuma  "
              f"({os.path.getsize(path) / 1024:.0f} KB)")

    print(f"\n{sum(len(v) for v in by_country.values())} okuma -> {FEED}")
    print(f"toplam {total_bytes / 1024:.0f} KB, olcum tarihi {observed[:10]}")
    if missed:
        print(f"{missed} plaj icin bugun okuma yok "
              f"(izgara {MAX_DRIFT_KM} km icinde deger vermedi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
