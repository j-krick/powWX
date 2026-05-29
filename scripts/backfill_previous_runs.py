#!/usr/bin/env python
"""Backfill verification history from Open-Meteo's Previous Runs API.

Usage:
    python scripts/backfill_previous_runs.py [--past-days N] [--forecast-days N]

``--past-days`` is capped at ~92 per request by the API; run repeatedly to walk
further back in time.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.previous_runs import backfill  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--past-days", type=int, default=7)
    ap.add_argument("--forecast-days", type=int, default=2)
    args = ap.parse_args()

    summary = backfill(past_days=args.past_days, forecast_days=args.forecast_days)
    print(json.dumps(summary, indent=2))
    return 0 if summary["n_records"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
