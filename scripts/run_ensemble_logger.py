#!/usr/bin/env python
"""Entry point for the scheduled ensemble logger (GitHub Actions / local)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.ensemble_logger import log_ensembles  # noqa: E402


def main() -> int:
    summary = log_ensembles()
    print(json.dumps(summary, indent=2))
    for key, err in summary["failed"].items():
        print(f"::warning title=ensemble logger::{key} not logged this run ({err})")
    if summary["n_records"] == 0:
        print("WARNING: no ensemble records logged this run.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
