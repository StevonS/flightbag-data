"""Build Canadian airspace from NAV CANADA's Designated Airspace Handbook
(DAH): control zones (Part 3) and Class F danger, restricted and advisory
areas (Part 5).

The DAH is published free as a PDF every 56 days. The workflow converts it
with ``pdftotext -layout``; this module reads that text.

Usage: python -m pipeline.build_dah <dah_layout.txt> <out.json>
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

PRECISION = 5
ARC_STEP_DEG = 5

COORD = (
    r"([NS])(\d{2})°(\d{2})'(\d{2}(?:\.\d+)?)\"\s*"
    r"([EW])(\d{2,3})°(\d{2})'(\d{2}(?:\.\d+)?)\""
)
_COORD = re.compile(COORD)
_ARC = re.compile(
    r"thence\s+(counter-clockwise|clockwise)\s+along\s+the\s+arc\s+of\s+a\s+"
    r"circle\s+of\s+([\d.]+)\s+miles?\s+radius\s+centred\s+on\s+" + COORD
)
_CIRCLE = re.compile(
    r"circle\s+of:?\s+([\d.]+)\s+miles?\s+radius\s+centred\s+on:?"
    r"(?:\s+the\s+following:?)?\s+" + COORD
)
_FT = r"([\d,]+)\s*[´'’]"

# Lines that are page furniture, not content.
_JUNK = re.compile(
    r"DESIGNATED AIRSPACE HANDBOOK|Effective 0901Z|DAH\s+\w+ \d+, \d{4}|"
    r"FLIGHT INFORMATION REGION\s{3,}|^\s*\d{1,3}\s*$|"
    r"^\s*(?:\d+\s+)?Transport Canada\s*$|^\s*[a-z]?\s*(?:t|f|of)? ?Canada\s*$|"
    r"^\s*(RESTRICTED|DANGER|ADVISORY) AREAS\s{3,}|Source of Canadian Airspace Data|"
    r"^\s*(?!to\s*$|and\s*$)[a-z]{1,4}\s*$"
)


def coord(m: re.Match, start: int = 1) -> tuple[float, float]:
    """(lon, lat) from a COORD match starting at group ``start``."""
    g = [m.group(start + i) for i in range(8)]
    lat = int(g[1]) + int(g[2]) / 60 + float(g[3]) / 3600
    lon = int(g[5]) + int(g[6]) / 60 + float(g[7]) / 3600
    if g[0] == "S":
        lat = -lat
    if g[4] == "W":
        lon = -lon
    return lon, lat


def _destination(lon, lat, bearing_deg, nm):
    """Great-circle destination; good to metres at these distances."""
    r = nm / 3440.065
    p1, l1, b = map(math.radians, (lat, lon, bearing_deg))
    p2 = math.asin(math.sin(p1) * math.cos(r) + math.cos(p1) * math.sin(r) * math.cos(b))
    l2 = l1 + math.atan2(
        math.sin(b) * math.sin(r) * math.cos(p1),
        math.cos(r) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(l2), math.degrees(p2)


def _bearing(c, p):
    p1, p2 = math.radians(c[1]), math.radians(p[1])
    dl = math.radians(p[0] - c[0])
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360


def circle(center, nm):
    pts = [_destination(*center, b, nm) for b in range(0, 360, ARC_STEP_DEG)]
    return pts + [pts[0]]


def arc(center, nm, start, end, clockwise):
    """Points from just after ``start`` to just before ``end`` around
    ``center``; the end points themselves come from the text."""
    b0, b1 = _bearing(center, start), _bearing(center, end)
    sweep = (b1 - b0) % 360 if clockwise else -((b0 - b1) % 360)
    steps = max(1, int(abs(sweep) / ARC_STEP_DEG))
    return [
        _destination(*center, b0 + sweep * i / steps, nm) for i in range(1, steps)
    ]


def geometry(text: str) -> tuple[list | None, bool]:
    """Outer ring [[lon, lat], ...] for one description, and whether it had
    to be approximated (borders, shorelines, exclusions)."""
    approx = bool(re.search(r"\b(along the|excluding|except)\b", text, re.I)) and not re.search(
        r"along\s+the\s+arc", text, re.I
    )
    if re.search(r"excluding|except", text, re.I):
        approx = True
    if "tangent to circles" in text:
        r = re.search(r"([\d.]+)\s+miles?\s+radius\s+centred\s+on", text)
        centres = [coord(c) for c in _COORD.finditer(text)]
        if not r or not centres:
            return None, True
        pts = [p for c in centres for p in circle(c, float(r.group(1)))[:-1]]
        hull = _convex_hull(pts)
        return hull + [hull[0]], approx
    if "beginning at" not in text:
        m = _CIRCLE.search(text)
        if not m:
            return None, approx
        return circle(coord(m, 2), float(m.group(1))), approx

    body = text.split("beginning at", 1)[1]
    end = body.find("point of beginning")
    if end < 0:
        return None, True
    body = body[:end]
    ring: list = []
    pos = 0
    while True:
        a = _ARC.search(body, pos)
        c = _COORD.search(body, pos)
        if c is None and a is None:
            break
        if a is not None and (c is None or a.start() <= c.start()):
            center = coord(a, 3)
            nxt = _COORD.search(body, a.end())
            target = coord(nxt) if nxt else (ring[0] if ring else None)
            if ring and target:
                ring.extend(arc(center, float(a.group(2)), ring[-1], target, a.group(1) == "clockwise"))
            pos = a.end()
            continue
        ring.append(coord(c))
        pos = c.end()
    if len(ring) < 3:
        return None, True
    ring.append(ring[0])
    return ring, approx


def _convex_hull(points):
    """Monotone chain convex hull, counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _ft(s: str) -> int:
    return int(s.replace(",", ""))


def limits_cz(text: str) -> tuple[dict, dict]:
    """Vertical limits from 'The airspace [from X] to [below] Y ...'."""
    head = text.split("within", 1)[0]
    m = re.search(r"from\s+" + _FT + r"\s*(AGL)?\s+to\s+(below\s+)?" + _FT + r"\s*(AGL)?", head)
    if m:
        lower = {"ft": _ft(m.group(1)), "ref": "AGL" if m.group(2) else "MSL"}
        upper = {"ft": _ft(m.group(4)), "ref": "AGL" if m.group(5) else "MSL"}
        return lower, upper
    m = re.search(r"to\s+(below\s+)?" + _FT + r"\s*(AGL)?", head)
    if m:
        return {"ref": "SFC"}, {"ft": _ft(m.group(2)), "ref": "AGL" if m.group(3) else "MSL"}
    return {"ref": "SFC"}, {"ref": "UNL"}


def limits_f(designations: list[str]) -> tuple[dict, dict]:
    """Lowest floor and highest ceiling of 'Surface to 12,000´',
    'Above 12,000´ to FL180' and the like."""

    def one(s):
        s = s.replace("Unlimited", "UNL")
        lo_s, _, hi_s = s.partition(" to ")

        def parse(v, is_upper):
            v = v.strip()
            if re.match(r"(?i)surface|sfc", v):
                return {"ref": "SFC"}
            if re.match(r"(?i)unl", v):
                return {"ref": "UNL"}
            fl = re.search(r"FL\s*(\d{2,3})", v)
            if fl:
                return {"ft": int(fl.group(1)) * 100, "ref": "MSL"}
            ft = re.search(_FT + r"\s*(AGL)?", v)
            if ft:
                return {"ft": _ft(ft.group(1)), "ref": "AGL" if ft.group(2) else "MSL"}
            return {"ref": "UNL"} if is_upper else {"ref": "SFC"}

        return parse(lo_s, False), parse(hi_s, True)

    pairs = [one(d) for d in designations] or [({"ref": "SFC"}, {"ref": "UNL"})]
    lower = min((p[0] for p in pairs), key=lambda l: -1 if l["ref"] == "SFC" else l.get("ft", 0))
    upper = max((p[1] for p in pairs), key=lambda u: 1e9 if u["ref"] == "UNL" else u.get("ft", 0))
    return lower, upper


def clean_lines(text: str) -> list[str]:
    return [l.strip() for l in text.split("\n") if l.strip() and not _JUNK.search(l)]


def _rounded(ring):
    out = []
    for lon, lat in ring:
        p = [round(lon, PRECISION), round(lat, PRECISION)]
        if not out or out[-1] != p:
            out.append(p)
    if out[0] != out[-1]:
        out.append(out[0])
    return out


_PARA = re.compile(r"^3\.\d+\.\d+-\d+\s+(.*)$")


def control_zones(lines: list[str]) -> list[dict]:
    """All control zones in Part 3 (sections 3.x.4 or 3.7.6)."""
    out = []
    in_cz = False
    cls = None
    name = None
    para: list[str] = []

    def flush():
        nonlocal para
        if name and para:
            text = " ".join(para)
            if "airspace" in text:
                ring, approx = geometry(text)
                if ring:
                    lower, upper = limits_cz(text)
                    out.append(
                        {
                            "cls": cls or "E",
                            "kind": "CZ",
                            "name": name,
                            "ident": "",
                            "lower": lower,
                            "upper": upper,
                            "polygons": [[_rounded(ring)]],
                            **({"approx": True} if approx else {}),
                        }
                    )
        para = []

    for line in lines:
        if re.match(r"^3\.\d+\.\d+\s+CONTROL ZONES", line):
            flush()
            in_cz, cls, name = True, None, None
            continue
        if re.match(r"^3\.\d+\.\d+\s+[A-Z ]+$", line) or re.match(r"^\d\.\s+[A-Z]", line):
            flush()
            in_cz = False
            continue
        if not in_cz:
            continue
        m = _PARA.match(line)
        if m is None and re.match(r"^3\.\d+\.\d+-\d+$", line):
            continue  # paragraph number printed on its own line
        if m is None and line.startswith("The airspace") and name:
            flush()
            para = [line]
            continue
        if m:
            flush()
            rest = m.group(1).strip()
            k = re.match(r"^Class ([B-E])\b", rest)
            if k:
                cls = k.group(1)
                continue
            if rest.endswith(":") and "airspace" not in rest:
                name = rest[:-1].strip()
                continue
            para = [rest]
        elif para:
            para.append(line)
    flush()
    return out


_AREA = re.compile(r"^(CY[RDA])(\d{3})(\([A-Z]\))?\s+(.+)$")


def class_f(lines: list[str]) -> list[dict]:
    """Danger, restricted and advisory areas from Part 5."""
    out = []
    started = False
    current = None

    def flush():
        nonlocal current
        if current is None:
            return
        text = " ".join(current["text"])
        ring, approx = geometry(text)
        if ring:
            lower, upper = limits_f(current["alt"])
            out.append(
                {
                    "cls": "F",
                    "kind": current["kind"],
                    "name": current["name"],
                    "ident": current["ident"],
                    "lower": lower,
                    "upper": upper,
                    "polygons": [[_rounded(ring)]],
                    "times": "; ".join(dict.fromkeys(current["times"])),
                    **({"approx": True} if approx else {}),
                }
            )
        current = None

    seen = set()
    for line in lines:
        if re.match(r"^5\.1\s+DANGER AREAS", line):
            started = True
        if not started:
            continue
        m = _AREA.match(line)
        if m and not line.endswith(".") and len(line) < 70:
            ident = m.group(1) + m.group(2) + (m.group(3) or "")
            if current is not None and current["ident"] == ident:
                continue  # running page header repeating the entry name
            flush()
            if ident in seen:
                continue
            seen.add(ident)
            current = {
                "ident": ident,
                "kind": m.group(1),
                "name": m.group(4).strip(),
                "text": [],
                "alt": [],
                "times": [],
                "field": None,
            }
            continue
        if current is None:
            continue
        f = re.match(r"^(Designated Altitude|Time of Designation|User|Controlling|Operating|Remarks)[^–-]*[–-]\s*(.*)$", line)
        if f:
            current["field"] = f.group(1)
            if f.group(1) == "Designated Altitude":
                current["alt"].append(f.group(2))
            elif f.group(1) == "Time of Designation":
                current["times"].append(f.group(2))
            continue
        if current["field"] is None:
            current["text"].append(line)
    flush()
    return out


def build(text_path: Path, out_path: Path) -> dict:
    lines = clean_lines(text_path.read_text(encoding="utf-8", errors="ignore"))
    eff = re.search(r"Effective 0901Z (\d+ \w+ \d{4})", text_path.read_text(encoding="utf-8", errors="ignore"))
    cz = control_zones(lines)
    f = class_f(lines)
    result = {
        "source": "NAV CANADA Designated Airspace Handbook",
        "effective": eff.group(1) if eff else None,
        "airspace": cz + f,
    }
    out_path.write_text(json.dumps(result, separators=(",", ":")))
    summary = {
        "controlZones": len(cz),
        "classF": {k: sum(1 for a in f if a["kind"] == k) for k in ("CYR", "CYD", "CYA")},
        "approx": sum(1 for a in cz + f if a.get("approx")),
    }
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    build(Path(sys.argv[1]), Path(sys.argv[2]))
