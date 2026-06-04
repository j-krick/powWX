"""powWX blend — a bias-corrected, skill-weighted multi-model temperature forecast.

Built on the verification findings: every model carries a measurable per-lead
bias (e.g. the 1–3 °C cold bias at the top station), and the models have
different skill. Correct each model's bias and combine them weighted by skill
(inverse MAE) and you get a consensus that, in the literature, is hard to beat
(NWS National Blend of Models; Krishnamurti's superensemble; BMA/EMOS).

The blend for a forecast valid at *t* (issued at *t − lead*) is::

    blend(t) = Σ_m w_m(lead) · (forecast_m(t) − bias_m(lead)) / Σ_m w_m(lead)

over the models actually available at that valid time and lead.

**Strictly causal.** Coefficients (``bias_m``, ``w_m``) are learned only from
matched pairs whose *observation was already available* — i.e. ``valid_time``
before the moment the forecast was issued — within a trailing window. The
out-of-sample evaluation (:func:`walk_forward_blend`) retrains every month on
only the prior window, so a blend pair is never informed by its own outcome.
This is the whole point: an in-sample blend would look better than it is.
"""

from __future__ import annotations

import pandas as pd

WINDOW_DAYS = 90      # trailing training window (≈ current season; adapts to drift)
MIN_TRAIN = 30        # min matched pairs per (model, lead_day) to trust a coefficient
MAE_FLOOR = 0.5       # °C; floor so a fluke-small MAE can't dominate the weights
MIN_MEMBERS = 2       # need ≥2 corrected models to form a blend value
BLEND_ID = "powwx_blend"
BLEND_LABEL = "powWX blend"

_BLEND_COLS = ["model", "issued_at", "valid_time", "value", "source",
               "observed", "error", "abs_error", "lead_day"]


def _empty_blend() -> pd.DataFrame:
    return pd.DataFrame(columns=_BLEND_COLS)


def learn_coefficients(
    aligned: pd.DataFrame, *, asof: pd.Timestamp,
    window_days: int = WINDOW_DAYS, min_train: int = MIN_TRAIN,
    mae_floor: float = MAE_FLOOR,
) -> pd.DataFrame:
    """Per ``(model, lead_day)`` bias + inverse-MAE weight from matched pairs whose
    observation was available before ``asof`` (``valid_time < asof``), within the
    trailing ``window_days``. Strictly causal — nothing at/after ``asof`` is used.

    Returns columns ``[model, lead_day, bias, mae, n, weight]``.
    """
    cols = ["model", "lead_day", "bias", "mae", "n", "weight"]
    if aligned.empty:
        return pd.DataFrame(columns=cols)
    lo = asof - pd.Timedelta(days=window_days)
    train = aligned[(aligned["valid_time"] < asof) & (aligned["valid_time"] >= lo)]
    if train.empty:
        return pd.DataFrame(columns=cols)
    coef = (
        train.groupby(["model", "lead_day"])
        .agg(bias=("error", "mean"), mae=("abs_error", "mean"), n=("error", "size"))
        .reset_index()
    )
    coef = coef[coef["n"] >= min_train].copy()
    coef["weight"] = 1.0 / coef["mae"].clip(lower=mae_floor)
    return coef


def _combine(merged: pd.DataFrame, *, min_members: int) -> pd.DataFrame:
    """Weighted mean of bias-corrected members per forecast instance
    ``(issued_at, valid_time, lead_day)``. ``merged`` carries per-row
    ``value, bias, weight, observed``."""
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
    mae_floor: float = MAE_FLOOR,
) -> pd.DataFrame:
    """Out-of-sample blend. For each calendar month of *issue* dates, learn
    coefficients from the trailing window ending at the month's start (so only
    prior, already-observed pairs are used), then blend every forecast issued in
    that month. Returns blend pseudo-pairs (``model = powwx_blend``)."""
    if aligned.empty:
        return _empty_blend()
    a = aligned.copy()
    # issued_at is UTC; take UTC wall-time months (tz_localize(None) avoids the
    # "dropping timezone" warning and is explicit that the months are in UTC).
    blocks = a["issued_at"].dt.tz_localize(None).dt.to_period("M")
    merged_parts = []
    for period in blocks.drop_duplicates().sort_values():
        p_start = period.start_time.tz_localize("UTC")
        coef = learn_coefficients(a, asof=p_start, window_days=window_days,
                                  min_train=min_train, mae_floor=mae_floor)
        if coef.empty:
            continue
        test = a[blocks.values == period]
        m = test.merge(coef[["model", "lead_day", "bias", "weight"]],
                       on=["model", "lead_day"], how="inner")
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
    """Apply learned ``coef`` to the latest run's per-model values to produce the
    forward blend. ``live_rows`` needs ``model, valid_time, value, lead_day``.
    Returns ``[valid_time, value, n_members]`` sorted by time."""
    if live_rows.empty or coef.empty:
        return pd.DataFrame(columns=["valid_time", "value", "n_members"])
    m = live_rows.merge(coef[["model", "lead_day", "bias", "weight"]],
                        on=["model", "lead_day"], how="inner")
    if m.empty:
        return pd.DataFrame(columns=["valid_time", "value", "n_members"])
    m["wc"] = m["weight"] * (m["value"] - m["bias"])
    agg = (
        m.groupby("valid_time")
        .agg(wc=("wc", "sum"), w=("weight", "sum"), n_members=("model", "size"))
        .reset_index()
    )
    agg = agg[agg["n_members"] >= min_members].copy()
    agg["value"] = agg["wc"] / agg["w"]
    return agg[["valid_time", "value", "n_members"]].sort_values("valid_time").reset_index(drop=True)
