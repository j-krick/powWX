#!/usr/bin/env python
"""OFFLINE EXPERIMENT (not part of the deploy): sweep blend weighting schemes to
see whether anything beats inverse-MAE — especially whether we can win the bottom
station outright — under the SAME walk-forward, out-of-sample discipline as the
shipped blend. Prints a comparison; nothing is written.

Schemes: equal (bias-corrected mean), inverse-MAE (current), inv-MAE^2/^3,
softmax, top-N by skill, best-1 (dynamic model selection), and regime-aware
(weights keyed on a forecast-time-known regime: season / time-of-day / forecast
temperature bin).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from powwx import config as cfg  # noqa: E402
from powwx import obs_logger  # noqa: E402
from powwx import observations as obs  # noqa: E402
from powwx import verification as ver  # noqa: E402

WINDOW, MIN_TRAIN, FLOOR = 90, 30, 0.5
SEASON = ver._SEASON


def aligned_for(lid: str) -> pd.DataFrame:
    if lid == "pow_o_meter":
        recs = [r for r in obs.fetch_pow_o_meter_default() if r["variable"] == "temperature_2m"]
    else:
        recs = obs_logger.load_observation_log(cfg.DATA_DIR, location=lid, variable="temperature_2m")
    obs_df = ver.observations_frame(recs)
    fc = ver.load_forecast_log(cfg.DATA_DIR, variable="temperature_2m", location=lid)
    return ver.align_to_observations(fc, obs_df, tolerance=pd.Timedelta(minutes=30))


def _regime_col(df: pd.DataFrame, regime: str | None) -> pd.DataFrame:
    if regime is None:
        return df
    d = df.copy()
    if regime == "season":
        d["rg"] = d["valid_time"].dt.month.map(SEASON)
    elif regime == "tod":
        d["rg"] = d["valid_time"].dt.tz_convert(ver.LOCAL_TZ).dt.hour.map(ver._part_of_day)
    elif regime == "fcst_temp":
        # forecast value bin — known at forecast time (unlike observed temp).
        d["rg"] = pd.cut(d["value"], bins=ver.OBS_TEMP_BINS, labels=ver.OBS_TEMP_LABELS).astype(str)
    return d


def walk_forward(aligned, *, weight, regime=None, top_n=None, min_members=2):
    a = _regime_col(aligned, regime)
    keys = ["model", "lead_day"] + (["rg"] if regime else [])
    blocks = a["issued_at"].dt.tz_localize(None).dt.to_period("M")
    parts = []
    for period in blocks.drop_duplicates().sort_values():
        p_start = period.start_time.tz_localize("UTC")
        train = a[(a["valid_time"] < p_start) & (a["valid_time"] >= p_start - pd.Timedelta(days=WINDOW))]
        if train.empty:
            continue
        coef = (train.groupby(keys)
                .agg(bias=("error", "mean"), mae=("abs_error", "mean"), n=("error", "size"))
                .reset_index())
        coef = coef[coef["n"] >= MIN_TRAIN].copy()
        if coef.empty:
            continue
        coef["w"] = weight(coef["mae"].clip(lower=FLOOR))
        test = a[blocks.values == period]
        m = test.merge(coef[keys + ["bias", "w", "mae"]], on=keys, how="inner")
        if not m.empty:
            parts.append(m)
    if not parts:
        return None
    M = pd.concat(parts, ignore_index=True)
    inst = ["issued_at", "valid_time", "lead_day"]
    if top_n is not None:
        M["rk"] = M.groupby(inst)["mae"].rank(method="first")  # 1 = lowest MAE
        M = M[M["rk"] <= top_n]
    M["wc"] = M["w"] * (M["value"] - M["bias"])
    agg = (M.groupby(inst)
           .agg(wc=("wc", "sum"), w=("w", "sum"), observed=("observed", "first"), nm=("model", "size"))
           .reset_index())
    agg = agg[agg["nm"] >= min_members].copy()
    agg["pred"] = agg["wc"] / agg["w"]
    agg["ae"] = (agg["pred"] - agg["observed"]).abs()
    return agg


SCHEMES = [
    ("equal (bias-corr mean)", dict(weight=lambda m: pd.Series(1.0, index=m.index))),
    ("inverse-MAE (current)", dict(weight=lambda m: 1.0 / m)),
    ("inverse-MAE^2", dict(weight=lambda m: 1.0 / m**2)),
    ("inverse-MAE^3", dict(weight=lambda m: 1.0 / m**3)),
    ("softmax(tau=0.7)", dict(weight=lambda m: np.exp(-m / 0.7))),
    ("top-3 by skill, inv-MAE", dict(weight=lambda m: 1.0 / m, top_n=3)),
    ("best-1 (model selection)", dict(weight=lambda m: 1.0 / m, top_n=1, min_members=1)),
    ("regime=season, inv-MAE", dict(weight=lambda m: 1.0 / m, regime="season")),
    ("regime=time-of-day, inv-MAE", dict(weight=lambda m: 1.0 / m, regime="tod")),
    ("regime=fcst-temp, inv-MAE", dict(weight=lambda m: 1.0 / m, regime="fcst_temp")),
    ("regime=tod, inv-MAE^2", dict(weight=lambda m: 1.0 / m**2, regime="tod")),
    ("regime=tod, inv-MAE^3", dict(weight=lambda m: 1.0 / m**3, regime="tod")),
]

RAW_BEST = {"pow_o_meter": ("ICON", 1.83), "station_58": ("GEM RDPS", 1.38)}


def main() -> int:
    for lid in ["pow_o_meter", "station_58"]:
        a = aligned_for(lid)
        rawname, rawmae = RAW_BEST[lid]
        print(f"\n=== {lid} (best raw: {rawname} {rawmae}) ===")
        print(f"  {'scheme':30} {'MAE':>6} {'vs best raw':>12}   d1   d2   d3   d5   d7")
        for name, kw in SCHEMES:
            agg = walk_forward(a, **kw)
            if agg is None or agg.empty:
                print(f"  {name:30}  (no result)")
                continue
            mae = agg["ae"].mean()
            byld = agg.groupby("lead_day")["ae"].mean()
            d = {k: byld.get(k, float('nan')) for k in (1, 2, 3, 5, 7)}
            delta = (rawmae - mae) / rawmae * 100
            print(f"  {name:30} {mae:6.3f} {delta:+10.1f}%  "
                  + " ".join(f"{d[k]:4.2f}" for k in (1, 2, 3, 5, 7)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
