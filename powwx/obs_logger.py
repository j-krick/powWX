"""Append-only observation logger (the "actuals" half of verification).

Why this exists: Avalanche Canada's station-#58 API only ever serves the last
~7 days of measurements — there is **no historical archive**. So, exactly like
forecast runs and webcam frames, an un-logged observation is lost forever. This
logger captures station #58 on a schedule and appends it to a Parquet log that
accrues a verifiable record going forward.

POW-O-METER (the top station) is logged too, from powOmeter's published v1
files, so verification never depends on a live third-party fetch. powOmeter
serves its full history every time, so only hours not already in the log are
appended. Temperature and humidity only: snow depth must stay split by site
(see :data:`POWOMETER_LOG_VARIABLES`), which this log's columns don't carry.

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
# POW-O-METER variables that can be pooled across the station's sites.
POWOMETER_LOG_VARIABLES = {"temperature_2m", "relative_humidity_2m"}


def log_observations(*, data_dir: Path | None = None, days: int = 7) -> dict:
    """Fetch station #58 (last ``days``) and POW-O-METER's new hours, and append
    one Parquet file. A source that fails is reported in ``failed`` and doesn't
    stop the other. Returns a summary dict."""
    data_dir = data_dir or cfg.DATA_DIR
    fetched_at = om.utcnow()
    fetched_iso = om._iso(fetched_at)

    records: list[dict] = []
    per_source: dict[str, int] = {}
    errors: dict[str, str] = {}

    try:
        recs = obs.fetch_station_58(days=days)
        for r in recs:
            r["source"] = "avalanche_canada"
        records += recs
        per_source["avalanche_canada"] = len(recs)
    except Exception as exc:  # noqa: BLE001 - keep the other source
        errors["avalanche_canada"] = f"{type(exc).__name__}: {exc}"

    try:
        seen = _logged_keys(data_dir, location="pow_o_meter")
        recs = [
            {"location": r["location"], "time": r["time"], "variable": r["variable"],
             "value": r["value"], "source": "powometer_v1"}
            for r in obs.fetch_powometer_v1()
            if r["variable"] in POWOMETER_LOG_VARIABLES and (r["time"], r["variable"]) not in seen
        ]
        records += recs
        per_source["powometer_v1"] = len(recs)
    except Exception as exc:  # noqa: BLE001 - keep the other source
        errors["powometer_v1"] = f"{type(exc).__name__}: {exc}"

    for r in records:
        r["fetched_at"] = fetched_iso
    path = write_observation_run(records, data_dir=data_dir, fetched_at=fetched_at)
    return {
        "fetched_at": fetched_iso,
        "n_records": len(records),
        "per_source": per_source,
        "failed": errors,
        "path": str(path) if path else None,
    }


def _logged_keys(data_dir: Path, *, location: str) -> set[tuple[str, str]]:
    """``(time, variable)`` pairs already in the observation log for ``location``."""
    return {(str(r["time"]), r["variable"])
            for variable in POWOMETER_LOG_VARIABLES
            for r in load_observation_log(data_dir, location=location, variable=variable)}


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
