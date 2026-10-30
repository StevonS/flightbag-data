"""FAA sectional chart catalogue."""

from __future__ import annotations

import re

# FAA file names as used in download URLs
# (https://aeronav.faa.gov/visual/<MM-DD-YYYY>/sectional-files/<name>.zip).
SECTIONALS = [
    "Albuquerque", "Anchorage", "Atlanta", "Bethel", "Billings",
    "Brownsville", "Cape_Lisburne", "Charlotte", "Cheyenne", "Chicago",
    "Cincinnati", "Cold_Bay", "Dallas-Ft_Worth", "Dawson", "Denver",
    "Detroit", "Dutch_Harbor", "El_Paso", "Fairbanks", "Great_Falls",
    "Green_Bay", "Halifax", "Hawaiian_Islands", "Houston", "Jacksonville",
    "Juneau", "Kansas_City", "Ketchikan", "Klamath_Falls", "Kodiak",
    "Lake_Huron", "Las_Vegas", "Los_Angeles", "McGrath", "Memphis", "Miami",
    "Montreal", "New_Orleans", "New_York", "Nome", "Omaha", "Phoenix",
    "Point_Barrow", "Salt_Lake_City", "San_Antonio", "San_Francisco",
    "Seattle", "Seward", "St_Louis", "Twin_Cities", "Washington",
    "Western_Aleutian_Islands", "Wichita",
]  # fmt: skip

DOWNLOAD_URL = (
    "https://aeronav.faa.gov/visual/{date}/sectional-files/{name}.zip"
)


def chart_id(faa_name: str) -> str:
    """Stable id used for file names: ``Dallas-Ft_Worth`` -> ``dallas_ft_worth``."""
    return re.sub(r"[^a-z0-9]+", "_", faa_name.lower()).strip("_")


def display_name(faa_name: str) -> str:
    """Human name: ``Dallas-Ft_Worth`` -> ``Dallas-Ft Worth``."""
    return faa_name.replace("_", " ")


def tif_key(tif_file_name: str) -> str:
    """Clip-shape key for a GeoTIFF inside a chart zip.

    ``Detroit SEC.tif`` -> ``detroit``;
    ``Western Aleutian Islands East SEC.tif`` -> ``western_aleutian_islands_east``.
    """
    stem = re.sub(r"\.tiff?$", "", tif_file_name, flags=re.IGNORECASE)
    stem = re.sub(r"\s+SEC$", "", stem, flags=re.IGNORECASE)
    return chart_id(stem)


def parse_selection(selection: str) -> list[str]:
    """Charts named in a workflow input: ``all`` or a comma-separated list."""
    selection = selection.strip()
    if selection.lower() in ("", "all"):
        return list(SECTIONALS)
    wanted = {chart_id(s) for s in selection.split(",") if s.strip()}
    chosen = [n for n in SECTIONALS if chart_id(n) in wanted]
    unknown = wanted - {chart_id(n) for n in chosen}
    if unknown:
        raise ValueError(f"Unknown charts: {', '.join(sorted(unknown))}")
    return chosen
