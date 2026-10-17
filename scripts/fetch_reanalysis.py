#!/usr/bin/env python
"""Fetch ERA5-Land / ERA5 daily reanalysis for both stations into data/reanalysis/.

Two uses:
  - Full backfill, run LOCALLY (Open-Meteo throttles GitHub's runners):
        python scripts/fetch_reanalysis.py --start 1950-01-01
    (each model starts at its own first date if --start is earlier).
  - Weekly top-up in CI (reanalysis.yml): the last few months, which ERA5
    revises after first release:
        python scripts/fetch_reanalysis.py --recent 90

Each request is one chunk of at most --chunk-years years, written to its own
file, so a failure loses one chunk and a rerun can fill it in.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx import config as cfg  # noqa: E402
from powwx import openmeteo as om  # noqa: E402
from powwx import reanalysis as ra  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    when = ap.add_mutually_exclusive_group(required=True)
    when.add_argument("--start", help="first date (YYYY-MM-DD) for a backfill")
    when.add_argument("--recent", type=int, help="fetch only the last N days")
    ap.add_argument("--end", help="last date (default: today; days not yet available are skipped)")
    ap.add_argument("--models", default=",".join(ra.MODELS), help="comma-separated models")
    ap.add_argument("--chunk-years", type=int, default=10)
    ap.add_argument("--pause", type=float, default=10.0, help="seconds between requests")
    args = ap.parse_args(argv)

    end = args.end or date.today().isoformat()
    start = args.start or (date.today() - timedelta(days=args.recent)).isoformat()
    failed, written = [], []
    for model in args.models.split(","):
        first = max(start, ra.MODELS[model][0])
        for loc in cfg.load_locations():
            for a, b in ra.chunks(first, end, args.chunk_years):
                try:
                    recs = ra.fetch_daily(loc, model=model, start=a, end=b)
                    path = ra.write_fetch(recs, data_dir=cfg.DATA_DIR, fetched_at=om.utcnow())
                    written.append({"model": model, "location": loc["id"], "span": f"{a}..{b}",
                                    "rows": len(recs), "path": str(path) if path else None})
                    print(f"{model} {loc['id']} {a}..{b}: {len(recs)} rows", flush=True)
                except Exception as exc:  # noqa: BLE001 - keep the other chunks
                    failed.append(f"{model} {loc['id']} {a}..{b}: {type(exc).__name__}: {exc}")
                    print(f"::warning title=reanalysis::{failed[-1]}", flush=True)
                time.sleep(args.pause)
    print(json.dumps({"written": len(written), "failed": failed}, indent=2))
    return 1 if failed and not written else 0


if __name__ == "__main__":
    raise SystemExit(main())
