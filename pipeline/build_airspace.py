"""Build airspace.json.gz (US Class B, C and D) from the FAA AIS open data
Class Airspace layer (GeoJSON).

Usage: python -m pipeline.build_airspace <class_airspace.geojson> <out dir>
"""

from __future__ import annotations

import gzip
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

SOURCE_URL = (
    "https://opendata.arcgis.com/datasets/"
    "c6a62360338e408cb1512366ad61559e_0.geojson"
)
CLASSES = {"B", "C", "D"}
PRECISION = 5  # decimal degrees, about 1 m


def _get(props: dict, *names: str):
    """Case-insensitive property lookup; first non-empty value wins."""
    lowered = {k.lower(): v for k, v in props.items()}
    for name in names:
        value = lowered.get(name.lower())
        if value not in (None, ""):
            return value
    return None


def airspace_class(props: dict) -> str | None:
    value = _get(props, "CLASS", "CLASS_CODE")
    if isinstance(value, str) and value.strip().upper() in CLASSES:
        return value.strip().upper()
    local = str(_get(props, "LOCAL_TYPE") or "").upper()
    for cls in CLASSES:
        if local == f"CLASS_{cls}":
            return cls
    return None


def limit(props: dict, side: str) -> dict:
    """Altitude limit for ``side`` ('LOWER' or 'UPPER').

    Returns ``{"ft": int, "ref": "MSL"|"AGL"}`` or ``{"ref": "SFC"}``.
    """
    code = str(_get(props, f"{side}_CODE") or "").upper()
    desc = str(_get(props, f"{side}_DESC") or "").upper()
    raw = _get(props, f"{side}_VAL")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = None

    if code == "SFC" or desc == "SFC" or (side == "LOWER" and value in (0, None)):
        return {"ref": "SFC"}
    if value is None or value < 0:
        return {"ref": "UNL" if side == "UPPER" else "SFC"}
    uom = str(_get(props, f"{side}_UOM") or "FT").upper()
    feet = value * 100 if uom == "FL" else value
    ref = "AGL" if code == "AGL" or desc == "AGL" else "MSL"
    return {"ft": int(round(feet)), "ref": ref}


def _round_ring(ring: list) -> list:
    out = []
    for lon, lat, *_ in ring:
        point = [round(lon, PRECISION), round(lat, PRECISION)]
        if not out or out[-1] != point:
            out.append(point)
    return out


def polygons(geometry: dict) -> list[list]:
    """List of polygons; each polygon is [outer ring, hole, ...]."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        groups = [coords]
    elif kind == "MultiPolygon":
        groups = coords
    else:
        return []
    return [[_round_ring(r) for r in poly if len(r) >= 4] for poly in groups]


def build(geojson_path: Path, out_dir: Path) -> dict:
    data = json.loads(geojson_path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    print(f"{len(features)} features in source")
    if features:
        print("Property keys:", sorted(features[0].get("properties", {})))

    out_features = []
    seen = Counter()
    for feature in features:
        props = feature.get("properties") or {}
        seen[str(_get(props, "LOCAL_TYPE", "CLASS"))] += 1
        cls = airspace_class(props)
        if cls is None:
            continue
        polys = [p for p in polygons(feature.get("geometry")) if p]
        if not polys:
            continue
        out_features.append(
            {
                "cls": cls,
                "name": str(_get(props, "NAME") or "").strip(),
                "ident": str(_get(props, "IDENT", "ICAO_ID") or "").strip(),
                "lower": limit(props, "LOWER"),
                "upper": limit(props, "UPPER"),
                "polygons": polys,
            }
        )

    print("Types seen:", dict(seen.most_common(12)))
    if not out_features:
        raise SystemExit("No Class B/C/D airspace found; check the schema above")

    # Draw order: D under C under B.
    out_features.sort(key=lambda f: ("DCB".index(f["cls"]), f["name"]))
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "airspace.json.gz"
    payload = json.dumps(
        {"source": "FAA AIS open data", "airspace": out_features},
        separators=(",", ":"),
    ).encode()
    with gzip.GzipFile(out, "wb", mtime=0) as gz:
        gz.write(payload)

    counts = Counter(f["cls"] for f in out_features)
    meta = {
        "id": "airspace",
        "file": out.name,
        "count": len(out_features),
        "byClass": dict(sorted(counts.items())),
        "sizeBytes": out.stat().st_size,
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "source": "FAA AIS open data (US only)",
    }
    (out_dir / "airspace.meta.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
