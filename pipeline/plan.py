"""Decide what a workflow run builds; writes GitHub Actions outputs.

Usage: python -m pipeline.plan <charts selection> <force: true|false>
Reads existing release tags from the RELEASE_TAGS environment variable
(newline-separated).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys

from . import charts, cycle


def plan(selection: str, force: bool, existing_tags: set[str], today: dt.date):
    airac = cycle.current_airac(today)
    names = charts.parse_selection(selection)
    full = len(names) == len(charts.SECTIONALS)
    tag = f"airac-{airac.isoformat()}" + ("" if full else "-partial")
    build = force or not full or tag not in existing_tags
    return {
        "build": build,
        "tag": tag,
        "full": full,
        "date": today.isoformat(),
        "edition": cycle.faa_date(cycle.current_chart_edition(today)),
        "matrix": json.dumps({"chart": names}),
    }


if __name__ == "__main__":
    result = plan(
        sys.argv[1] if len(sys.argv) > 1 else "all",
        len(sys.argv) > 2 and sys.argv[2].lower() == "true",
        set(os.environ.get("RELEASE_TAGS", "").split()),
        dt.datetime.now(dt.timezone.utc).date(),
    )
    out = os.environ.get("GITHUB_OUTPUT")
    lines = [f"{k}={str(v).lower() if isinstance(v, bool) else v}" for k, v in result.items()]
    print("\n".join(lines))
    if out:
        with open(out, "a") as f:
            f.write("\n".join(lines) + "\n")
