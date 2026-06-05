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

from powwx import blend as bl  # noqa: E402
from powwx import config as cfg  # noqa: E402
from powwx import freezing_level as fl  # noqa: E402
from powwx import obs_logger  # noqa: E402
from powwx import observations as obs  # noqa: E402
from powwx import verification as ver  # noqa: E402

# Variables verified in this cut. Temperature first; extend this list to fan out.
VARIABLES = ["temperature_2m"]
MIN_N = 20  # drop (model, lead_day) cells with fewer matched pairs than this
TOLERANCE_MINUTES = 30
FL_PRODUCT_DAYS = 150               # recent window of observed FL estimate to ship
FL_MAX_HALFWIDTH_M = 500            # drop estimates whose ±band is wider than this
                                    # (weak vertical gradient -> uselessly uncertain)
FL_MODELS = ["gfs_seamless", "icon_global"]  # the only models with freezing_level_height


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


def _blend_summary(overall, by_lead, best_raw) -> dict | None:
    """Compare the blend against the best single (raw) model, overall and by lead."""
    bo = overall[overall["model"] == bl.BLEND_ID]
    raw = overall[overall["model"] != bl.BLEND_ID]
    if bo.empty or raw.empty:
        return None
    b = bo.iloc[0]
    best_raw_overall = raw.iloc[0]  # overall is sorted by MAE ascending
    # Per-lead: blend MAE vs the best raw model's MAE at that lead.
    raw_best = {int(r["lead_day"]): r["mae"] for _, r in best_raw.iterrows()}
    blend_lead = by_lead[by_lead["model"] == bl.BLEND_ID].sort_values("lead_day")
    per_lead = []
    for _, r in blend_lead.iterrows():
        ld = int(r["lead_day"])
        per_lead.append({"lead_day": ld, "blend_mae": round(float(r["mae"]), 2),
                         "best_raw_mae": round(float(raw_best.get(ld, float("nan"))), 2)
                         if ld in raw_best else None})
    impr = (best_raw_overall["mae"] - b["mae"]) / best_raw_overall["mae"] * 100.0
    return {
        "overall": {"mae": round(float(b["mae"]), 2), "bias": round(float(b["bias"]), 2),
                    "rmse": round(float(b["rmse"]), 2), "n": int(b["n"])},
        "best_raw_model": best_raw_overall["model"],
        "best_raw_mae": round(float(best_raw_overall["mae"]), 2),
        "improvement_pct": round(float(impr), 1),
        "beats_best_raw": bool(b["mae"] < best_raw_overall["mae"]),
        "by_lead": per_lead,
    }


def _live_blend_series(fc, aligned, band_q) -> dict | None:
    """Forward blend for the latest live run: learn coefficients as of now from the
    matched pairs, apply to the most recent live forecast, and wrap each point in
    the per-lead uncertainty band. Returns times / values / lower / upper."""
    live = fc[fc["source"] == "live"]
    if live.empty or aligned.empty:
        return None
    latest = live["issued_at"].max()
    run = live[live["issued_at"] == latest].copy()
    lead = (run["valid_time"] - run["issued_at"]).dt.total_seconds() / 86400.0
    run["lead_day"] = lead.round().astype(int)
    coef = bl.learn_coefficients(aligned, asof=pd.Timestamp(datetime.now(timezone.utc)))
    series = bl.live_blend(run[["model", "valid_time", "value", "lead_day"]], coef)
    if series.empty:
        return None
    lower, upper = [], []
    for _, r in series.iterrows():
        q = band_q.get(int(r["lead_day"]))
        lower.append(round(float(r["value"] + q[0]), 2) if q else None)
        upper.append(round(float(r["value"] + q[1]), 2) if q else None)
    return {
        "issued_at": latest.strftime("%Y-%m-%dT%H:%MZ"),
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in series["valid_time"]],
        "values": [round(float(v), 2) for v in series["value"]],
        "lower": lower,
        "upper": upper,
    }


def _freezing_level_block(labels) -> tuple[dict | None, dict | None]:
    """Two-station observed freezing-level estimate: a recent interp-regime series
    (the product) and an interp-regime cross-check of model freezing_level_height
    against the estimate (a consistency check, NOT ground-truth verification)."""
    top = ver.observations_frame(_obs_records("pow_o_meter", "temperature_2m"))
    bottom = ver.observations_frame(_obs_records("station_58", "temperature_2m"))
    paired = fl.pair_stations(bottom, top)
    if paired.empty:
        return None, None
    est = fl.estimate_series(paired)
    interp = est[est["regime"] == "interp"]
    if interp.empty:
        return None, None

    # Product: recent reliable (interp) estimate points with their ±1σ band, after
    # dropping weak-gradient points whose band balloons past usefulness.
    cutoff = interp["time"].max() - pd.Timedelta(days=FL_PRODUCT_DAYS)
    recent = interp[(interp["time"] >= cutoff)
                    & ((interp["upper"] - interp["lower"]) / 2 <= FL_MAX_HALFWIDTH_M)
                    ].sort_values("time")
    if recent.empty:
        return None, None
    product = {
        "z_bottom": fl.Z_BOTTOM, "z_top": fl.Z_TOP,
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in recent["time"]],
        "h0": [round(float(v)) for v in recent["h0"]],
        "lower": [round(float(v)) for v in recent["lower"]],
        "upper": [round(float(v)) for v in recent["upper"]],
    }

    # Cross-check: day-ahead model freezing_level_height vs the observed estimate,
    # interp regime only. Both are "freezing level" but different quantities, so
    # this is corroboration, not accuracy scoring.
    crosscheck = None
    fc = ver.load_forecast_log(cfg.DATA_DIR, variable="freezing_level_height", location="pow_o_meter")
    if not fc.empty:
        fc = fc.copy()
        fc["lead_day"] = ((fc["valid_time"] - fc["issued_at"]).dt.total_seconds() / 86400.0).round()
        fc1 = fc[fc["lead_day"] == 1]
        obs_h0 = interp[["time", "h0"]].sort_values("time")
        rows = []
        for mid, g in fc1.groupby("model"):
            if mid not in FL_MODELS:
                continue
            m = pd.merge_asof(g.sort_values("valid_time"), obs_h0, left_on="valid_time",
                              right_on="time", direction="nearest",
                              tolerance=pd.Timedelta(minutes=TOLERANCE_MINUTES)).dropna(subset=["h0"])
            if len(m) < 30:
                continue
            err = m["value"] - m["h0"]
            rows.append({"model": mid, "label": labels.get(mid, mid),
                         "mae": round(float(err.abs().mean())), "bias": round(float(err.mean())),
                         "n": int(len(m))})
        if rows:
            crosscheck = {
                "lead_day": 1,
                "n_interp_hours": int(len(interp)),
                "period": {"start": interp["time"].min().strftime("%Y-%m-%dT%H:%MZ"),
                           "end": interp["time"].max().strftime("%Y-%m-%dT%H:%MZ")},
                "models": sorted(rows, key=lambda r: r["mae"]),
                "note": "Consistency cross-check (interpolation regime, day-ahead): model "
                        "freezing_level_height (a free-atmosphere column diagnostic) vs the "
                        "station-derived surface estimate — related but not identical "
                        "quantities, so this is corroboration, not ground-truth verification.",
            }
    return product, crosscheck


def build() -> tuple[dict, dict, dict]:
    models_cfg = cfg.load_models()
    locations = cfg.load_locations()
    labels = {m["id"]: m["label"] for m in models_cfg["models"]}

    result_locs: dict = {}
    blend_live_locs: dict = {}
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

            # powWX blend: walk-forward (out-of-sample) bias-corrected consensus,
            # added as a pseudo-model so it's ranked honestly against the raw models.
            blend_pairs = bl.walk_forward_blend(aligned)
            combined = (pd.concat([aligned, blend_pairs], ignore_index=True)
                        if not blend_pairs.empty else aligned)

            by_lead = ver.metrics_by_lead(combined, min_n=MIN_N)        # incl. blend
            overall = ver.overall_metrics(combined, min_n=MIN_N)        # incl. blend
            # best_by_lead stays RAW-only so the "which model leads" story is about
            # the source models, not the blend that's built from them.
            best = ver.best_by_lead(ver.metrics_by_lead(aligned, min_n=MIN_N))

            blend_summary = _blend_summary(overall, by_lead, best)
            band_q = bl.residual_quantiles(blend_pairs)
            if blend_summary is not None:
                blend_summary["band_level"] = bl.BAND_LEVEL
                blend_summary["band_coverage_oos"] = bl.band_coverage_oos(blend_pairs)

            # Conditional verification: does the ranking change by regime? Computed
            # day-ahead (lead day 1) so models with different horizons compare fairly.
            strat_df = ver.add_strata(aligned, variable=variable)
            strata: dict = {}
            for dim, label, order, only_var in ver.STRATA:
                if only_var is not None and variable != only_var:
                    continue
                sm = ver.stratified_metrics(strat_df, by=dim, min_n=MIN_N)
                if sm.empty:
                    continue
                present = [s for s in order if s in set(sm[dim])]
                by_model = {}
                for mid, g in sm.groupby("model"):
                    g = g.set_index(dim).reindex(present).reset_index()
                    rows = g.dropna(subset=["mae"])[[dim, "n", "mae", "bias", "rmse"]]
                    rows = rows.rename(columns={dim: "stratum"})
                    by_model[mid] = _round_records(rows, ["mae", "bias", "rmse"])
                sbest = ver.best_by_stratum(sm, by=dim).rename(columns={dim: "stratum"})
                strata[dim] = {
                    "label": label,
                    "order": present,
                    "lead_days": list(ver.STRAT_LEAD_DAYS),
                    "by_model": by_model,
                    "best": _round_records(sbest[["stratum", "model", "mae", "n"]], ["mae"]),
                    "flips": int(sbest["model"].nunique()) > 1,
                }

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
                "blend": blend_summary,
                "strata": strata,
            }

            live = _live_blend_series(fc, aligned, band_q)
            if live is not None:
                blend_live_locs.setdefault(lid, {})[variable] = live

        if per_var:
            result_locs[lid] = {
                "label": loc["name"],
                "elevation_m": loc["elevation_m"],
                "role": loc["role"],
                "obs_source": "POW-O-METER Google Sheet" if lid == "pow_o_meter"
                              else "Avalanche Canada station #58 (logged)",
                "variables": per_var,
            }

    fl_product, fl_crosscheck = _freezing_level_block(labels)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "tolerance_minutes": TOLERANCE_MINUTES,
        "min_n": MIN_N,
        "freezing_level": fl_crosscheck,
        "units": {"temperature_2m": "°C"},
        "var_labels": {"temperature_2m": "Temperature"},
        "models": ([{"id": m["id"], "label": labels[m["id"]]} for m in models_cfg["models"]]
                   + [{"id": bl.BLEND_ID, "label": bl.BLEND_LABEL}]),
        "locations": result_locs,
        "notes": (
            "Error = forecast − observed. MAE/bias/RMSE in the variable's units. "
            "Lead day = forecast valid time minus issue time, rounded to whole days "
            "(backfill rows are exact integer-day offsets). Cells with < "
            f"{MIN_N} matched pairs are dropped. The powWX blend is a bias-corrected, "
            "inverse-MAE-weighted consensus, validated walk-forward (out-of-sample)."
        ),
        "attribution": "Forecasts by Open-Meteo.com (CC BY 4.0). Actuals: POW-O-METER "
                       "(top) and Avalanche Canada / BC MoTI station #58 (bottom).",
    }
    blend_payload = {
        "generated_at": payload["generated_at"],
        "window_days": bl.WINDOW_DAYS,
        "units": {"temperature_2m": "°C"},
        "note": "powWX blend: per-model bias removed and inverse-MAE weighted, "
                f"coefficients from the trailing {bl.WINDOW_DAYS} days.",
        "locations": blend_live_locs,
    }
    fl_payload = {
        "generated_at": payload["generated_at"],
        "units": "m",
        "t_sigma_c": fl.T_SIGMA,
        "note": "Observed freezing level estimated from the two stations' temperatures "
                "(lapse-rate extrapolation), interpolation regime only (0 °C between "
                f"{int(fl.Z_BOTTOM)} and {int(fl.Z_TOP)} m). Band is ±1σ. Top station is "
                "off in summer, so this is a winter/spring product.",
        "estimate": fl_product,
    }
    return payload, blend_payload, fl_payload


def main() -> int:
    out_dir = cfg.REPO_ROOT / "viewer" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload, blend_payload, fl_payload = build()
    (out_dir / "verification.json").write_text(
        json.dumps(payload, separators=(",", ":")), encoding="utf-8"
    )
    (out_dir / "blend.json").write_text(
        json.dumps(blend_payload, separators=(",", ":")), encoding="utf-8"
    )
    (out_dir / "freezing_level.json").write_text(
        json.dumps(fl_payload, separators=(",", ":")), encoding="utf-8"
    )

    summary = {
        lid: {
            v: {"n_pairs": d["n_pairs"], "best_overall": d["overall"][0]["model"] if d["overall"] else None,
                "blend": (f"{d['blend']['overall']['mae']} vs best raw {d['blend']['best_raw_mae']} "
                          f"({'+' if d['blend']['improvement_pct'] >= 0 else ''}{d['blend']['improvement_pct']}%)")
                if d.get("blend") else None}
            for v, d in loc["variables"].items()
        }
        for lid, loc in payload["locations"].items()
    }
    print(json.dumps({"out": str(out_dir / "verification.json"), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
