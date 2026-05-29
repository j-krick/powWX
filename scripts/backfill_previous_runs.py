#!/usr/bin/env python
"""Backfill verification history from Open-Meteo's Previous Runs API.

Walks a date range in monthly chunks (one Parquet file per month), pulling the
1-7 day lead-time offsets for every location/model. Archive coverage starts
~Jan 2024 (GFS 2m-temp back to Mar 2021); earlier/unavailable data just comes
back null and is dropped.

Usage:
    python scripts/backfill_previous_runs.py                       # 2024-01-01 -> today
    python scripts/backfill_previous_runs.py --start 2024-06-01 --end 2024-08-31
    python scripts/backfill_previous_runs.py --past-days 7          # relative fallback
"""

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dateutil.relativedelta import relativedelta  # noqa: E402

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx.previous_runs import backfill  # noqa: E402

ARCHIVE_START = "2024-01-01"


def _months(start: date, end: date):
    """Yield (chunk_start, chunk_end) month-aligned windows covering [start, end]."""
    cur = start
    while cur <= end:
        nxt = (cur.replace(day=1) + relativedelta(months=1))
        chunk_end = min(nxt - relativedelta(days=1), end)
        yield cur, chunk_end
        cur = nxt


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default=ARCHIVE_START, help="YYYY-MM-DD (default 2024-01-01)")
    ap.add_argument("--end", default=None, help="YYYY-MM-DD (default: today UTC)")
    ap.add_argument("--past-days", type=int, default=None,
                    help="Relative mode: ignore dates, pull last N days instead.")
    ap.add_argument("--forecast-days", type=int, default=2)
    args = ap.parse_args()

    if args.past_days is not None:
        summary = backfill(past_days=args.past_days, forecast_days=args.forecast_days)
        print(json.dumps(summary, indent=2))
        return 0 if summary["n_records"] else 1

    start = datetime.strptime(args.start, "%Y-%m-%d").date()
    end = (datetime.strptime(args.end, "%Y-%m-%d").date()
           if args.end else datetime.now(timezone.utc).date())

    total_rows = 0
    failed: list[str] = []
    chunks = list(_months(start, end))
    print(f"Backfilling {start} -> {end} in {len(chunks)} monthly chunks...", flush=True)
    for cs, ce in chunks:
        # One chunk failing (e.g. a slow API request exhausting retries) must not
        # abandon the whole multi-month backfill — log it and carry on.
        try:
            s = backfill(start_date=cs.isoformat(), end_date=ce.isoformat())
            total_rows += s["n_records"]
            print(f"  {cs} .. {ce}: {s['n_records']:>8,} rows -> "
                  f"{Path(s['path']).name if s['path'] else '(empty)'}", flush=True)
        except Exception as exc:  # noqa: BLE001 - resilience over strictness here
            failed.append(f"{cs}..{ce}")
            print(f"  {cs} .. {ce}: FAILED ({type(exc).__name__}: {exc})", flush=True)

    print(json.dumps({"start": str(start), "end": str(end), "chunks": len(chunks),
                      "total_rows": total_rows, "failed_chunks": failed}, indent=2))
    if failed:
        print(f"WARNING: {len(failed)} chunk(s) failed; re-run those date ranges "
              f"to fill the gaps: {failed}", file=sys.stderr)
    # Succeed if we got any data — committed chunks are kept; gaps can be re-run.
    return 0 if total_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
