#!/usr/bin/env python
"""Compute forecast-verification metrics and write viewer/data/verification.json.

Joins the committed forecast log to observed actuals and reports, per location,
which model has the lowest error and how error grows with lead time.

Actuals by station:
  - POW-O-METER (top): read directly from its Google Sheet (durable history back
    to 2025-01-26), so the 2024->now forecast backfill is verifiable immediately.
  - Station #58 (bottom): read from the append-only observation log (AvCan's API
    keeps only ~7 days), which accrues going forward via run_obs_logger.py.

Phase 2a is temperature-first (the strongest signal, verifiable at both
elevations); other variables fan out from the same pipeline once this is solid.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

import pandas as pd  # noqa: E402

from powwx import config as cfg  # noqa: E402
from powwx import obs_logger  # noqa: E402
from powwx import observations as obs  # noqa: E402
from powwx import verification as ver  # noqa: E402

# Variables verified in this cut. Temperature first; extend this list to fan out.
VARIABLES = ["temperature_2m"]
MIN_N = 20  # drop (model, lead_day) cells with fewer matched pairs than this
TOLERANCE_MINUTES = 30


def _obs_records(location: str, variable: str) -> list[dict]:
    """Assemble observation records for one station+variable from its source."""
    if location == "pow_o_meter":
        recs = obs.fetch_pow_o_meter_default()
        return [r for r in recs if r["variable"] == variable]
    # Everything else: the append-only observation log (station #58 etc.).
    return obs_logger.load_observation_log(cfg.DATA_DIR, location=location, variable=variable)


def _round_records(df, cols, nd=2) -> list[dict]:
    out = df.copy()
    for c in cols:
        out[c] = out[c].round(nd)
    for c in ("n", "lead_day"):
        if c in out.columns:
            out[c] = out[c].astype(int)
    return out.to_dict("records")


def build() -> dict:
    models_cfg = cfg.load_models()
    locations = cfg.load_locations()
    labels = {m["id"]: m["label"] for m in models_cfg["models"]}

    result_locs: dict = {}
    for loc in locations:
        lid = loc["id"]
        per_var: dict = {}
        for variable in VARIABLES:
            try:
                recs = _obs_records(lid, variable)
            except Exception as exc:  # noqa: BLE001 - one station shouldn't sink the build
                print(f"WARNING: obs fetch failed for {lid}/{variable}: {exc}", file=sys.stderr)
                recs = []
            obs_df = ver.observations_frame(recs)
            fc = ver.load_forecast_log(cfg.DATA_DIR, variable=variable, location=lid)
            aligned = ver.align_to_observations(
                fc, obs_df, tolerance=pd.Timedelta(minutes=TOLERANCE_MINUTES)
            )
            if aligned.empty:
                print(f"INFO: no verifiable pairs for {lid}/{variable} "
                      f"(obs rows={len(obs_df)}, forecast rows={len(fc)})", file=sys.stderr)
                continue

            by_lead = ver.metrics_by_lead(aligned, min_n=MIN_N)
            overall = ver.overall_metrics(aligned, min_n=MIN_N)
            best = ver.best_by_lead(by_lead)

            # by_lead -> {model: [{lead_day, n, mae, bias, rmse}, ...]} for charting.
            by_lead_models: dict = {}
            for mid, g in by_lead.groupby("model"):
                g = g.sort_values("lead_day")
                by_lead_models[mid] = _round_records(
                    g[["lead_day", "n", "mae", "bias", "rmse"]], ["mae", "bias", "rmse"]
                )

            per_var[variable] = {
                "n_pairs": int(len(aligned)),
                "period": {
                    "start": aligned["valid_time"].min().strftime("%Y-%m-%dT%H:%MZ"),
                    "end": aligned["valid_time"].max().strftime("%Y-%m-%dT%H:%MZ"),
                },
                "max_lead_day": int(by_lead["lead_day"].max()) if not by_lead.empty else 0,
                "overall": _round_records(
                    overall[["model", "n", "mae", "bias", "rmse"]], ["mae", "bias", "rmse"]
                ),
                "by_lead": by_lead_models,
                "best_by_lead": _round_records(
                    best[["lead_day", "model", "mae", "n"]], ["mae"]
                ),
            }

        if per_var:
            result_locs[lid] = {
                "label": loc["name"],
                "elevation_m": loc["elevation_m"],
                "role": loc["role"],
                "obs_source": "POW-O-METER Google Sheet" if lid == "pow_o_meter"
                              else "Avalanche Canada station #58 (logged)",
                "variables": per_var,
            }

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "tolerance_minutes": TOLERANCE_MINUTES,
        "min_n": MIN_N,
        "units": {"temperature_2m": "°C"},
        "var_labels": {"temperature_2m": "Temperature"},
        "models": [{"id": m["id"], "label": labels[m["id"]]} for m in models_cfg["models"]],
        "locations": result_locs,
        "notes": (
            "Error = forecast − observed. MAE/bias/RMSE in the variable's units. "
            "Lead day = forecast valid time minus issue time, rounded to whole days "
            "(backfill rows are exact integer-day offsets). Cells with < "
            f"{MIN_N} matched pairs are dropped."
        ),
        "attribution": "Forecasts by Open-Meteo.com (CC BY 4.0). Actuals: POW-O-METER "
                       "(top) and Avalanche Canada / BC MoTI station #58 (bottom).",
    }


def main() -> int:
    out_dir = cfg.REPO_ROOT / "viewer" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = build()
    (out_dir / "verification.json").write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )

    summary = {
        lid: {
            v: {"n_pairs": d["n_pairs"], "best_overall": d["overall"][0]["model"] if d["overall"] else None}
            for v, d in loc["variables"].items()
        }
        for lid, loc in payload["locations"].items()
    }
    print(json.dumps({"out": str(out_dir / "verification.json"), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
