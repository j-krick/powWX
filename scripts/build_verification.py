#!/usr/bin/env python
"""Compute forecast-verification metrics and write viewer/data/verification.json.

Joins the committed forecast log to observed actuals and reports, per location,
which model has the lowest error and how error grows with lead time.

Actuals for both stations come from the append-only observation log, written by
run_obs_logger.py:
  - POW-O-METER (top): powOmeter's published v1 hourly series (history back to
    2025-01-26), so the 2024->now forecast backfill is verifiable.
  - Station #58 (bottom): Avalanche Canada (keeps only ~7 days) plus the PCDS import.

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
from powwx import verification as ver  # noqa: E402

# Variables verified in this cut. Temperature first; extend this list to fan out.
VARIABLES = ["temperature_2m"]
MIN_N = 20  # drop (model, lead_day) cells with fewer matched pairs than this
TOLERANCE_MINUTES = 30
FL_PRODUCT_DAYS = 150               # recent window of observed FL estimate to ship
FL_MAX_HALFWIDTH_M = 500            # drop estimates whose ±band is wider than this
                                    # (weak vertical gradient -> uselessly uncertain)
# Hard caps on how far we extrapolate beyond the stations. The ±band only models
# measurement noise, not the lapse rate changing with height, so a long
# extrapolation can pass the band filter yet still be shaky — cap it independently.
FL_EXTRAP_ABOVE_M = 700             # max metres above the top station (1150 m)
FL_EXTRAP_BELOW_M = 500             # max metres below the bottom station (740 m)
FL_MODELS = ["gfs_seamless", "icon_global"]  # the only models with freezing_level_height


def _obs_records(location: str, variable: str) -> list[dict]:
    """Observation records for one station+variable, from the append-only log.

    Both stations are on the hour there: station #58 natively, POW-O-METER because
    powOmeter interpolates its ~67-min readings onto the hour before publishing.
    """
    return obs_logger.load_observation_log(cfg.DATA_DIR, location=location, variable=variable)


def _round_records(df, cols, nd=2) -> list[dict]:
    out = df.copy()
    for c in cols:
        out[c] = out[c].round(nd)
    for c in ("n", "lead_day"):
        if c in out.columns:
            out[c] = out[c].astype(int)
    return out.to_dict("records")


def _comparison(c: dict) -> dict:
    """Round a paired comparison for the JSON; ``decisive`` = more than TIE_SE
    standard errors from zero, on enough independent days."""
    return {"blend_mae": round(c["mae_a"], 2), "best_raw_mae": round(c["mae_b"], 2),
            "improvement_pct": round(c["pct"], 1), "improvement_se": round(c["pct_se"], 1),
            "n": c["n"],
            "decisive": c["days"] >= ver.MIN_DAYS and abs(c["pct"]) > ver.TIE_SE * c["pct_se"]}


def _blend_summary(aligned, blend_pairs, overall, winners) -> dict | None:
    """Compare the blend with the most accurate single model *at each lead*, on
    the forecasts both made (never pooled across different horizons, review F2)."""
    bo = overall[overall["model"] == bl.BLEND_ID]
    if bo.empty or blend_pairs.empty:
        return None
    b = bo.iloc[0]
    per_lead, blend_parts, raw_parts = [], [], []
    for w in winners:
        ld = w["lead_day"]
        bl_ld = blend_pairs[blend_pairs["lead_day"] == ld]
        raw_ld = aligned[(aligned["lead_day"] == ld) & (aligned["model"] == w["model"])]
        c = ver.paired_comparison(bl_ld, raw_ld)
        if c is None or c["n"] < MIN_N:
            continue
        per_lead.append({"lead_day": ld, "best_raw_model": w["model"], **_comparison(c)})
        blend_parts.append(bl_ld)
        raw_parts.append(raw_ld)
    if not per_lead:
        return None
    # One headline over all those leads: each blend forecast against the best
    # single model for its own lead.
    pooled = ver.paired_comparison(pd.concat(blend_parts), pd.concat(raw_parts))
    return {
        "overall": {"mae": round(float(b["mae"]), 2), "bias": round(float(b["bias"]), 2),
                    "rmse": round(float(b["rmse"]), 2), "n": int(b["n"])},
        "vs_best_by_lead": {**_comparison(pooled),
                            "lead_days": [p["lead_day"] for p in per_lead]},
        "by_lead": per_lead,
    }


def _winner_record(w: dict, key: str, value) -> dict:
    """Round a :func:`ver.rank_with_ties` result for the JSON."""
    r2 = lambda v: None if v is None else round(float(v), 2)  # noqa: E731
    return {key: value, "model": w["model"], "mae": r2(w["mae"]), "n": w["n"],
            "decisive": w["decisive"], "tied": w["tied"], "runner_up": w["runner_up"],
            "margin": r2(w["margin"]), "margin_se": r2(w["margin_se"])}


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
    # Stop the line where the out-of-sample band ends: past the leads with a
    # verified error distribution, the blend has no evidence behind it (review F15).
    series = series[series["lead_day"].astype(int).isin(band_q.keys())]
    if series.empty:
        return None
    lower, upper = [], []
    for _, r in series.iterrows():
        q = band_q[int(r["lead_day"])]
        lower.append(round(float(r["value"] + q[0]), 2))
        upper.append(round(float(r["value"] + q[1]), 2))
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
    against the estimate (a consistency check, NOT ground-truth verification).

    Needs both stations; if either can't be read the block is skipped, so a missing
    station degrades the page instead of failing the build."""
    try:
        top = ver.observations_frame(_obs_records("pow_o_meter", "temperature_2m"))
        bottom = ver.observations_frame(_obs_records("station_58", "temperature_2m"))
    except Exception as exc:  # noqa: BLE001 - one station shouldn't sink the build
        print(f"WARNING: freezing-level estimate skipped: {exc}", file=sys.stderr)
        return None, None
    paired = fl.pair_stations(bottom, top)
    if paired.empty:
        return None, None
    est = fl.estimate_series(paired)
    interp = est[est["regime"] == "interp"]  # cross-check uses interp only

    # Product: interp (reliable) plus bounded above/below extrapolation. Two guards:
    # the ±band width AND a hard distance cap (the band doesn't see lapse-rate change
    # with height). Inversions can't be linearly extrapolated, so they stay gaps.
    halfwidth = (est["upper"] - est["lower"]) / 2
    shown = est[
        (halfwidth <= FL_MAX_HALFWIDTH_M)
        & (
            (est["regime"] == "interp")
            | ((est["regime"] == "above") & (est["h0"] <= fl.Z_TOP + FL_EXTRAP_ABOVE_M))
            | ((est["regime"] == "below") & (est["h0"] >= fl.Z_BOTTOM - FL_EXTRAP_BELOW_M))
        )
    ]
    if shown.empty:
        return None, (None if interp.empty else _fl_crosscheck(interp, labels))

    cutoff = shown["time"].max() - pd.Timedelta(days=FL_PRODUCT_DAYS)
    recent = shown[shown["time"] >= cutoff].sort_values("time").copy()
    # Even perfectly time-aligned, H₀ amplifies temperature noise (it divides by the
    # small top–bottom gradient). Apply a gentle 3 h rolling median, but only WITHIN
    # contiguous hourly runs so it never smooths across the gaps between episodes.
    run = (recent["time"].diff() > pd.Timedelta(hours=1, minutes=1)).cumsum()
    for col in ("h0", "lower", "upper"):
        recent[col] = recent.groupby(run)[col].transform(
            lambda x: x.rolling(3, center=True, min_periods=1).median())
    product = {
        "z_bottom": fl.Z_BOTTOM, "z_top": fl.Z_TOP,
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in recent["time"]],
        "h0": [round(float(v)) for v in recent["h0"]],
        "lower": [round(float(v)) for v in recent["lower"]],
        "upper": [round(float(v)) for v in recent["upper"]],
        "regime": list(recent["regime"]),   # 'interp' solid vs 'above'/'below' dashed
    }

    crosscheck = None if interp.empty else _fl_crosscheck(interp, labels)
    return product, crosscheck


def _fl_crosscheck(interp, labels) -> dict | None:
    """Day-ahead model freezing_level_height vs the observed estimate, interp regime
    only. Both are 'freezing level' but different quantities — corroboration, not
    accuracy scoring."""
    fc = ver.load_forecast_log(cfg.DATA_DIR, variable="freezing_level_height", location="pow_o_meter")
    if fc.empty:
        return None
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
    if not rows:
        return None
    return {
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
            # Winners are ranked per lead day, RAW models only, so the "which model
            # leads" story is about the source models, not the blend built from them.
            winners = ver.winners_by_lead(aligned, min_n=MIN_N)

            blend_summary = _blend_summary(aligned, blend_pairs, overall, winners)
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
                fixed_lead = strat_df[strat_df["lead_day"].isin(ver.STRAT_LEAD_DAYS)]
                best = []
                for s in present:
                    w = ver.rank_with_ties(fixed_lead[fixed_lead[dim] == s], min_n=MIN_N)
                    if w is not None:
                        best.append(_winner_record(w, "stratum", s))
                decisive = {b["model"] for b in best if b["decisive"]}
                strata[dim] = {
                    "label": label,
                    "order": present,
                    "lead_days": list(ver.STRAT_LEAD_DAYS),
                    "by_model": by_model,
                    "best": best,
                    # Only a flip if different models win clearly in different bins.
                    "flips": len(decisive) > 1,
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
                "by_lead": by_lead_models,
                "winners_by_lead": [_winner_record(w, "lead_day", w["lead_day"]) for w in winners],
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
                "obs_source": ("POW-O-METER via powOmeter v1 (logged). Readings are "
                               "interpolated onto the hour, which lowers every model's "
                               "error here by a few per cent. Since 2026-10-16 the top "
                               "station uses powOmeter's corrected UTC times; earlier "
                               "top-station numbers, from the Google Sheet, are superseded")
                              if lid == "pow_o_meter"
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
            "Lead day = forecast valid time minus issue time, rounded to whole days; "
            "hours already past when a "
            "live run was fetched are excluded, so day 0 is same-day forecasts only. "
            "Cells with < "
            f"{MIN_N} matched pairs are dropped. Models are ranked per lead day, never "
            "pooled across horizons, on the forecasts both made for the same hours; "
            "uncertainty comes from resampling whole days, and a model is named most "
            f"accurate only if it leads by more than {ver.TIE_SE:g} standard errors, "
            "otherwise it's a tie. The powWX blend is a bias-corrected, "
            "inverse-MAE-weighted consensus, validated walk-forward (out-of-sample). "
            "Scores before 2026-05-29 come from Open-Meteo's archive of past runs, "
            "where day N is the newest run started at least N days before the hour "
            "(24-29 h ahead for day 1). Live forecasts are already 5-12 h old when "
            "fetched (runs take hours to publish), so archive-era scores at a given "
            "lead day are slightly flattering, for every model alike."
        ),
        "attribution": "Forecasts by Open-Meteo.com (CC BY 4.0). Actuals: POW-O-METER data, "
                       "Mount Remo Backcountry Society, CC BY 4.0 (top) and BC Ministry of "
                       "Transportation and Infrastructure station #58, used with permission, "
                       "via Avalanche Canada (bottom); MoTI is not liable for the accuracy "
                       "of its data.",
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
                "with a straight-line lapse rate. Regime 'interp' (0 °C between "
                f"{int(fl.Z_BOTTOM)} and {int(fl.Z_TOP)} m) is interpolation; 'above' and "
                f"'below' are extrapolated up to {FL_EXTRAP_ABOVE_M} m above the top station "
                f"or {FL_EXTRAP_BELOW_M} m below the bottom one, a rougher guess (drawn "
                "dashed). Band is ±1σ of measurement noise only; it does not include "
                "the lapse rate changing with height.",
        "regime_counts": ({r: fl_product["regime"].count(r) for r in ("interp", "above", "below")}
                          if fl_product else None),
        "estimate": fl_product,
    }
    return payload, blend_payload, fl_payload


def main(out_dir: Path | None = None) -> int:
    out_dir = out_dir or cfg.REPO_ROOT / "viewer" / "data"
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
            v: {"n_pairs": d["n_pairs"],
                "day_ahead": next((f"{w['model']}{'' if w['decisive'] else ' (tie: ' + ', '.join(w['tied'][1:]) + ')'}"
                                   for w in d["winners_by_lead"] if w["lead_day"] == 1), None),
                "blend_vs_best_by_lead": (
                    f"{d['blend']['vs_best_by_lead']['improvement_pct']:+}% "
                    f"± {d['blend']['vs_best_by_lead']['improvement_se']}")
                if d.get("blend") else None}
            for v, d in loc["variables"].items()
        }
        for lid, loc in payload["locations"].items()
    }
    print(json.dumps({"out": str(out_dir / "verification.json"), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
