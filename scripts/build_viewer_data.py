#!/usr/bin/env python
"""Assemble the viewer's JSON data files from the committed Parquet log, the
station observation API, and the R2 webcam listing.

Outputs (to viewer/data/):
  forecast.json      latest run, per location/variable aligned time series per model
  observations.json  recent observed series per location/variable
  webcams.json       ordered frame index per camera (+ public base url)
  meta.json          generated-at, run issued_at, attribution

Webcam listing needs R2 creds (+ R2_PUBLIC_BASE_URL); if absent it's skipped so
the build still works locally. Observations failures are non-fatal.
"""

from __future__ import annotations

import glob
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dateutil import parser as dtparser  # noqa: E402

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

from powwx import config as cfg  # noqa: E402
from powwx import observations as obs  # noqa: E402

FORECAST_DAYS = 7
PAST_HOURS = 12
OBS_DAYS = 14  # how much observation history to load (the viewer can pan back over it)

UNITS = {
    "temperature_2m": "°C",
    "relative_humidity_2m": "%",
    "precipitation": "mm",
    "snowfall": "cm",
    "snow_depth": "m",
    "wind_speed_10m": "km/h",
    "wind_gusts_10m": "km/h",
    "wind_direction_10m": "°",
    "freezing_level_height": "m",
}
# Cross-check link-outs (we link, never scrape).
LINKS = [
    {"label": "SpotWX", "url": "https://spotwx.com/"},
    {"label": "snow-forecast", "url": "https://www.snow-forecast.com/resorts/Shames-Mountain/6day/mid"},
    {"label": "meteoblue", "url": "https://www.meteoblue.com/en/weather/week/shames-mountain"},
    {"label": "Shames co-op snow report", "url": "https://mymountaincoop.ca/shames-mountain/our-mountain/snow-report/"},
]


def _to_utc(ts: str) -> datetime:
    dt = dtparser.parse(ts)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _iso_z(ts: str) -> str:
    return _to_utc(ts).strftime("%Y-%m-%dT%H:%MZ")


def latest_run_file(data_dir: Path) -> Path:
    runs = glob.glob(str(data_dir / "forecasts" / "issued_date=*" / "run_*.parquet"))
    if not runs:
        raise SystemExit("No forecast run files found — has the logger run yet?")
    return Path(max(runs))  # run_<UTC-stamp> sorts chronologically


def build_forecast(models_cfg: dict, locations: list[dict]) -> dict:
    import pandas as pd

    run_path = latest_run_file(cfg.DATA_DIR)
    df = pd.read_parquet(run_path)
    labels = {m["id"]: m["label"] for m in models_cfg["models"]}

    now = datetime.now(timezone.utc)
    lo = now - timedelta(hours=PAST_HOURS)
    hi = now + timedelta(days=FORECAST_DAYS)

    df = df[df["source"] == "live"].copy()
    df["vt"] = df["valid_time"].map(_to_utc)
    df = df[(df["vt"] >= lo) & (df["vt"] <= hi)]

    forecast: dict = {}
    for loc in locations:
        lid = loc["id"]
        sub_loc = df[df["location"] == lid]
        per_var: dict = {}
        for variable in models_cfg["variables"]:
            sv = sub_loc[sub_loc["variable"] == variable]
            if sv.empty:
                continue
            times = sorted(sv["vt"].unique())
            time_strs = [t.strftime("%Y-%m-%dT%H:%MZ") for t in times]
            idx = {t: i for i, t in enumerate(times)}
            series: dict = {}
            for mid, g in sv.groupby("model"):
                arr = [None] * len(times)
                for t, v in zip(g["vt"], g["value"]):
                    arr[idx[t]] = round(float(v), 2)
                series[mid] = arr
            per_var[variable] = {"times": time_strs, "series": series}
        forecast[lid] = per_var

    issued = df["issued_at"].iloc[0] if not df.empty else None
    return {
        "issued_at": _iso_z(issued) if issued else None,
        "units": UNITS,
        "models": [{"id": m["id"], "label": labels[m["id"]]} for m in models_cfg["models"]],
        "locations": {
            loc["id"]: {"label": loc["name"], "elevation_m": loc["elevation_m"], "role": loc["role"]}
            for loc in locations
        },
        "forecast": forecast,
    }


def build_observations() -> dict:
    out: dict = {}
    records: list[dict] = []
    # Station #58 (bottom, 740 m) — Avalanche Canada API.
    try:
        records += obs.fetch_station_58(days=OBS_DAYS)
    except Exception as exc:  # noqa: BLE001 - obs panels are best-effort
        print(f"WARNING: station #58 fetch failed: {exc}", file=sys.stderr)
    # POW-O-METER (top, 1150 m) — published Google-Sheet CSV, trimmed to the window.
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=OBS_DAYS)
        pom = obs.fetch_pow_o_meter_default()
        records += [r for r in pom if _to_utc(r["time"]) >= cutoff]
    except Exception as exc:  # noqa: BLE001 - obs panels are best-effort
        print(f"WARNING: POW-O-METER fetch failed: {exc}", file=sys.stderr)
    # Collect then sort ascending by time (sources may return newest-first).
    pairs: dict = {}
    for r in records:
        key = (r["location"], r["variable"])
        pairs.setdefault(key, []).append((_to_utc(r["time"]), r["value"]))
    for (loc_id, variable), series in pairs.items():
        series.sort(key=lambda p: p[0])
        loc = out.setdefault(loc_id, {})
        loc[variable] = {
            "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t, _ in series],
            "values": [v for _, v in series],
        }
    return out


def build_webcams() -> dict:
    from powwx import webcam_index as wi

    public = wi.public_base_url_from_env()
    import os

    if not public or not os.environ.get("R2_ACCOUNT_ID"):
        print("INFO: R2 not configured (R2_PUBLIC_BASE_URL / creds) — live frames "
              "only, no history.", file=sys.stderr)
        return {"public_base_url": "", "cameras": wi.base_cameras()}
    from powwx.storage import get_r2_client

    client = get_r2_client()
    bucket = os.environ["R2_BUCKET"]
    return wi.build_webcam_index(client=client, bucket=bucket, public_base_url=public)


def main() -> int:
    models_cfg = cfg.load_models()
    locations = cfg.load_locations()
    out_dir = cfg.REPO_ROOT / "viewer" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    forecast = build_forecast(models_cfg, locations)
    observations = build_observations()
    webcams = build_webcams()
    meta = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "latest_run_issued_at": forecast.get("issued_at"),
        "attribution": "Weather data by Open-Meteo.com (CC BY 4.0). "
                       "Station #58 by Avalanche Canada / BC MoTI.",
        "links": LINKS,
        "station_note": "Observations are uncontrolled sensor data; treat as approximate.",
    }

    for name, payload in [("forecast", forecast), ("observations", observations),
                          ("webcams", webcams), ("meta", meta)]:
        (out_dir / f"{name}.json").write_text(json.dumps(payload, separators=(",", ":")),
                                              encoding="utf-8")

    nframes = sum(c.get("count", 0) for c in webcams.get("cameras", {}).values())
    print(json.dumps({
        "forecast_locations": list(forecast["forecast"].keys()),
        "forecast_issued_at": forecast.get("issued_at"),
        "obs_locations": list(observations.keys()),
        "webcam_frames": nframes,
        "out_dir": str(out_dir),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
