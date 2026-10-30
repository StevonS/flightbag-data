"""Convert one FAA sectional chart into an MBTiles file of WebP tiles.

Steps for each GeoTIFF in the chart's zip:
  1. gdal_translate -expand rgb   (palette -> RGB, as a VRT)
  2. gdalwarp to EPSG:3857 at zoom-11 resolution, clipped to the chart body
     with a clip shape (removes the legend and border), with an alpha band
Then all parts are mosaicked, written as MBTiles at zoom 11, and gdaladdo
builds zoom 10 down to MIN_ZOOM.

Usage: python -m pipeline.build_chart <FAA name> <MM-DD-YYYY> <out dir>
Requires GDAL 3.6+ command-line tools.
"""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from . import charts

MIN_ZOOM = 5
MAX_ZOOM = 11
WEBP_QUALITY = 60

# Web Mercator metres per pixel at MAX_ZOOM.
_ORIGIN_SHIFT = 2 * math.pi * 6378137 / 2
TARGET_RES = 2 * _ORIGIN_SHIFT / 256 / (2**MAX_ZOOM)

CLIP_DIR = Path(__file__).resolve().parent.parent / "clipshapes" / "sectional"


def run(*args: str) -> str:
    print("+", " ".join(args), flush=True)
    result = subprocess.run(args, check=True, capture_output=True, text=True)
    return result.stdout


def download(url: str, dest: Path) -> None:
    print(f"Downloading {url}", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "flightbag-data"})
    with urllib.request.urlopen(req, timeout=300) as resp, dest.open("wb") as f:
        shutil.copyfileobj(resp, f, length=1 << 20)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def warp_part(tif: Path, clip: Path, work: Path) -> Path:
    expanded = work / f"{tif.stem}.rgb.vrt"
    warped = work / f"{tif.stem}.3857.tif"
    run("gdal_translate", "-q", "-of", "VRT", "-expand", "rgb",
        str(tif), str(expanded))  # fmt: skip
    run(
        "gdalwarp", "-q", "-overwrite",
        "-t_srs", "EPSG:3857",
        "-tr", str(TARGET_RES), str(TARGET_RES), "-tap",
        "-r", "bilinear",
        "-cutline", str(clip), "-crop_to_cutline",
        "-dstalpha",
        "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
        "-co", "TILED=YES", "-co", "COMPRESS=DEFLATE",
        str(expanded), str(warped),
    )  # fmt: skip
    return warped


def bounds_wgs84(mbtiles: Path) -> list[float]:
    """[west, south, east, north] from the MBTiles metadata."""
    info = json.loads(run("gdalinfo", "-json", str(mbtiles)))
    raw = info["metadata"][""]["bounds"]
    return [round(float(v), 5) for v in raw.split(",")]


def build(faa_name: str, edition: str, out_dir: Path) -> dict:
    chart = charts.chart_id(faa_name)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{chart}.mbtiles"

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        zip_path = work / "chart.zip"
        download(charts.DOWNLOAD_URL.format(date=edition, name=faa_name), zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work / "src")

        parts = []
        for tif in sorted((work / "src").rglob("*.tif")):
            key = charts.tif_key(tif.name)
            clip = CLIP_DIR / f"{key}.shp"
            if "inset" in key:
                print(f"Skipping inset {tif.name}")
                continue
            if not clip.exists():
                print(f"::warning::No clip shape for {tif.name} ({key}); skipped")
                continue
            parts.append(warp_part(tif, clip, work))
        if not parts:
            raise SystemExit(f"No usable GeoTIFFs in {faa_name}")

        mosaic = work / "mosaic.vrt"
        run("gdalbuildvrt", "-q", str(mosaic), *map(str, parts))

        out.unlink(missing_ok=True)
        run(
            "gdal_translate", "-q", "-of", "MBTILES",
            "-co", "TILE_FORMAT=WEBP", "-co", f"QUALITY={WEBP_QUALITY}",
            "-co", f"NAME={charts.display_name(faa_name)} Sectional",
            "-co", f"DESCRIPTION=FAA VFR sectional, edition {edition}",
            "-co", "TYPE=baselayer",
            str(mosaic), str(out),
        )  # fmt: skip
        factors = [str(2**i) for i in range(1, MAX_ZOOM - MIN_ZOOM + 1)]
        run("gdaladdo", "-q", "-r", "average", str(out), *factors)

    meta = {
        "id": chart,
        "name": charts.display_name(faa_name),
        "kind": "sectional",
        "file": out.name,
        "bounds": bounds_wgs84(out),
        "minZoom": MIN_ZOOM,
        "maxZoom": MAX_ZOOM,
        "sizeBytes": out.stat().st_size,
        "sha256": sha256_of(out),
    }
    (out_dir / f"{chart}.json").write_text(json.dumps(meta, indent=2))
    print(json.dumps(meta, indent=2))
    return meta


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    build(sys.argv[1], sys.argv[2], Path(sys.argv[3]))
