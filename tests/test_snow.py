"""Snow: forecast snowfall vs measured depth change, the blend, and the live
depth forecast (powwx/snow.py)."""

import numpy as np
import pandas as pd
import pytest

from powwx import snow

H = pd.Timedelta(hours=1)
D = pd.Timedelta(days=1)


def _hourly(start, values_cm, site_hours=1):
    idx = pd.date_range(start, periods=len(values_cm), freq="h", tz="UTC")
    return pd.Series(np.asarray(values_cm, float) / 100, index=idx)


def test_window_end_puts_the_end_hour_in_its_own_window():
    t = pd.DatetimeIndex(["2026-11-24T16:00Z", "2026-11-24T16:30Z", "2026-11-24T03:00Z"])
    ends = snow.window_end(t)
    assert list(ends.dt.strftime("%d %H")) == ["24 16", "25 16", "24 16"]


def test_changes_are_only_taken_within_a_site():
    a = _hourly("2026-09-20T00:00Z", [50] * 24 * 3)     # old site, deep
    b = _hourly("2026-09-23T00:00Z", [5] * 24 * 3)      # new site, shallow
    ch = snow.observed_changes({"a": a, "b": b})
    assert set(ch["site"]) == {"a", "b"}
    assert (ch["change"].abs() < 1e-9).all()             # no -45 cm jump across the move


def _forecast(ends, snowfall_per_day, *, lead_days=(1, 2), model="m1", source="previous_runs"):
    """Hourly snowfall evenly spread over each window, for each lead day."""
    rows = []
    for e, sf in zip(ends, snowfall_per_day):
        for lead in lead_days:
            for h in range(24):
                vt = e - h * H
                init = vt - (lead * 24 + 1) * H
                rows.append({"model": model, "issued_at": init, "init_time": init, "valid_time": vt,
                             "value": sf / 24, "source": source})
    return pd.DataFrame(rows)


def test_daily_snowfall_sums_each_window_per_lead_day():
    ends = pd.date_range("2026-01-01T16:00Z", periods=3, freq="D")
    d = snow.daily_snowfall(_forecast(ends, [0, 12, 24]))
    got = d[d["lead_day"] == 1].set_index("end")["snowfall"]
    assert got.round(6).tolist() == [0, 12, 24]
    assert set(d["lead_day"]) == {1, 2}


def _synthetic_pairs(n_days=200, seed=0, a=-2.0, b=0.8):
    rng = np.random.default_rng(seed)
    ends = pd.date_range("2025-01-01T16:00Z", periods=n_days, freq="D")
    sf = rng.gamma(0.6, 10, n_days)
    change = a + b * sf + rng.normal(0, 1.0, n_days)
    rows = []
    for model, noise in [("good", 0.5), ("poor", 8.0)]:
        x = np.maximum(0, sf + rng.normal(0, noise, n_days))
        rows.append(pd.DataFrame({"model": model, "lead_day": 1, "end": ends, "snowfall": x}))
    daily = pd.concat(rows, ignore_index=True)
    changes = pd.DataFrame({"end": ends, "depth_start": 50.0, "depth_end": 50.0 + change,
                            "change": change, "site": "s"})
    return snow.pairs(daily, changes)


def test_fit_recovers_the_line_and_weights_the_better_model_more():
    coef = snow.fit(_synthetic_pairs()).set_index("model")
    assert coef.loc["good", "b"] == pytest.approx(0.8, abs=0.05)
    assert coef.loc["good", "a"] == pytest.approx(-2.0, abs=0.5)
    assert coef.loc["good", "weight"] > coef.loc["poor", "weight"]


def test_walk_forward_is_out_of_sample_and_beats_no_change():
    p = _synthetic_pairs(n_days=300)
    oos = snow.walk_forward(p)
    # The first windows have no earlier data to learn from, so they get no prediction.
    first_month = p["end"].min().to_period("M")
    assert not (oos["end"].dt.to_period("M") == first_month).any()
    rows = snow.scores_by_lead(oos, p)
    assert rows[0]["blend_mae"] < rows[0]["persistence_mae"] / 2
    assert rows[0]["models"]["good"]["mae"] < rows[0]["models"]["poor"]["mae"]


def test_walk_forward_never_forecasts_below_bare_ground():
    p = _synthetic_pairs(n_days=200)
    p["depth_start"] = 0.0
    oos = snow.walk_forward(p)
    assert (oos["pred"] >= -1e-9).all()


def test_live_forecast_starts_at_the_latest_reading_and_stays_non_negative():
    now = pd.Timestamp("2026-11-24T20:00Z")
    obs = _hourly("2026-11-24T00:00Z", [30] * 18)               # 30 cm, last reading 17Z
    t0 = obs.index.max()
    hours = pd.date_range(t0 + H, periods=24 * 4, freq="h", tz="UTC")
    fc = pd.DataFrame({"model": "m1", "issued_at": t0, "init_time": t0 - 6 * H,
                       "valid_time": hours, "value": 1.0, "source": "live"})   # 24 cm/day
    coef = pd.DataFrame([{"model": "m1", "lead_day": k, "a": -2.0, "b": 0.5, "mae": 3.0,
                          "r": 0.6, "n": 100, "weight": 1.0} for k in range(1, 8)])
    band = {k: (-3.0 * k, 3.0 * k) for k in range(1, 8)}
    out = snow.live_forecast(fc, obs, coef, band, now=now)
    assert out["anchor"]["depth_cm"] == pytest.approx(30.0)
    assert out["times"][0] == "2026-11-24T17:00Z" and out["values"][0] == pytest.approx(0.30)
    first = out["daily"][0]                                       # 23 h left in the window
    assert first["end"] == "2026-11-25T16:00Z"
    assert first["depth_cm"] == pytest.approx(30 + (-2 * 23 / 24 + 0.5 * 23), abs=0.2)
    assert all(v >= 0 for v in out["lower"])
    # Too old a reading: no forecast.
    assert snow.live_forecast(fc, obs, coef, band, now=now + 3 * D) is None


def test_forecast_length_stops_where_the_blend_stops_beating_no_change():
    rows = [{"lead_day": d, "skill_pct": s} for d, s in [(1, 20), (2, 10), (3, 1), (4, -2), (5, 4)]]
    assert snow.skilful_leads(rows) == 3
    assert snow.skilful_leads([{"lead_day": 2, "skill_pct": 30}]) == 0
