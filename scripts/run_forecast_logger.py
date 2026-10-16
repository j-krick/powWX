#!/usr/bin/env python
"""Entry point for the scheduled forecast logger (GitHub Actions / local)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.forecast_logger import log_forecasts  # noqa: E402


def main() -> int:
    summary = log_forecasts()
    print(json.dumps(summary, indent=2))
    # A partial run is still saved (and committed); flag it as an Actions
    # warning annotation rather than failing, so the good data isn't discarded.
    for loc_id, err in summary["failed_locations"].items():
        print(f"::warning title=forecast logger::{loc_id} not logged this run ({err})")
    # A model with no values is likely renamed or dropped by Open-Meteo; check
    # with scripts/verify_models.py. Warn, don't fail: failing would skip the commit.
    for loc_id, models in summary["missing_models"].items():
        what = "any model" if summary["per_location"][loc_id] == 0 else ", ".join(models)
        print(f"::warning title=forecast logger::{loc_id}: no values for {what}")
    if summary["n_records"] == 0:
        print("WARNING: no records logged this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
