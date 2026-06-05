"""Estimate the freezing level (0 °C isotherm height) from the two temperature
stations, by the standard surface-temperature-lapse-rate method.

With temperatures at two known elevations (bottom 740 m, top 1150 m) the observed
lapse rate is ``Γ = (T_bottom − T_top) / Δz`` and the 0 °C height is::

    H₀ = z_bottom + T_bottom / Γ        (= z_top + T_top / Γ)

This is a workhorse in mountain hydrology / snow science (rain–snow line, snowmelt
timing, avalanche freezing level). Two big caveats drive the design here:

- It's a *linear* profile over only 410 m, so it's an **estimate with a real
  error bar** — we propagate a per-station temperature uncertainty into a range.
- Its trustworthiness depends on regime, so every estimate is flagged:
    * ``interp``    — 0 °C lands between the stations → interpolation, reliable.
    * ``above``     — both stations > 0 °C → 0 °C above the top → extrapolation up.
    * ``below``     — both stations < 0 °C → 0 °C below the valley → extrapolation.
    * ``inversion`` — top warmer than bottom (Γ ≤ 0) → linear model invalid; H₀ NaN.

Note this is a *surface-2 m-temperature* estimate; a model's
``freezing_level_height`` is a *free-atmosphere column* diagnostic — related but
not identical, so model-vs-estimate comparison is a cross-check, not ground truth.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

Z_BOTTOM = 740.0
Z_TOP = 1150.0
T_SIGMA = 1.0  # °C per-station representativeness/measurement uncertainty for the band


def estimate_series(
    paired: pd.DataFrame, *, z_b: float = Z_BOTTOM, z_t: float = Z_TOP,
    t_sigma: float = T_SIGMA,
) -> pd.DataFrame:
    """Add ``h0, lower, upper, regime`` to a frame with ``t_bottom`` and ``t_top``.

    ``lower``/``upper`` are ±1σ from propagating ``t_sigma`` on both temperatures
    through H₀ (the band naturally widens as the stations' temps converge / as the
    estimate extrapolates). H₀ and the band are NaN in the ``inversion`` regime.
    """
    df = paired.copy()
    tb = df["t_bottom"].to_numpy(dtype=float)
    tt = df["t_top"].to_numpy(dtype=float)
    dz = z_t - z_b
    D = tb - tt  # > 0 for normal cooling with height
    normal = D > 0
    safeD = np.where(normal, D, np.nan)

    h0 = z_b + dz * tb / safeD
    # ∂H₀/∂T_b = -dz·T_top/D², ∂H₀/∂T_t = dz·T_bottom/D²  → σ via error propagation.
    sigma = np.abs(dz) * t_sigma / safeD**2 * np.sqrt(tt**2 + tb**2)

    regime = np.where(
        ~normal, "inversion",
        np.where(h0 < z_b, "below", np.where(h0 > z_t, "above", "interp")),
    )
    df["h0"] = h0
    df["lower"] = h0 - sigma
    df["upper"] = h0 + sigma
    df["regime"] = regime
    return df


def estimate_point(t_bottom: float, t_top: float, **kw) -> dict:
    """Scalar convenience wrapper around :func:`estimate_series`."""
    row = estimate_series(
        pd.DataFrame({"t_bottom": [t_bottom], "t_top": [t_top]}), **kw
    ).iloc[0]
    return {
        "h0": None if pd.isna(row["h0"]) else float(row["h0"]),
        "lower": None if pd.isna(row["lower"]) else float(row["lower"]),
        "upper": None if pd.isna(row["upper"]) else float(row["upper"]),
        "regime": str(row["regime"]),
    }


def pair_stations(
    bottom: pd.DataFrame, top: pd.DataFrame, *, tolerance: pd.Timedelta = pd.Timedelta(minutes=30),
) -> pd.DataFrame:
    """Time-align two ``[time, observed]`` obs frames into ``[time, t_bottom, t_top]``
    (nearest match within ``tolerance``)."""
    if bottom.empty or top.empty:
        return pd.DataFrame(columns=["time", "t_bottom", "t_top"])
    b = bottom.rename(columns={"observed": "t_bottom"}).sort_values("time")
    t = top.rename(columns={"observed": "t_top"}).sort_values("time")
    merged = pd.merge_asof(b, t, on="time", direction="nearest", tolerance=tolerance)
    return merged.dropna(subset=["t_bottom", "t_top"]).reset_index(drop=True)
