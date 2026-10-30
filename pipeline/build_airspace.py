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
# Douglas-Peucker tolerance in degrees (about 20 m). The source draws arcs
# with thousands of points; this keeps boundaries well within a line width
# on a sectional while shrinking the file about tenfold.
TOLERANCE = 0.0002


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


def _perpendicular_sq(p, a, b) -> float:
    """Squared distance from point p to segment a-b (planar degrees)."""
    (px, py), (ax, ay), (bx, by) = p, a, b
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return (px - ax - t * dx) ** 2 + (py - ay - t * dy) ** 2


def simplify(points: list, tolerance: float) -> list:
    """Douglas-Peucker simplification, iterative. Keeps both end points."""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    tol_sq = tolerance * tolerance
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        worst, index = 0.0, -1
        for i in range(first + 1, last):
            d = _perpendicular_sq(points[i], points[first], points[last])
            if d > worst:
                worst, index = d, i
        if index != -1 and worst > tol_sq:
            keep[index] = True
            stack.append((first, index))
            stack.append((index, last))
    return [p for p, k in zip(points, keep) if k]


def _round_ring(ring: list) -> list:
    points = [(float(lon), float(lat)) for lon, lat, *_ in ring]
    # Simplify the two halves separately so a closed ring keeps its shape.
    mid = len(points) // 2
    simplified = simplify(points[: mid + 1], TOLERANCE)[:-1] + simplify(
        points[mid:], TOLERANCE
    )
    out = []
    for lon, lat in simplified:
        point = [round(lon, PRECISION), round(lat, PRECISION)]
        if not out or out[-1] != point:
            out.append(point)
    if out and out[0] != out[-1]:
        out.append(out[0])
    return out if len(out) >= 4 else []


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
    result = []
    for poly in groups:
        rings = [_round_ring(r) for r in poly if len(r) >= 4]
        if rings and rings[0]:  # drop polygons whose outer ring collapsed
            result.append([r for r in rings if r])
    return result


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
