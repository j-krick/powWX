#!/usr/bin/env python
"""Entry point for the scheduled observation logger (GitHub Actions / local).

Captures station #58 (whose API only retains ~7 days) into the append-only
observation log so a verifiable record of the bottom station accrues over time.
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
    if summary["n_records"] == 0:
        print("WARNING: no observations logged this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
