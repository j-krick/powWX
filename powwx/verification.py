"""Phase 2 — forecast verification.

Join the append-only forecast log to observed actuals and compute per-model
error metrics by lead time. This is the novel, decision-relevant part of powWX:
*which model is most trustworthy here, at which horizon, for which variable.*

The pieces are deliberately small and pure so they can be tested and reused:

- :func:`load_forecast_log` reads the committed Parquet log for one
  ``(location, variable)`` and returns a tidy frame with parsed UTC times.
- :func:`align_to_observations` joins each forecast value to the nearest
  observation in time (``merge_asof``, tolerance-bounded) and computes the
  signed error ``forecast - observed`` and the lead time.
- :func:`metrics_by_lead` and :func:`overall_metrics` aggregate MAE / bias /
  RMSE / n per model and lead day.

Time handling: the logger writes ``valid_time`` as naive UTC (Open-Meteo is
queried with ``timezone=GMT``) and ``issued_at`` / observation times as ISO
``...Z``. Everything here is parsed with ``utc=True`` so the join is in one
consistent UTC frame. Lead time is ``valid_time - issued_at`` — exact integer
days for backfill rows, continuous for live rows (see :mod:`powwx.openmeteo`).
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

# How close (in time) an observation must be to a forecast's valid_time to count
# as the actual for it. POW-O-METER transmits roughly hourly at irregular
# minutes and station #58 is on the hour, while forecasts are on the hour, so a
# 30-minute window matches one obs per forecast hour without double-counting.
DEFAULT_TOLERANCE = pd.Timedelta(minutes=30)


def load_forecast_log(
    data_dir: Path,
    *,
    variable: str,
    location: str,
    sources: list[str] | None = None,
) -> pd.DataFrame:
    """Read the committed forecast log for one ``(location, variable)``.

    Returns columns ``[model, issued_at, valid_time, value, source]`` with
    ``issued_at`` / ``valid_time`` as UTC-aware timestamps. ``sources`` optionally
    restricts to e.g. ``["live"]`` or ``["previous_runs"]``; default keeps both.
    """
    files = sorted(glob.glob(str(data_dir / "forecasts" / "issued_date=*" / "*.parquet")))
    if not files:
        return _empty_aligned_inputs()

    frames = []
    cols = ["location", "model", "issued_at", "valid_time", "variable", "value", "source"]
    for f in files:
        df = pd.read_parquet(f, columns=cols)
        df = df[(df["location"] == location) & (df["variable"] == variable)]
        if sources is not None:
            df = df[df["source"].isin(sources)]
        if not df.empty:
            frames.append(df)
    if not frames:
        return _empty_aligned_inputs()

    out = pd.concat(frames, ignore_index=True)
    out["issued_at"] = pd.to_datetime(out["issued_at"], utc=True)
    out["valid_time"] = pd.to_datetime(out["valid_time"], utc=True)
    out = out.drop(columns=["location", "variable"])
    # A given (model, issued_at, valid_time) can recur across overlapping live
    # runs and backfill chunks; keep the last write so each forecast counts once.
    out = out.drop_duplicates(
        subset=["model", "issued_at", "valid_time"], keep="last"
    ).reset_index(drop=True)
    return out


def _empty_aligned_inputs() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["model", "issued_at", "valid_time", "value", "source"]
    )


def observations_frame(records: list[dict]) -> pd.DataFrame:
    """Normalise long obs records ``{time, value, ...}`` to a sorted UTC frame
    ``[time, observed]`` for a single station+variable, ready for the asof join.

    Duplicate timestamps (re-logged obs) collapse to their mean.
    """
    if not records:
        return pd.DataFrame(columns=["time", "observed"])
    df = pd.DataFrame.from_records(records)
    df["time"] = pd.to_datetime(df["time"], utc=True)
    df = (
        df.groupby("time", as_index=False)["value"]
        .mean()
        .rename(columns={"value": "observed"})
        .sort_values("time")
        .reset_index(drop=True)
    )
    return df


def align_to_observations(
    forecasts: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    tolerance: pd.Timedelta = DEFAULT_TOLERANCE,
) -> pd.DataFrame:
    """Attach the nearest observation to each forecast and compute error + lead.

    Adds columns ``observed``, ``error`` (= forecast − observed),
    ``abs_error``, ``lead_hours`` and ``lead_day`` (lead rounded to the nearest
    whole day). Forecasts with no observation within ``tolerance`` are dropped —
    they cannot be verified.
    """
    if forecasts.empty or observations.empty:
        return pd.DataFrame(
            columns=[
                "model", "issued_at", "valid_time", "value", "source",
                "observed", "error", "abs_error", "lead_hours", "lead_day",
            ]
        )

    f = forecasts.sort_values("valid_time").reset_index(drop=True)
    o = observations.sort_values("time").reset_index(drop=True)

    merged = pd.merge_asof(
        f, o,
        left_on="valid_time", right_on="time",
        direction="nearest", tolerance=tolerance,
    )
    merged = merged.dropna(subset=["observed"]).copy()

    merged["error"] = merged["value"] - merged["observed"]
    merged["abs_error"] = merged["error"].abs()
    lead = merged["valid_time"] - merged["issued_at"]
    merged["lead_hours"] = lead.dt.total_seconds() / 3600.0
    # Round to whole days: backfill rows are exact integer-day offsets, and live
    # rows bin to the nearest day so the two stack on one lead-day axis.
    merged["lead_day"] = (merged["lead_hours"] / 24.0).round().astype(int)
    # Guard against clock skew / valid_time before issued_at on live rows.
    merged = merged[merged["lead_day"] >= 0]
    return merged.drop(columns=["time"]).reset_index(drop=True)


def _agg(group: pd.DataFrame) -> pd.Series:
    err = group["error"]
    return pd.Series(
        {
            "n": int(len(group)),
            "mae": float(group["abs_error"].mean()),
            "bias": float(err.mean()),
            "rmse": float((err.pow(2).mean()) ** 0.5),
        }
    )


def metrics_by_lead(aligned: pd.DataFrame, *, min_n: int = 20) -> pd.DataFrame:
    """MAE / bias / RMSE / n per ``(model, lead_day)``.

    Cells with fewer than ``min_n`` matched pairs are dropped — a metric over a
    handful of points is noise, not signal.
    """
    if aligned.empty:
        return pd.DataFrame(columns=["model", "lead_day", "n", "mae", "bias", "rmse"])
    out = (
        aligned.groupby(["model", "lead_day"], sort=True)
        .apply(_agg, include_groups=False)
        .reset_index()
    )
    out = out[out["n"] >= min_n].reset_index(drop=True)
    return out


def overall_metrics(aligned: pd.DataFrame, *, min_n: int = 20) -> pd.DataFrame:
    """MAE / bias / RMSE / n per model, pooled across all lead days."""
    if aligned.empty:
        return pd.DataFrame(columns=["model", "n", "mae", "bias", "rmse"])
    out = (
        aligned.groupby("model", sort=True)
        .apply(_agg, include_groups=False)
        .reset_index()
    )
    out = out[out["n"] >= min_n].sort_values("mae").reset_index(drop=True)
    return out


def best_by_lead(by_lead: pd.DataFrame) -> pd.DataFrame:
    """For each lead day, the model with the lowest MAE (the 'winner')."""
    if by_lead.empty:
        return pd.DataFrame(columns=["lead_day", "model", "mae", "n"])
    idx = by_lead.groupby("lead_day")["mae"].idxmin()
    return (
        by_lead.loc[idx, ["lead_day", "model", "mae", "n"]]
        .sort_values("lead_day")
        .reset_index(drop=True)
    )
