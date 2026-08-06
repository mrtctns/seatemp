#!/usr/bin/env python3
# encoding: utf-8
"""Build the history feed: the last seven days, and the same week a year ago.

    python3 tools/build_history.py              # roll the window forward a day
    python3 tools/build_history.py --full       # refetch both whole windows
    python3 tools/build_history.py TR GR        # just these countries

Published separately from the daily feed, at feed/v1/h/<CC>.json, and fetched by
the app only when someone opens the history view. Folding it into <CC>.json
would have tripled the file every user downloads on every launch — at the beach,
on a bad signal — to carry a premium feature most of them will never open.

**Rolls by default, and that is the difference between minutes and hours.** The
first version always refetched both seven-day windows, on the reasoning that a
day lost to a 502 would otherwise stay missing for a week. It is a real risk,
but the price was a five-hour job to redo six days that had not changed. So the
default now fetches only the days the published file is missing — usually one —
and `--full` exists for the initial build and for repairing gaps.

Rolling keeps the correctness argument by *checking* rather than assuming: any
day already in the file is reused, any day in the window that is absent is
fetched, however old. A day lost to a 502 is retried the next morning instead of
being written off.
"""
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from noaa import ATTRIBUTION, SeaGrid, fetch_range_chunked  # noqa: E402
from update_feed import CATALOGUE, MAX_DRIFT_KM, REGIONS  # noqa: E402
from update_feed import FEED as DAILY_FEED  # noqa: E402

# Beside the daily feed, whichever layout that resolved to. Deriving it rather
# than rebuilding the path is what keeps the two in step when the tools run
# from the published feed repository instead of the app one.
FEED = os.path.join(DAILY_FEED, "h")

DAYS = 7

# The analysis publishes a day or two behind real time, so a window ending today
# would always have empty days on the end. Ending it here keeps the chart full.
LAG_DAYS = 2


def window(end, days=DAYS):
    return [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]


def sample(beaches, days, label):
    """{beach id: {day: celsius}} for one window."""
    start, stop = days[0], days[-1]
    print(f"{label}: {start} - {stop}", flush=True)
    out = {}
    for name, s, n, w, e in REGIONS:
        # Only the beaches this region can possibly answer for.
        #
        # Pinning every beach in the catalogue against every region is what
        # made the first version of this take hours: 28,933 beaches × 7 days ×
        # 10 regions is two million nearest-cell searches per window, and all
        # but a fifteenth of them are a beach in Brazil being measured against
        # a grid of the Black Sea. Regions still overlap at their edges, so a
        # beach can appear in two — which is why the closer hit still wins
        # below.
        mine = [b for b in beaches
                if s <= b["qlat"] <= n and w <= b["qlon"] <= e]
        if not mine:
            continue
        print(f"  izgara: {name} ({len(mine)} plaj) ...", flush=True)
        try:
            by_day = fetch_range_chunked(
                s, n, w, e, start, stop,
                points=[(b["qlat"], b["qlon"]) for b in mine])
        except Exception as exc:                     # noqa: BLE001
            print(f"    ATLANDI: {exc}")
            continue

        for day, cells in by_day.items():
            grid = SeaGrid(cells)
            for b in mine:
                hit = grid.nearest(b["qlat"], b["qlon"], MAX_DRIFT_KM)
                if hit is None:
                    continue
                seen = out.setdefault(b["id"], {})
                # Regions overlap at their edges. Keep whichever cell is
                # physically closer rather than whichever region ran last.
                if day not in seen or hit[0] < seen[day][0]:
                    seen[day] = (hit[0], round(hit[3], 1))
        print(f"    {len(by_day)} gun", flush=True)

    return {bid: {d: v[1] for d, v in days_seen.items()}
            for bid, days_seen in out.items()}


def published(countries):
    """What the last run wrote, as {beach id: {day: celsius}}.

    Both windows are folded into one map keyed by real date, so a day that was
    "last year" in an older file is still recognised if the window has moved
    over it. Reading the file's own `d`/`yd` arrays rather than recomputing the
    dates is what makes that safe.
    """
    known, covered = {}, {}
    for cc in countries:
        covered[cc] = set()
        path = os.path.join(FEED, f"{cc}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, ValueError):
            continue
        for key, dates in (("h", payload.get("d")), ("y", payload.get("yd"))):
            if not dates:
                continue
            covered[cc].update(dates)
            for bid, entry in payload.get("r", {}).items():
                values = entry.get(key)
                if not values:
                    continue
                for day, value in zip(dates, values):
                    if value is not None:
                        known.setdefault(bid, {})[day] = value
    return known, covered


def gather(beaches, days, covered, label, full):
    """Values for one window, fetching only the days not already published.

    A day counts as missing if *any* country is short of it. Deciding globally
    — "some beach somewhere has this day, so skip it" — would leave a country
    whose file was written before the others permanently a day behind, and
    nothing in the output would say so.
    """
    if full:
        return sample(beaches, days, label)
    missing = [d for d in days
               if any(d not in dates for dates in covered.values())]
    if not missing:
        print(f"{label}: {days[0]} - {days[-1]} zaten tam, cekim yok", flush=True)
        return {}
    if len(missing) < len(days):
        print(f"{label}: {len(missing)}/{len(days)} gun eksik "
              f"({', '.join(missing)})", flush=True)
    # One contiguous request covering the gaps. Non-adjacent gaps pull in a day
    # or two that is already known, which costs nothing and is simpler than
    # issuing a request per island.
    return sample(beaches, [missing[0], missing[-1]] if len(missing) > 1
                  else missing, label)


def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    full = "--full" in sys.argv[1:]

    with open(CATALOGUE, encoding="utf-8") as f:
        beaches = json.load(f)
    wanted = {c.upper() for c in argv}
    if wanted:
        beaches = [b for b in beaches if b["cc"] in wanted]
    if not beaches:
        print("katalogda eslesen plaj yok")
        return 1

    end = date.today() - timedelta(days=LAG_DAYS)
    now_days = window(end)
    # 364 rather than 365, so last year's window covers the same weekdays and
    # starts on the same day of the week. A week that slides by one day is the
    # kind of thing nobody notices and nobody can explain later.
    then_days = window(end - timedelta(days=364))

    print(f"{len(beaches)} plaj, {len({b['cc'] for b in beaches})} ulke")
    countries = sorted({b["cc"] for b in beaches})
    known, covered = ({}, {cc: set() for cc in countries}) if full \
        else published(countries)
    print(f"{'tam yeniden uretim' if full else f'{len(known)} plaj yayindan okundu'}\n")

    now = gather(beaches, now_days, covered, "bu hafta", full)
    then = gather(beaches, then_days, covered, "gecen yil ayni hafta", full)
    # Anything freshly fetched wins; anything not fetched falls back to what was
    # already published, so a day lost to a 502 last night is filled in tonight
    # rather than written off for the week.
    for source in (now, then):
        for bid, days_seen in source.items():
            known.setdefault(bid, {}).update(days_seen)
    now = then = known

    os.makedirs(FEED, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    by_country, total_bytes, written = {}, 0, 0
    for b in beaches:
        h = [now.get(b["id"], {}).get(d) for d in now_days]
        y = [then.get(b["id"], {}).get(d) for d in then_days]
        # A beach with nothing in either window is left out entirely rather
        # than written as a row of nulls the app has to special-case.
        if not any(v is not None for v in h):
            continue
        entry = {"h": h}
        if any(v is not None for v in y):
            entry["y"] = y
        by_country.setdefault(b["cc"], {})[b["id"]] = entry

    for cc, readings in sorted(by_country.items()):
        payload = {"u": stamp, "s": ATTRIBUTION,
                   "d": now_days, "yd": then_days, "r": readings}
        path = os.path.join(FEED, f"{cc}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))
        size = os.path.getsize(path)
        total_bytes += size
        written += len(readings)
        print(f"  {cc}: {len(readings)} plaj ({size / 1024:.0f} KB)")

    print(f"\n{written} plaj -> {FEED}")
    print(f"toplam {total_bytes / 1024 / 1024:.2f} MB")
    with_year = sum(1 for c in by_country.values() for e in c.values() if "y" in e)
    print(f"{with_year} plajda gecen yil verisi var")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
