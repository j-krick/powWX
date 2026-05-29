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
    if summary["n_records"] == 0:
        print("WARNING: no records logged this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
