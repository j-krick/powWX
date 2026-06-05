"""powWX blend — a bias-corrected, skill-weighted multi-model temperature forecast,
with an out-of-sample uncertainty band.

Built on the verification findings: every model carries a measurable per-lead
bias (e.g. the 1–3 °C cold bias at the top station), and the models have
different, *time-of-day-dependent* skill. So the blend for a forecast valid at
*t* (issued at *t − lead*) is::

    blend(t) = Σ_m w_m · (forecast_m(t) − bias_m) / Σ_m w_m

where ``bias_m`` and ``w_m = 1 / MAE_m**POWER`` are learned per
``(model, lead_day, time-of-day)`` — a regime that is known at forecast time and
well sampled in any window (unlike season, which a 90-day window can't separate).
A sweep (scripts/exp_blend_weighting.py) picked this scheme: it beats the best
single model at *both* stations out-of-sample. Selection ("just use the best
model") was far worse — blending's error cancellation matters.

**Strictly causal.** Coefficients are learned only from matched pairs whose
observation was already available (``valid_time`` before the forecast's issue
time), within a trailing window. :func:`walk_forward_blend` retrains every month
on only prior data, so a blend pair is never informed by its own outcome.

The **uncertainty band** is the empirical distribution of the blend's own
out-of-sample errors per lead: an 80 % band runs from the 10th to the 90th
percentile of (blend − observed). It is verifiable — :func:`band_coverage_oos`
learns the quantiles on early data and checks coverage on later data.
"""

from __future__ import annotations

import pandas as pd

WINDOW_DAYS = 90      # trailing training window (adapts to drift)
MIN_TRAIN = 30        # min matched pairs per (model, lead_day, tod) to trust a coefficient
MAE_FLOOR = 0.5       # °C; floor so a fluke-small MAE can't dominate the weights
WEIGHT_POWER = 2.0    # w ∝ 1/MAE**POWER; 2 concentrates on skill without selection's fragility
MIN_MEMBERS = 2       # need ≥2 corrected models to form a blend value
BAND_LEVEL = 0.8      # central probability the band aims to cover
BLEND_ID = "powwx_blend"
BLEND_LABEL = "powWX blend"
LOCAL_TZ = "America/Vancouver"

# Coefficients are keyed on these. time-of-day is forecast-time-known and well
# sampled in any window; it captured the day/night skill flips we measured.
KEYS = ["model", "lead_day", "part_of_day"]
_BLEND_COLS = ["model", "issued_at", "valid_time", "value", "source",
               "observed", "error", "abs_error", "lead_day"]


def _empty_blend() -> pd.DataFrame:
    return pd.DataFrame(columns=_BLEND_COLS)


def _part_of_day(hour: int) -> str:
    if hour < 6:
        return "Night"
    if hour < 12:
        return "Morning"
    if hour < 18:
        return "Afternoon"
    return "Evening"


def _with_tod(df: pd.DataFrame) -> pd.DataFrame:
    """Attach the local (DST-aware) time-of-day bin from valid_time."""
    out = df.copy()
    out["part_of_day"] = out["valid_time"].dt.tz_convert(LOCAL_TZ).dt.hour.map(_part_of_day)
    return out


def learn_coefficients(
    aligned: pd.DataFrame, *, asof: pd.Timestamp,
    window_days: int = WINDOW_DAYS, min_train: int = MIN_TRAIN,
    mae_floor: float = MAE_FLOOR, power: float = WEIGHT_POWER,
) -> pd.DataFrame:
    """Per ``(model, lead_day, time-of-day)`` bias + weight from matched pairs whose
    observation was available before ``asof`` (``valid_time < asof``), within the
    trailing ``window_days``. Strictly causal. Returns ``KEYS + [bias, mae, n, weight]``."""
    cols = KEYS + ["bias", "mae", "n", "weight"]
    if aligned.empty:
        return pd.DataFrame(columns=cols)
    lo = asof - pd.Timedelta(days=window_days)
    train = aligned[(aligned["valid_time"] < asof) & (aligned["valid_time"] >= lo)]
    if train.empty:
        return pd.DataFrame(columns=cols)
    train = _with_tod(train)
    coef = (
        train.groupby(KEYS)
        .agg(bias=("error", "mean"), mae=("abs_error", "mean"), n=("error", "size"))
        .reset_index()
    )
    coef = coef[coef["n"] >= min_train].copy()
    coef["weight"] = 1.0 / coef["mae"].clip(lower=mae_floor) ** power
    return coef


def _combine(merged: pd.DataFrame, *, min_members: int) -> pd.DataFrame:
    """Weighted mean of bias-corrected members per instance ``(issued_at, valid_time, lead_day)``."""
    m = merged.copy()
    m["wc"] = m["weight"] * (m["value"] - m["bias"])
    agg = (
        m.groupby(["issued_at", "valid_time", "lead_day"])
        .agg(wc=("wc", "sum"), w=("weight", "sum"),
             observed=("observed", "first"), n_members=("model", "size"))
        .reset_index()
    )
    agg = agg[agg["n_members"] >= min_members].copy()
    agg["value"] = agg["wc"] / agg["w"]
    return agg


def walk_forward_blend(
    aligned: pd.DataFrame, *, window_days: int = WINDOW_DAYS,
    min_train: int = MIN_TRAIN, min_members: int = MIN_MEMBERS,
    mae_floor: float = MAE_FLOOR, power: float = WEIGHT_POWER,
) -> pd.DataFrame:
    """Out-of-sample blend: for each calendar month of *issue* dates, learn
    coefficients from the trailing window ending at the month's start, then blend
    every forecast issued that month. Returns blend pseudo-pairs (``powwx_blend``)."""
    if aligned.empty:
        return _empty_blend()
    a = _with_tod(aligned)
    blocks = a["issued_at"].dt.tz_localize(None).dt.to_period("M")
    merged_parts = []
    for period in blocks.drop_duplicates().sort_values():
        p_start = period.start_time.tz_localize("UTC")
        coef = learn_coefficients(aligned, asof=p_start, window_days=window_days,
                                  min_train=min_train, mae_floor=mae_floor, power=power)
        if coef.empty:
            continue
        test = a[blocks.values == period]
        m = test.merge(coef[KEYS + ["bias", "weight"]], on=KEYS, how="inner")
        if not m.empty:
            merged_parts.append(m)
    if not merged_parts:
        return _empty_blend()
    agg = _combine(pd.concat(merged_parts, ignore_index=True), min_members=min_members)
    if agg.empty:
        return _empty_blend()
    agg["model"] = BLEND_ID
    agg["source"] = "blend"
    agg["error"] = agg["value"] - agg["observed"]
    agg["abs_error"] = agg["error"].abs()
    return agg[_BLEND_COLS].reset_index(drop=True)


def live_blend(
    live_rows: pd.DataFrame, coef: pd.DataFrame, *, min_members: int = MIN_MEMBERS
) -> pd.DataFrame:
    """Apply learned ``coef`` to the latest run's per-model values. ``live_rows`` needs
    ``model, valid_time, value, lead_day``. Returns ``[valid_time, value, n_members, lead_day]``."""
    cols = ["valid_time", "value", "n_members", "lead_day"]
    if live_rows.empty or coef.empty:
        return pd.DataFrame(columns=cols)
    m = _with_tod(live_rows).merge(coef[KEYS + ["bias", "weight"]], on=KEYS, how="inner")
    if m.empty:
        return pd.DataFrame(columns=cols)
    m["wc"] = m["weight"] * (m["value"] - m["bias"])
    agg = (
        m.groupby(["valid_time", "lead_day"])
        .agg(wc=("wc", "sum"), w=("weight", "sum"), n_members=("model", "size"))
        .reset_index()
    )
    agg = agg[agg["n_members"] >= min_members].copy()
    agg["value"] = agg["wc"] / agg["w"]
    return agg[cols].sort_values("valid_time").reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Uncertainty band — empirical out-of-sample residual quantiles per lead
# --------------------------------------------------------------------------- #

def residual_quantiles(blend_pairs: pd.DataFrame, *, level: float = BAND_LEVEL,
                       min_n: int = 50) -> dict[int, tuple[float, float]]:
    """Per-lead (low, high) signed-error quantiles of the (out-of-sample) blend, so
    ``[value+low, value+high]`` covers the central ``level`` of past outcomes."""
    if blend_pairs.empty:
        return {}
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    out: dict[int, tuple[float, float]] = {}
    for ld, g in blend_pairs.groupby("lead_day"):
        if len(g) < min_n:
            continue
        out[int(ld)] = (float(g["error"].quantile(lo_q)), float(g["error"].quantile(hi_q)))
    return out


def band_coverage_oos(blend_pairs: pd.DataFrame, *, level: float = BAND_LEVEL,
                      split: float = 0.7) -> float | None:
    """Honest calibration check: learn the per-lead quantiles on the earliest
    ``split`` of issue dates, then measure how often the band covers the held-out
    later pairs. Returns the achieved coverage fraction (target = ``level``)."""
    if blend_pairs.empty:
        return None
    bp = blend_pairs.sort_values("issued_at")
    cut = bp["issued_at"].quantile(split)
    train, test = bp[bp["issued_at"] <= cut], bp[bp["issued_at"] > cut]
    q = residual_quantiles(train, level=level)
    if not q or test.empty:
        return None
    covered = total = 0
    for ld, g in test.groupby("lead_day"):
        if int(ld) not in q:
            continue
        lo, hi = q[int(ld)]
        covered += int(((g["error"] >= lo) & (g["error"] <= hi)).sum())
        total += len(g)
    return round(covered / total, 3) if total else None
