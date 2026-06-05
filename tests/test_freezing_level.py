"""Tests for the two-station freezing-level estimate — the formula and the
regime flags that decide whether it's trustworthy."""

import pandas as pd

from powwx import freezing_level as fl


def test_interpolation_regime_midpoint():
    # Bottom +2 (740 m), top -2 (1150 m): 0 °C exactly midway -> 945 m, regime interp.
    r = fl.estimate_point(2.0, -2.0)
    assert r["regime"] == "interp"
    assert abs(r["h0"] - 945.0) < 1e-6
    assert r["lower"] < r["h0"] < r["upper"]


def test_above_and_below_extrapolation():
    above = fl.estimate_point(8.0, 5.0)   # both warm -> 0 °C above the top
    below = fl.estimate_point(-3.0, -7.0)  # both cold -> 0 °C below the valley
    assert above["regime"] == "above" and above["h0"] > fl.Z_TOP
    assert below["regime"] == "below" and below["h0"] < fl.Z_BOTTOM


def test_inversion_is_flagged_not_computed():
    # Top warmer than bottom (cold-air pool) -> linear model invalid.
    r = fl.estimate_point(-4.0, -1.0)
    assert r["regime"] == "inversion"
    assert r["h0"] is None


def test_band_widens_as_stations_converge():
    wide = fl.estimate_point(0.5, -0.4)   # small gradient -> uncertain
    tight = fl.estimate_point(4.0, -4.0)  # strong gradient -> confident
    assert (wide["upper"] - wide["lower"]) > (tight["upper"] - tight["lower"])


def test_pair_stations_aligns_on_time():
    bottom = pd.DataFrame({"time": pd.to_datetime(["2025-01-01T00:00Z", "2025-01-01T01:00Z"]),
                           "observed": [2.0, 1.0]})
    top = pd.DataFrame({"time": pd.to_datetime(["2025-01-01T00:05Z", "2025-01-01T01:50Z"]),
                        "observed": [-2.0, -3.0]})
    paired = fl.pair_stations(bottom, top, tolerance=pd.Timedelta(minutes=30))
    assert len(paired) == 1                       # 01:50 is >30 min from 01:00 -> dropped
    assert paired.iloc[0]["t_bottom"] == 2.0 and paired.iloc[0]["t_top"] == -2.0
