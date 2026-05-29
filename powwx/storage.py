"""Storage helpers: append-only Parquet for forecasts, R2 for webcam frames.

Forecast log layout (committed to git by the scheduled job)::

    data/forecasts/issued_date=YYYY-MM-DD/run_<UTC-timestamp>.parquet

Each logging run writes ONE new file. Nothing is ever overwritten — preserving
every (issued_at, valid_time) pair is the whole basis of verification.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pandas as pd

FORECAST_COLUMNS = [
    "location",
    "model",
    "issued_at",
    "valid_time",
    "variable",
    "value",
    "source",
    "fetched_at",
]


def write_forecast_run(
    records: list[dict],
    *,
    data_dir: Path,
    fetched_at: datetime,
    tag: str = "run",
) -> Path | None:
    """Write one run's records to a new partitioned Parquet file.

    Returns the file path, or ``None`` if there were no records.
    """
    if not records:
        return None
    df = pd.DataFrame.from_records(records)
    # Stable column order; tolerate any missing optional columns.
    for col in FORECAST_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[FORECAST_COLUMNS]

    issued_date = fetched_at.strftime("%Y-%m-%d")
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    part_dir = data_dir / "forecasts" / f"issued_date={issued_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / f"{tag}_{stamp}.parquet"
    df.to_parquet(out, engine="pyarrow", index=False, compression="zstd")
    return out


# --------------------------------------------------------------------------- #
# Cloudflare R2 (S3-compatible) for webcam frames
# --------------------------------------------------------------------------- #

def get_r2_client():
    """Build a boto3 S3 client pointed at Cloudflare R2 from env vars.

    Required env: R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY.
    """
    import boto3

    account_id = _require_env("R2_ACCOUNT_ID")
    return boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=_require_env("R2_ACCESS_KEY_ID"),
        aws_secret_access_key=_require_env("R2_SECRET_ACCESS_KEY"),
        region_name="auto",
    )


def upload_bytes(client, *, bucket: str, key: str, data: bytes, content_type: str) -> None:
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val
