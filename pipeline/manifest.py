"""Assemble manifest.json from the per-file metadata in a build folder.

Usage: python -m pipeline.manifest <build dir> <release tag> <YYYY-MM-DD>

The app reads the manifest from the latest release:
https://github.com/<owner>/flightbag-data/releases/latest/download/manifest.json
Each file URL points at its own tagged release, so a manifest never refers
to files that a later release replaced.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import cycle

SCHEMA_VERSION = 1


def asset_url(repo: str, tag: str, file_name: str) -> str:
    return f"https://github.com/{repo}/releases/download/{tag}/{file_name}"


def build(build_dir: Path, tag: str, today: dt.date, repo: str) -> dict:
    edition = cycle.current_chart_edition(today)
    airac = cycle.current_airac(today)

    chart_entries = []
    for meta_file in sorted(build_dir.glob("*.json")):
        if meta_file.name.endswith(".meta.json") or meta_file.name == "manifest.json":
            continue
        meta = json.loads(meta_file.read_text())
        meta["url"] = asset_url(repo, tag, meta.pop("file"))
        meta["edition"] = edition.isoformat()
        meta["expires"] = cycle.next_chart_edition(today).isoformat()
        chart_entries.append(meta)
    chart_entries.sort(key=lambda c: c["name"])

    datasets = []
    for meta_file in sorted(build_dir.glob("*.meta.json")):
        meta = json.loads(meta_file.read_text())
        meta["url"] = asset_url(repo, tag, meta.pop("file"))
        meta["effective"] = airac.isoformat()
        meta["expires"] = cycle.next_airac(today).isoformat()
        datasets.append(meta)

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "release": tag,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "charts": chart_entries,
        "datasets": datasets,
    }
    (build_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    result = build(
        Path(sys.argv[1]),
        sys.argv[2],
        dt.date.fromisoformat(sys.argv[3]),
        os.environ.get("GITHUB_REPOSITORY", "StevonS/flightbag-data"),
    )
    print(json.dumps(result, indent=2))
