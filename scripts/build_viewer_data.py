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
from powwx import verification as ver  # noqa: E402

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
# Data credits, each linked (Open-Meteo's licence requires the link; the
# POW-O-METER wording is the one its LICENSE-DATA.md asks for).
CREDITS = [
    {"text": "Weather data by Open-Meteo.com (CC BY 4.0)", "url": "https://open-meteo.com/"},
    {"text": "Climate normals: ERA5-Land reanalysis, Copernicus Climate Change Service (contains "
             "modified Copernicus information; neither the European Commission nor ECMWF is "
             "responsible for any use), via Open-Meteo.com",
     "url": "https://open-meteo.com/en/docs/historical-weather-api"},
    {"text": "Terrace homogenized temperature: ECCC AHCCD (Open Government Licence – Canada)",
     "url": "https://www.canada.ca/en/environment-climate-change/services/climate-change/"
            "science-research-data/climate-trends-variability/adjusted-homogenized-canadian-data.html"},
    {"text": "El Niño index (RONI): NOAA Climate Prediction Center",
     "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/"},
    {"text": "POW-O-METER data, Mount Remo Backcountry Society, CC BY 4.0",
     "url": "https://github.com/mount-remo-backcountry-society/powOmeter"},
    # MoTI's conditions (permission 2026-10-16): credit MoTI, and say it isn't
    # liable for accuracy (the footer disclaimer carries that line).
    {"text": "Station #58: BC Ministry of Transportation and Infrastructure (used with "
             "permission), via Avalanche Canada",
     "url": "https://avalanche.ca/"},
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

    try:
        past = build_past_forecast(models_cfg, locations, now)
    except Exception as exc:  # noqa: BLE001 - the past overlay is optional
        print(f"::warning title=past forecast::skipped ({exc})")
        past = {}
    for lid, per_var in past.items():
        for variable, node in per_var.items():
            live_node = forecast.get(lid, {}).get(variable)
            if not live_node or not live_node["times"]:
                continue
            # The current run takes over where its own series starts.
            keep = [i for i, t in enumerate(node["times"]) if t < live_node["times"][0]]
            if keep:
                live_node["past"] = {
                    "times": [node["times"][i] for i in keep],
                    "series": {m: [v[i] for i in keep] for m, v in node["series"].items()},
                }

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
        "past": {"days": ver.PAST_DAYS, "lead_hours": ver.PAST_LEAD_HOURS},
    }


def build_past_forecast(models_cfg: dict, locations: list[dict], now: datetime) -> dict:
    """Day-ahead forecasts for the past few days, per location/variable:
    ``{times, series}`` like the live block. Each hour comes from the newest live
    snapshot fetched at least PAST_LEAD_HOURS before it (see
    ``verification.day_ahead_forecast``). Best-effort: an empty dict on failure."""
    import pandas as pd

    PAST_FORECAST_DAYS, PAST_LEAD_HOURS = ver.PAST_DAYS, ver.PAST_LEAD_HOURS
    start = pd.Timestamp(now - timedelta(days=PAST_FORECAST_DAYS)).floor("h")
    end = pd.Timestamp(now)
    first_day = (start - timedelta(hours=PAST_LEAD_HOURS + 48)).strftime("%Y-%m-%d")
    files = [f for f in glob.glob(str(cfg.DATA_DIR / "forecasts" / "issued_date=*" / "run_*.parquet"))
             if Path(f).parent.name.split("=", 1)[1] >= first_day]
    if not files:
        return {}
    cols = ["location", "model", "issued_at", "valid_time", "variable", "value", "source"]
    df = pd.concat([pd.read_parquet(f, columns=cols) for f in files], ignore_index=True)
    df["issued_at"] = pd.to_datetime(df["issued_at"], utc=True)
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    df = df.drop_duplicates(subset=["location", "variable", "model", "issued_at", "valid_time"],
                            keep="last")

    out: dict = {}
    for loc in locations:
        for variable in models_cfg["variables"]:
            sub = df[(df["location"] == loc["id"]) & (df["variable"] == variable)]
            picked = ver.day_ahead_forecast(sub.drop(columns=["location", "variable"]),
                                            start=start, end=end, lead_hours=PAST_LEAD_HOURS)
            if picked.empty:
                continue
            times = sorted(picked["valid_time"].unique())
            idx = {t: i for i, t in enumerate(times)}
            series: dict = {}
            for mid, g in picked.groupby("model"):
                arr = [None] * len(times)
                for t, v in zip(g["valid_time"], g["value"]):
                    if pd.notna(v):
                        arr[idx[t]] = round(float(v), 2)
                series[mid] = arr
            out.setdefault(loc["id"], {})[variable] = {
                "times": [pd.Timestamp(t).strftime("%Y-%m-%dT%H:%MZ") for t in times],
                "series": series,
            }
    return out


def build_observations() -> dict:
    out: dict = {}
    records: list[dict] = []
    # Station #58 (bottom, 740 m) — Avalanche Canada API.
    try:
        records += obs.fetch_station_58(days=OBS_DAYS)
    except Exception as exc:  # noqa: BLE001 - obs panels are best-effort
        print(f"WARNING: station #58 fetch failed: {exc}", file=sys.stderr)
    # POW-O-METER (top, 1150 m) — powOmeter v1, already on the hour, trimmed to the
    # window. Snow depth from the current site only: depths aren't comparable
    # across the station move.
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=OBS_DAYS)
        pom = [r for r in obs.fetch_powometer_v1() if _to_utc(r["time"]) >= cutoff]
        depth = [r for r in pom if r["variable"] == "snow_depth"]
        current_site = max(depth, key=lambda r: r["time"])["site_id"] if depth else None
        records += [r for r in pom
                    if r["variable"] != "snow_depth" or r["site_id"] == current_site]
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

    # An R2 failure (e.g. an expired token) costs only the frame history, never
    # the whole page build (review F11).
    try:
        client = get_r2_client()
        bucket = os.environ["R2_BUCKET"]
        return wi.build_webcam_index(client=client, bucket=bucket, public_base_url=public)
    except Exception as exc:  # noqa: BLE001 - webcams are best-effort
        print(f"::warning title=webcams::R2 listing failed, live frames only ({exc})")
        return {"public_base_url": "", "cameras": wi.base_cameras()}


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
        "credits": CREDITS,
        "links": LINKS,
        "station_note": "Observations are provisional: raw sensor data, not quality-controlled.",
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
