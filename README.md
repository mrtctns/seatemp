# seatemp — daily sea surface temperature feed

Serves the data behind the **Sea Temperature** iOS app. One JSON file per
country, refreshed twice a day by the workflow in `.github/workflows/`.

The app never queries the satellite archive itself. NOAA CoastWatch is a free
public research endpoint that returns 502s and 503s under load and updates only
once a day, so a per-user request would retry against a busy server to fetch a
number that had not changed since the last thousand people asked for it. This
repository is the buffer: a scheduled job does the retrying, and phones read a
static file off a CDN.

## Layout

```
v1/<COUNTRY>.json     one file per ISO country code
beaches.json          the catalogue, with each beach pinned to a sea grid cell
tools/                the job that rebuilds v1/
```

### Feed format

```json
{
  "u": "2026-08-04T06:00:00+00:00",   // when this file was written
  "s": "NOAA CoastWatch · NASA JPL",  // attribution, shown in the app
  "r": {
    "tr1a2b3c": { "c": 25.4, "t": "2026-08-03T12:00:00Z" }
  }
}
```

`c` is degrees Celsius. `t` is when the **satellite pass** was taken, which is
not when the file was written — the app shows that date rather than implying the
reading is from this morning.

## Data

- **Temperature** — [NOAA CoastWatch](https://coastwatch.noaa.gov/), Geo-polar
  Blended SST (`noaacwBLENDEDsstDNDaily`), produced with NASA JPL. US Government
  work, public domain. An L4 analysis: gap-free through cloud, and reporting the
  bulk water temperature a swimmer feels rather than the skin temperature an
  infrared sensor sees directly.
- **Beaches** — [OpenStreetMap](https://www.openstreetmap.org/copyright)
  contributors, ODbL. Redistributed here under the same licence.

## Limits

A satellite reports one value for a patch of sea a few kilometres across. Each
beach in `beaches.json` carries `d`, the distance in kilometres from the beach to
the water actually sampled, and the app shows it. Shallow water at the shore can
be two or three degrees warmer on a hot, still afternoon.

This is measurement, not forecast. Every value is a pass already flown.
