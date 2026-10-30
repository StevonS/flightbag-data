"""Build airports.json.gz for Canada and the US from OurAirports CSVs.

OurAirports (https://ourairports.com/data/) is community-maintained and in the
public domain. It is NOT an official source; the app labels it as such.

Usage: python -m pipeline.build_airports <csv dir> <out dir>
The csv dir must hold airports.csv, runways.csv and airport-frequencies.csv.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

COUNTRIES = {"CA", "US"}

# OurAirports type -> compact code used by the app.
TYPES = {
    "large_airport": "L",
    "medium_airport": "M",
    "small_airport": "S",
    "seaplane_base": "W",
}

SOURCE_URL = "https://davidmegginson.github.io/ourairports-data/{file}"
FILES = ("airports.csv", "runways.csv", "airport-frequencies.csv")


def _float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: str) -> int | None:
    number = _float(value)
    return None if number is None else round(number)


def _ident(row: dict) -> str:
    """Prefer the code pilots use: ICAO / GPS code, then local code."""
    for key in ("icao_code", "gps_code", "ident", "local_code"):
        if row.get(key, "").strip():
            return row[key].strip().upper()
    return ""


def load_runways(path: Path) -> dict[str, list[dict]]:
    runways: dict[str, list[dict]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("closed") == "1":
                continue
            ends = "/".join(
                e for e in (row.get("le_ident", ""), row.get("he_ident", "")) if e
            )
            runway = {
                "id": ends,
                "lengthFt": _int(row.get("length_ft", "")),
                "widthFt": _int(row.get("width_ft", "")),
                "surface": row.get("surface", "").strip().upper() or None,
                "lit": row.get("lighted") == "1",
            }
            runways[row["airport_ref"]].append(
                {k: v for k, v in runway.items() if v is not None}
            )
    for items in runways.values():
        items.sort(key=lambda r: -(r.get("lengthFt") or 0))
    return runways


def load_frequencies(path: Path) -> dict[str, list[dict]]:
    freqs: dict[str, list[dict]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            mhz = _float(row.get("frequency_mhz", ""))
            if mhz is None:
                continue
            freqs[row["airport_ref"]].append(
                {
                    "type": row.get("type", "").strip().upper(),
                    "name": row.get("description", "").strip(),
                    "mhz": round(mhz, 3),
                }
            )
    return freqs


def build(csv_dir: Path, out_dir: Path) -> dict:
    runways = load_runways(csv_dir / "runways.csv")
    freqs = load_frequencies(csv_dir / "airport-frequencies.csv")

    airports = []
    with (csv_dir / "airports.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            code = TYPES.get(row.get("type", ""))
            if code is None or row.get("iso_country") not in COUNTRIES:
                continue
            lat = _float(row.get("latitude_deg", ""))
            lon = _float(row.get("longitude_deg", ""))
            ident = _ident(row)
            if lat is None or lon is None or not ident:
                continue
            airport = {
                "id": ident,
                "name": row.get("name", "").strip(),
                "type": code,
                "lat": round(lat, 5),
                "lon": round(lon, 5),
                "elevFt": _int(row.get("elevation_ft", "")),
                "country": row["iso_country"],
                "region": row.get("iso_region", ""),
                "city": row.get("municipality", "").strip() or None,
                "runways": runways.get(row["id"], []),
                "freqs": freqs.get(row["id"], []),
            }
            airports.append({k: v for k, v in airport.items() if v is not None})

    airports.sort(key=lambda a: a["id"])
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "airports.json.gz"
    payload = json.dumps(
        {"source": "OurAirports", "airports": airports},
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode()
    # mtime=0 keeps the file byte-identical for identical input.
    with gzip.GzipFile(out, "wb", mtime=0) as gz:
        gz.write(payload)

    meta = {
        "id": "airports",
        "file": out.name,
        "count": len(airports),
        "sizeBytes": out.stat().st_size,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "source": "OurAirports (unofficial)",
    }
    (out_dir / "airports.meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
