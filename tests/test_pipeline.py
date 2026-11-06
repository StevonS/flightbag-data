import datetime as dt
import gzip
import json
from pathlib import Path

import pytest

from pipeline import (
    build_airports,
    build_airspace,
    build_dah,
    charts,
    cycle,
    manifest,
    plan,
)

FIXTURES = Path(__file__).parent / "fixtures"


# --- cycles -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "airac", "edition"),
    [
        (dt.date(2026, 10, 29), dt.date(2026, 10, 29), dt.date(2026, 10, 29)),
        (dt.date(2026, 11, 25), dt.date(2026, 10, 29), dt.date(2026, 10, 29)),
        (dt.date(2026, 11, 26), dt.date(2026, 11, 26), dt.date(2026, 10, 29)),
        (dt.date(2026, 12, 24), dt.date(2026, 12, 24), dt.date(2026, 12, 24)),
        (dt.date(2026, 10, 28), dt.date(2026, 10, 1), dt.date(2026, 9, 3)),
    ],
)
def test_cycles(today, airac, edition):
    assert cycle.current_airac(today) == airac
    assert cycle.current_chart_edition(today) == edition


def test_chart_dates_match_published_faa_editions():
    # FAA sectional editions published on aeronav.faa.gov.
    for published in ["09-03-2026", "10-29-2026", "12-24-2026"]:
        day = dt.datetime.strptime(published, "%m-%d-%Y").date()
        assert cycle.current_chart_edition(day) == day
        assert cycle.faa_date(day) == published


# --- chart names ------------------------------------------------------------


def test_chart_ids_and_names():
    assert charts.chart_id("Dallas-Ft_Worth") == "dallas_ft_worth"
    assert charts.chart_id("McGrath") == "mcgrath"
    assert charts.display_name("Lake_Huron") == "Lake Huron"
    assert len(set(map(charts.chart_id, charts.SECTIONALS))) == 53


def test_tif_keys_match_clip_shapes():
    assert charts.tif_key("Detroit SEC.tif") == "detroit"
    assert charts.tif_key("Dallas-Ft Worth SEC.tif") == "dallas_ft_worth"
    assert (
        charts.tif_key("Western Aleutian Islands East SEC.tif")
        == "western_aleutian_islands_east"
    )


def test_every_chart_has_a_clip_shape():
    clip_dir = Path(__file__).parent.parent / "clipshapes" / "sectional"
    shapes = {p.stem for p in clip_dir.glob("*.shp")}
    split = {"western_aleutian_islands"}  # East and West shapes
    for name in charts.SECTIONALS:
        cid = charts.chart_id(name)
        if cid in split:
            assert {f"{cid}_east", f"{cid}_west"} <= shapes
        else:
            assert cid in shapes, name


def test_parse_selection():
    assert charts.parse_selection("all") == charts.SECTIONALS
    assert charts.parse_selection("Detroit, lake_huron") == ["Detroit", "Lake_Huron"]
    with pytest.raises(ValueError):
        charts.parse_selection("Atlantis")


# --- plan -------------------------------------------------------------------


def test_plan_skips_a_full_build_that_already_exists():
    today = dt.date(2026, 11, 2)
    first = plan.plan("all", False, set(), today)
    assert first["build"] is True
    assert first["tag"] == "airac-2026-10-29"
    assert first["edition"] == "10-29-2026"

    again = plan.plan("all", False, {"airac-2026-10-29"}, today)
    assert again["build"] is False

    forced = plan.plan("all", True, {"airac-2026-10-29"}, today)
    assert forced["build"] is True


def test_plan_partial_build_is_tagged_separately():
    result = plan.plan("Detroit", False, {"airac-2026-10-29"}, dt.date(2026, 11, 2))
    assert result["build"] is True
    assert result["full"] is False
    assert result["tag"] == "airac-2026-10-29-partial"
    assert json.loads(result["matrix"]) == {"chart": ["Detroit"]}


# --- airports ---------------------------------------------------------------


def _read_gz(path: Path) -> dict:
    with gzip.open(path) as f:
        return json.load(f)


def test_airports(tmp_path):
    meta = build_airports.build(FIXTURES, tmp_path)
    data = _read_gz(tmp_path / "airports.json.gz")
    by_id = {a["id"]: a for a in data["airports"]}

    # Canada and US only; no heliports or closed fields.
    assert set(by_id) == {"CPZ9", "CYXU", "KDTW"}
    assert meta["count"] == 3

    cyxu = by_id["CYXU"]
    assert cyxu["type"] == "M"
    assert cyxu["elevFt"] == 912
    assert cyxu["lat"] == 43.0356
    # Closed runway dropped; longest first.
    assert [r["id"] for r in cyxu["runways"]] == ["15/33", "09/27"]
    assert cyxu["runways"][0] == {
        "id": "15/33",
        "lengthFt": 8800,
        "widthFt": 200,
        "surface": "ASP",
        "lit": True,
    }
    # Unparseable frequency dropped.
    assert cyxu["freqs"] == [
        {"type": "TWR", "name": "LONDON TWR", "mhz": 119.4},
        {"type": "ATIS", "name": "ATIS", "mhz": 127.15},
    ]
    # Falls back to the local code when there is no ICAO code.
    assert by_id["CPZ9"]["runways"][0]["surface"] == "TURF"


def test_airports_output_is_reproducible(tmp_path):
    a = build_airports.build(FIXTURES, tmp_path / "a")
    b = build_airports.build(FIXTURES, tmp_path / "b")
    assert a["sha256"] == b["sha256"]


# --- airspace ---------------------------------------------------------------


def test_airspace(tmp_path):
    meta = build_airspace.build(FIXTURES / "class_airspace.geojson", tmp_path)
    data = _read_gz(tmp_path / "airspace.json.gz")
    items = data["airspace"]

    assert meta["byClass"] == {"B": 2, "D": 1}
    # D drawn first, then B.
    assert [f["cls"] for f in items] == ["D", "B", "B"]

    pontiac = items[0]
    assert pontiac["name"] == "PONTIAC CLASS D"  # lower-case keys accepted
    assert pontiac["lower"] == {"ref": "SFC"}
    assert pontiac["upper"] == {"ft": 3500, "ref": "MSL"}

    shelf = next(f for f in items if f["lower"].get("ft") == 3000)
    assert shelf["upper"] == {"ft": 10000, "ref": "MSL"}
    # MultiPolygon with a hole: one polygon, outer ring + hole.
    assert len(shelf["polygons"]) == 1
    assert len(shelf["polygons"][0]) == 2


def test_airspace_fails_loudly_on_unknown_schema(tmp_path):
    src = tmp_path / "bad.geojson"
    src.write_text(json.dumps({"features": [{"properties": {"X": 1}}]}))
    with pytest.raises(SystemExit):
        build_airspace.build(src, tmp_path / "out")


def test_limits():
    assert build_airspace.limit({"UPPER_VAL": "45", "UPPER_UOM": "FL"}, "UPPER") == {
        "ft": 4500,
        "ref": "MSL",
    }
    assert build_airspace.limit({"UPPER_VAL": "-9998"}, "UPPER") == {"ref": "UNL"}
    assert build_airspace.limit(
        {"LOWER_VAL": "700", "LOWER_CODE": "AGL"}, "LOWER"
    ) == {"ft": 700, "ref": "AGL"}


# --- manifest ---------------------------------------------------------------


def test_manifest(tmp_path):
    (tmp_path / "detroit.json").write_text(
        json.dumps(
            {
                "id": "detroit",
                "name": "Detroit",
                "kind": "sectional",
                "file": "detroit.mbtiles",
                "bounds": [-85, 40, -76.6, 44.3],
                "minZoom": 5,
                "maxZoom": 11,
                "sizeBytes": 1,
                "sha256": "ab",
            }
        )
    )
    build_airports.build(FIXTURES, tmp_path)

    result = manifest.build(
        tmp_path, "airac-2026-10-29", dt.date(2026, 11, 2), "o/flightbag-data"
    )

    assert result["schemaVersion"] == 1
    chart = result["charts"][0]
    assert chart["url"] == (
        "https://github.com/o/flightbag-data/releases/download/"
        "airac-2026-10-29/detroit.mbtiles"
    )
    assert chart["edition"] == "2026-10-29"
    assert chart["expires"] == "2026-12-24"
    assert "file" not in chart

    airports = result["datasets"][0]
    assert airports["id"] == "airports"
    assert airports["effective"] == "2026-10-29"
    assert airports["expires"] == "2026-11-26"
    assert json.loads((tmp_path / "manifest.json").read_text()) == result


def test_simplify_keeps_shape_and_drops_collinear_points():
    line = [(x / 100, 0.0) for x in range(101)]  # straight: 101 points
    assert build_airspace.simplify(line, 0.0002) == [(0.0, 0.0), (1.0, 0.0)]

    corner = [(0.0, 0.0), (0.5, 0.0), (1.0, 0.0), (1.0, 0.5), (1.0, 1.0)]
    assert build_airspace.simplify(corner, 0.0002) == [
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 1.0),
    ]


def test_rings_stay_closed_after_simplification():
    import math

    circle = [
        [math.cos(t / 500 * 2 * math.pi) * 0.1, math.sin(t / 500 * 2 * math.pi) * 0.1]
        for t in range(501)
    ]
    ring = build_airspace._round_ring(circle)
    assert ring[0] == ring[-1]
    assert 20 < len(ring) < 200


# --- Designated Airspace Handbook (Canada) -----------------------------------


def _inside(ring, lon, lat):
    c = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            c = not c
    return c


def test_dah(tmp_path):
    out = tmp_path / "dah.json"
    summary = build_dah.build(FIXTURES / "dah_excerpt.txt", out)
    data = json.loads(out.read_text())
    assert data["effective"] is None or "2026" in data["effective"]
    items = {a["name"]: a for a in data["airspace"]}
    assert summary["controlZones"] == 6

    # Circle: 5 NM around the aerodrome, surface to 4000 ft.
    cykf = items["Kitchener/Waterloo, ON"]
    assert (cykf["cls"], cykf["kind"]) == ("C", "CZ")
    assert cykf["lower"] == {"ref": "SFC"}
    assert cykf["upper"] == {"ft": 4000, "ref": "MSL"}
    ring = cykf["polygons"][0][0]
    assert _inside(ring, -80.3786, 43.4608)  # the airport
    assert not _inside(ring, -80.3786, 43.56)  # 6 NM north

    # Lines and arcs: Billy Bishop contains CYTZ but not Pearson.
    cytz = items["Toronto/Billy Bishop Toronto City Airport, ON"]["polygons"][0][0]
    assert _inside(cytz, -79.3962, 43.6275)
    assert not _inside(cytz, -79.6306, 43.6772)

    assert items["Hamilton, ON"]["cls"] == "D"
    # Paragraph number printed apart from its text.
    assert items["Moose Jaw, SK"]["upper"] == {"ft": 8000, "ref": "MSL"}

    # Class F: two designations merge to the full range.
    meaford = items["MEAFORD RANGE, ON"]
    assert (meaford["kind"], meaford["ident"]) == ("CYR", "CYR501")
    assert meaford["lower"] == {"ref": "SFC"}
    assert meaford["upper"] == {"ft": 15000, "ref": "MSL"}
    assert meaford["times"] == "Cont; Ocsl by NOTAM"
    assert items["JOYCEVILLE, ON"]["upper"] == {"ft": 1400, "ref": "MSL"}
    # Lines tangent to two circles become their hull.
    matsqui = next(a for a in data["airspace"] if a["ident"] == "CYR141")
    assert len(matsqui["polygons"][0][0]) > 10


def test_dah_limits():
    assert build_dah.limits_cz("The airspace from 2000´ to 6000´ (5700´ AAE) within") == (
        {"ft": 2000, "ref": "MSL"},
        {"ft": 6000, "ref": "MSL"},
    )
    assert build_dah.limits_cz("The airspace to below 700´ AGL within") == (
        {"ref": "SFC"},
        {"ft": 700, "ref": "AGL"},
    )
    assert build_dah.limits_f(["Surface to FL180"]) == ({"ref": "SFC"}, {"ft": 18000, "ref": "MSL"})
    assert build_dah.limits_f(["Above 12,000´ to Unlimited"]) == (
        {"ft": 12000, "ref": "MSL"},
        {"ref": "UNL"},
    )


def test_airspace_with_dah(tmp_path):
    dah = tmp_path / "dah.json"
    build_dah.build(FIXTURES / "dah_excerpt.txt", dah)
    meta = build_airspace.build(FIXTURES / "class_airspace.geojson", tmp_path / "out", dah)
    items = _read_gz(tmp_path / "out" / "airspace.json.gz")["airspace"]
    assert meta["byClass"]["CYR"] == 4
    assert "Designated Airspace Handbook" in meta["source"]
    # Class F areas are drawn last, on top.
    assert items[-1]["cls"] == "F"
