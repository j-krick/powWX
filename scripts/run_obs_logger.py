#!/usr/bin/env python
"""Entry point for the scheduled observation logger (GitHub Actions / local).

Captures station #58 (whose API only retains ~7 days) and new POW-O-METER hours
(from powOmeter v1) into the append-only observation log, so verification never
depends on a live fetch.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.obs_logger import log_observations  # noqa: E402


def main() -> int:
    summary = log_observations()
    print(json.dumps(summary, indent=2))
    for source, err in summary["failed"].items():
        print(f"::warning title=obs logger::{source} not logged this run ({err})")
    if summary["n_records"] == 0:
        print("WARNING: no observations logged this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
