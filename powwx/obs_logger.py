"""Append-only observation logger (the "actuals" half of verification).

Why this exists: Avalanche Canada's station-#58 API only ever serves the last
~7 days of measurements — there is **no historical archive**. So, exactly like
forecast runs and webcam frames, an un-logged observation is lost forever. This
logger captures station #58 on a schedule and appends it to a Parquet log that
accrues a verifiable record going forward.

POW-O-METER (the top station) is *not* logged here: its Google Sheet already is
a durable multi-year hub, so verification reads it directly from the sheet.

Layout (committed to git, mirrors the forecast log)::

    data/observations/obs_date=YYYY-MM-DD/obs_<UTC-timestamp>.parquet

Append-only: each run writes a new file. The 7-day fetch window overlaps prior
runs, producing duplicate ``(location, time, variable)`` rows; those are deduped
on read (see :func:`powwx.verification.observations_frame`), never overwritten.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import observations as obs
from . import openmeteo as om

OBS_COLUMNS = ["location", "time", "variable", "value", "source", "fetched_at"]


def log_observations(*, data_dir: Path | None = None, days: int = 7) -> dict:
    """Fetch the last ``days`` of station #58 obs and append one Parquet file.

    Returns a summary dict. Station #58 only; POW-O-METER stays in its sheet.
    """
    data_dir = data_dir or cfg.DATA_DIR
    fetched_at = om.utcnow()
    fetched_iso = om._iso(fetched_at)

    records = obs.fetch_station_58(days=days)
    for r in records:
        r["source"] = "avalanche_canada"
        r["fetched_at"] = fetched_iso

    path = write_observation_run(records, data_dir=data_dir, fetched_at=fetched_at)
    return {
        "fetched_at": fetched_iso,
        "n_records": len(records),
        "path": str(path) if path else None,
    }


def write_observation_run(
    records: list[dict], *, data_dir: Path, fetched_at: datetime
) -> Path | None:
    """Write one observation run to a new partitioned Parquet file."""
    if not records:
        return None
    import pandas as pd

    df = pd.DataFrame.from_records(records)
    for col in OBS_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[OBS_COLUMNS]

    obs_date = fetched_at.strftime("%Y-%m-%d")
    stamp = fetched_at.strftime("%Y%m%dT%H%M%SZ")
    part_dir = data_dir / "observations" / f"obs_date={obs_date}"
    part_dir.mkdir(parents=True, exist_ok=True)
    out = part_dir / f"obs_{stamp}.parquet"
    df.to_parquet(out, engine="pyarrow", index=False, compression="zstd")
    return out


def load_observation_log(
    data_dir: Path, *, location: str, variable: str
) -> list[dict]:
    """Read the committed observation log for one ``(location, variable)`` as
    long records ``{location, time, variable, value}`` (deduped downstream)."""
    import glob

    import pandas as pd

    files = sorted(
        glob.glob(str(data_dir / "observations" / "obs_date=*" / "*.parquet"))
    )
    records: list[dict] = []
    for f in files:
        df = pd.read_parquet(f, columns=["location", "time", "variable", "value"])
        df = df[(df["location"] == location) & (df["variable"] == variable)]
        records.extend(df.to_dict("records"))
    return records
