# flightbag-data

Aeronautical data for the FlightBag app, converted from public sources and
published as GitHub releases. Not for primary navigation.

| File | Contents | Source | Updated |
| --- | --- | --- | --- |
| `<chart>.mbtiles` | FAA VFR sectional, clipped, WebP tiles, zoom 5–11 | [FAA digital VFR charts](https://www.faa.gov/air_traffic/flight_info/aeronav/digital_products/vfr/) (public domain) | 56 days |
| `airspace.json.gz` | US Class B, C and D airspace; Canadian control zones and Class F (CYR, CYD, CYA) areas | [FAA AIS open data](https://adds-faa.opendata.arcgis.com/) (public domain); NAV CANADA [Designated Airspace Handbook](https://www.navcanada.ca/en/aeronautical-information/operational-guides-and-publications.aspx) (Source of Canadian Airspace Data © Transport Canada) | 28 / 56 days |
| `airports.json.gz` | Canadian and US airports, runways, frequencies | [OurAirports](https://ourairports.com/data/) (public domain, unofficial) | 28 days |
| `manifest.json` | Index of the files above with editions, expiry dates and SHA-256 | — | each release |

The app reads
`https://github.com/StevonS/flightbag-data/releases/latest/download/manifest.json`.

## How it works

`.github/workflows/build.yml` runs daily and publishes once per 28-day AIRAC
cycle (tag `airac-YYYY-MM-DD`). Each sectional is converted in its own job:

1. Download the GeoTIFF zip from aeronav.faa.gov.
2. Expand the palette to RGB, reproject to Web Mercator at zoom-11
   resolution, and clip to the chart body with a clip shape.
3. Write MBTiles (WebP) and build zoom levels 10–5 with `gdaladdo`.

Run it manually from **Actions → Build data → Run workflow**. A subset of
charts (for example `Detroit`) is published as a prerelease tagged
`…-partial`, which the app ignores.

## Development

```sh
pip install pytest
python -m pytest tests
python -m pipeline.build_chart Detroit 10-29-2026 out   # needs GDAL 3.6+
```

## Credits

Sectional clip shapes in `clipshapes/` are from
[chartmaker](https://github.com/n129bz/chartmaker) by Brian A. Manlove, MIT
licence (see `clipshapes/LICENSE-chartmaker`).

## Licence

Code: MIT. Data: public domain sources as listed above.

## Canadian airspace

`pipeline/build_dah.py` reads the Designated Airspace Handbook (converted
with `pdftotext -layout`) and turns control-zone and Class F descriptions
into polygons: circles, lines and arcs, and areas bounded by lines tangent
to two circles. Boundaries that follow a border or shoreline, or that have
exclusions, are drawn with straight lines and marked `"approx": true`.
Terminal control areas, transition areas and Class E airways are not
included yet.
