#!/usr/bin/env python
"""Import PCIC/PCDS station CSV(s) into the append-only observation log.

The Pacific Climate Impacts Consortium PCDS portal
(https://services.pacificclimate.org/met-data-portal-pcds/app) serves the full
history of station #58 — Network Name ``MoTIe``, Native ID ``52401`` — which the
live Avalanche Canada API only keeps for ~7 days. Download the station CSV from
the portal, then run this to backfill the bottom-station actuals so the forecast
backfill becomes verifiable there too.

Usage:
  python scripts/import_pcds.py <path-to-52401.csv>
  python scripts/import_pcds.py <dir>     # imports every *.csv under <dir>
  python scripts/import_pcds.py           # defaults to ~/Downloads/pcds_data

Writes one Parquet file into data/observations/ (source = "pcds"); duplicates of
already-logged hours collapse harmlessly on read, so re-importing is safe.
"""

from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import config as cfg  # noqa: E402
from powwx import observations as obs  # noqa: E402
from powwx import obs_logger  # noqa: E402
from powwx import openmeteo as om  # noqa: E402

DEFAULT_DIR = Path(os.path.expanduser("~/Downloads/pcds_data"))


def _csv_paths(arg: str | None) -> list[Path]:
    target = Path(arg) if arg else DEFAULT_DIR
    if target.is_file():
        return [target]
    if target.is_dir():
        return [Path(p) for p in sorted(glob.glob(str(target / "**" / "*.csv"), recursive=True))]
    raise SystemExit(f"Not found: {target}")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    # variables.csv is metadata, not observations — skip it.
    paths = [p for p in _csv_paths(arg) if p.name.lower() != "variables.csv"]
    if not paths:
        raise SystemExit("No station CSVs found to import.")

    records: list[dict] = []
    per_file: dict[str, int] = {}
    for p in paths:
        recs = obs.parse_pcds_csv(p)
        per_file[str(p)] = len(recs)
        records.extend(recs)

    fetched_at = om.utcnow()
    fetched_iso = om._iso(fetched_at)
    for r in records:
        r["fetched_at"] = fetched_iso

    path = obs_logger.write_observation_run(
        records, data_dir=cfg.DATA_DIR, fetched_at=fetched_at
    )

    # Coverage summary by variable.
    by_var: dict[str, int] = {}
    for r in records:
        by_var[r["variable"]] = by_var.get(r["variable"], 0) + 1
    times = sorted(r["time"] for r in records)
    print(json.dumps({
        "files": per_file,
        "n_records": len(records),
        "by_variable": by_var,
        "time_range": [times[0], times[-1]] if times else None,
        "path": str(path) if path else None,
    }, indent=2))
    return 0 if records else 1


if __name__ == "__main__":
    raise SystemExit(main())
