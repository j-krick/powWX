"""Seasons, QC, climatology and percentiles behind "Season in context"."""

import numpy as np
import pandas as pd

from powwx import history as hist


def _hourly(values, start="2025-01-01T00:00Z"):
    return pd.Series(values, index=pd.date_range(start, periods=len(values), freq="h"), dtype=float)


# --- seasons -------------------------------------------------------------------

def test_season_boundaries_and_labels():
    idx = pd.DatetimeIndex(["2026-10-31", "2026-11-01", "2027-04-30", "2027-05-01"])
    assert hist.in_season(idx, "winter").tolist() == [False, True, True, False]
    assert hist.in_season(idx, "summer").tolist() == [True, False, False, True]
    assert hist.season_end_year(idx[1:3], "winter").tolist() == [2027, 2027]
    assert hist.season_label(2027, "winter") == "2026–27"
    assert hist.season_label(2027, "summer") == "2027"
    assert hist.season_start(2027, "snow") == pd.Timestamp("2026-10-01")


def test_positions_share_one_axis_and_feb29_joins_feb28():
    idx = pd.DatetimeIndex(["2023-11-01", "2024-02-28", "2024-02-29", "2024-03-01", "2025-03-01"])
    pos = hist.season_position(idx, "winter")
    assert pos[0] == 0
    assert pos[1] == pos[2]                   # Feb 29 shares Feb 28's slot
    assert pos[3] == pos[4]                   # Mar 1 lines up in leap and non-leap years
    assert len(hist.axis_dates("winter")) == 181
    assert len(hist.axis_dates("snow")) == 273


def test_latest_season_between_and_within_seasons():
    oct16 = pd.Timestamp("2026-10-16")
    assert hist.latest_season(oct16, "winter") == 2026       # last winter (2025-26)
    assert hist.latest_season(oct16, "summer") == 2026       # this summer
    assert hist.latest_season(oct16, "snow") == 2027         # snow season began 1 Oct
    assert hist.latest_season(pd.Timestamp("2027-01-10"), "summer") == 2026


def test_season_matrix_rows_are_seasons():
    days = pd.date_range("2024-11-01", "2026-04-30", freq="D")
    daily = pd.Series(np.arange(len(days), dtype=float), index=days)
    m = hist.season_matrix(daily, "winter")
    assert list(m.index) == [2025, 2026]
    assert m.loc[2025, 0] == 0.0              # 1 Nov 2024 opens winter 2024-25
    assert m.shape[1] == 181


# --- QC and daily values ---------------------------------------------------------

def test_qc_drops_sentinels_and_spikes_and_clamps_snow_noise():
    t = _hourly([1.0, 1.2, -6999.0, 1.1, 25.0, 1.3, 1.2, 1.0])
    out = hist.qc_hourly(t, "temperature_2m")
    assert -6999.0 not in out.values and 25.0 not in out.values
    assert len(out) == 6
    s = _hourly([1.0, 1.01, -79.99, 1.02, 3.5, 1.03, 1.02, -0.02, 1.0])
    out = hist.qc_hourly(s, "snow_depth")
    assert out.min() >= 0.0 and 3.5 not in out.values and -79.99 not in out.values
    bare = hist.qc_hourly(_hourly([0.03, -0.02, 0.01, 0.04, 0.0]), "snow_depth")
    assert (bare == 0.0).all()                 # sonic noise on bare ground counts as no snow


def test_daily_values_need_20_hours_and_use_bc_dates():
    # 24 h from 07:00Z = midnight PDT on 2025-07-01, then a 10-hour stub of the next day.
    s = _hourly([1.0] * 24 + [5.0] * 10, start="2025-07-01T07:00Z")
    d = hist.daily_values(s)
    assert list(d.index) == [pd.Timestamp("2025-07-01")]
    assert d.iloc[0] == 1.0


def test_running_mean_is_trailing_and_needs_min_days():
    days = pd.date_range("2025-01-01", periods=10, freq="D")
    daily = pd.Series(np.arange(10, dtype=float), index=days).drop(days[[1, 2, 3]])
    rm = hist.running_mean(daily, 7, min_valid=5)
    assert rm.index.min() == pd.Timestamp("2025-01-09")     # first day with 5 of 7 present
    assert rm.loc["2025-01-10"] == np.mean([4, 5, 6, 7, 8, 9])


# --- climatology, percentiles, ranks --------------------------------------------------

def test_doy_pool_wraps_round_new_year():
    days = pd.date_range("1991-01-01", "1992-12-31", freq="D")
    daily = pd.Series(1.0, index=days)
    pools = hist.doy_pools(daily, (1991, 1992), window=7)
    assert len(pools[1]) == len(pools[180]) == 2 * 15   # 15 days x 2 years, Dec days included


def test_climatology_quantiles_and_counts():
    rng = np.random.default_rng(1)
    days = pd.date_range("1991-01-01", "2020-12-31", freq="D")
    daily = pd.Series(rng.normal(0, 1, len(days)), index=days)
    clim = hist.doy_climatology(daily, (1991, 2020))
    assert clim.loc[100, "n_years"] == 30
    assert abs(clim.loc[100, 0.5]) < 0.3 and abs(clim.loc[100, 0.9] - 1.28) < 0.35


def test_percentile_and_rank_handle_ties_and_small_n():
    assert hist.percentile_of(5, [1, 2, 3, 4]) == 100.0
    assert hist.percentile_of(2, [1, 2, 3, 4]) == 37.5          # tie counts half
    assert hist.percentile_of(2, []) is None
    assert hist.rank_of(3.0, [1.0, 3.0, 5.0]) == (2, 4)         # tie shares the better rank
    assert hist.rank_of(0.5, [1.0, 2.0], descending=False) == (1, 3)


def test_same_days_means_skips_seasons_missing_too_many_days():
    m = pd.DataFrame({0: [1.0, 2.0, np.nan], 1: [1.0, 2.0, np.nan], 2: [1.0, np.nan, 4.0]},
                     index=[2011, 2012, 2013])
    means = hist.same_days_means(m, [0, 1, 2], min_coverage=0.6)
    assert means.to_dict() == {2011: 1.0, 2012: 2.0}


# --- adjusting reanalysis ---------------------------------------------------------------

def test_offsets_recover_a_known_monthly_bias_and_apply_smoothly():
    days = pd.date_range("2015-01-01", "2019-12-31", freq="D")
    rng = np.random.default_rng(2)
    model = pd.Series(rng.normal(0, 3, len(days)), index=days)
    station = model + np.where(days.month.isin([12, 1, 2]), 1.0, 0.0)
    off = hist.monthly_offsets(model, station)
    assert abs(off.loc[1, "offset"] - 1.0) < 1e-9 and abs(off.loc[7, "offset"]) < 1e-9
    adj = hist.apply_offsets(model, off)
    jump = (adj - model).diff().abs().max()
    assert jump < 0.1                                              # no step at month ends


def test_band_coverage_is_near_ten_percent_for_a_fair_band():
    days = pd.date_range("2005-01-01", "2024-12-31", freq="D")
    rng = np.random.default_rng(3)
    seasonal = 8 * np.sin(2 * np.pi * (days.dayofyear - 110) / 365)
    model = pd.Series(seasonal + rng.normal(0, 3, len(days)), index=days)
    station = pd.Series(seasonal + 0.7 + rng.normal(0, 3, len(days)), index=days)
    cov = hist.band_coverage(model, station)
    assert abs(cov["below"] - 0.1) < 0.03 and abs(cov["above"] - 0.1) < 0.03
    assert cov["expected"] == [0.1, 0.1]


# --- snow milestones ----------------------------------------------------------------------

def _snow_season(onset="2020-11-10", peak="2021-03-20", melt="2021-05-15", start="2020-10-01",
                 end="2021-06-30"):
    days = pd.date_range(start, end, freq="D")
    v = np.zeros(len(days))
    for i, d in enumerate(days):
        if pd.Timestamp(onset) <= d <= pd.Timestamp(peak):
            v[i] = 10 + 190 * (d - pd.Timestamp(onset)).days / (pd.Timestamp(peak) - pd.Timestamp(onset)).days
        elif pd.Timestamp(peak) < d < pd.Timestamp(melt):
            v[i] = 200 * (pd.Timestamp(melt) - d).days / (pd.Timestamp(melt) - pd.Timestamp(peak)).days
    return pd.Series(v, index=days)


def test_milestones_found_on_a_clean_season():
    m = hist.snow_milestones(_snow_season())
    assert m["onset"] == pd.Timestamp("2020-11-10")
    assert m["peak_date"] == pd.Timestamp("2021-03-20") and m["peak_cm"] == 200
    assert m["meltout"] is not None and m["meltout"] < pd.Timestamp("2021-05-15")


def test_milestones_refuse_what_gaps_hide():
    s = _snow_season()
    s = s[(s.index < "2020-11-01") | (s.index > "2020-12-31")]   # Nov-Dec missing
    m = hist.snow_milestones(s)
    assert m["onset"] is None and m["peak_cm"] is None
    late = _snow_season(start="2020-12-20")                     # starts already snowy
    assert hist.snow_milestones(late)["onset"] is None


def test_step_change_finds_a_planted_shift_and_not_a_seasonal_artefact():
    days = pd.date_range("2012-01-01", "2025-12-31", freq="D")
    rng = np.random.default_rng(4)
    model = pd.Series(8 * np.sin(2 * np.pi * days.dayofyear / 365) + rng.normal(0, 2, len(days)), index=days)
    seasonal_bias = np.where(days.month.isin([12, 1, 2]), 1.0, 0.0)
    shift = np.where(days >= "2021-10-15", -0.3, 0.0)
    station = model + seasonal_bias + shift + rng.normal(0, 1, len(days))
    # Drop most winters after the cut: without deseasonalising, that alone would look like a step.
    keep = (days < "2021-10-15") | ~days.month.isin([12, 1, 2])
    s = hist.step_change(station[keep], model, "2021-10-15")
    assert s["lo"] < -0.3 < s["hi"] and s["hi"] < 0
    none = hist.step_change(model + rng.normal(0, 1, len(days)), model, "2021-10-15")
    assert none["lo"] < 0 < none["hi"]


# --- long view: season means, trends, El Nino ------------------------------------------

def test_season_means_need_most_days_and_skip_a_season_under_way():
    days = pd.date_range("2020-11-01", "2022-01-15", freq="D")
    daily = pd.Series(1.0, index=days)
    daily = daily.drop(pd.date_range("2021-01-01", "2021-01-31"))      # winter 2021 loses 31 of 181 days
    winters = hist.season_means(daily, "winter")
    assert list(winters.index) == []                                  # 83 % < 90 %; 2022 still under way
    assert list(hist.season_means(daily, "winter", min_coverage=0.8).index) == [2021]
    assert list(hist.season_means(daily, "year").index) == [2021]     # 334/365 days; 2020, 2022 partial


def test_monthly_season_means_need_every_month():
    months = pd.date_range("1990-11-01", "1992-04-01", freq="MS")
    monthly = pd.Series(np.arange(len(months), dtype=float), index=months).drop(pd.Timestamp("1992-02-01"))
    w = hist.monthly_season_means(monthly, "winter")
    assert list(w.index) == [1991] and w.loc[1991] == np.mean(range(6))


def test_sen_slope_recovers_a_trend_and_admits_noise():
    rng = np.random.default_rng(5)
    years = np.arange(1950, 2026)
    warm = 0.03 * (years - 1950) + rng.normal(0, 0.8, len(years))     # +0.3 degC/decade
    t = hist.sen_slope_ci(years, warm)
    assert t["lo"] < 0.3 < t["hi"] and t["lo"] > 0 and t["n"] == 76
    short = hist.sen_slope_ci(years[-15:], rng.normal(0, 0.8, 15))
    assert short["lo"] < 0 < short["hi"]                              # 15 noisy years can't tell
    assert hist.sen_slope_ci(years[:5], warm[:5]) is None


def test_trend_statement_says_warming_only_when_every_source_agrees():
    a = {"slope": 0.3, "lo": 0.1, "hi": 0.5}
    b = {"slope": 0.6, "lo": 0.4, "hi": 0.8}
    c = {"slope": 0.2, "lo": -0.1, "hi": 0.5}
    assert hist.trend_statement([a, b]) == {"verdict": "warming", "min_slope": 0.3, "max_slope": 0.6,
                                            "n_sources": 2}
    assert hist.trend_statement([a, b, c])["verdict"] == "no clear trend"
    assert hist.trend_statement([None])["verdict"] == "unknown"


def test_enso_classes_and_winter_index():
    assert [hist.enso_class(v) for v in (-1.2, -1.0, -0.6, 0.0, 0.5, 0.99, 1.0, 2.3)] == [
        "moderate/strong La Niña", "moderate/strong La Niña", "weak La Niña", "neutral",
        "weak El Niño", "weak El Niño", "moderate/strong El Niño", "moderate/strong El Niño"]
    idx = pd.DataFrame({"season": ["OND", "NDJ", "DJF"], "year": [2015, 2015, 2016], "anom": [2.4, 2.6, 2.5]})
    assert hist.winter_index(idx).to_dict() == {2016: 2.6}           # NDJ 2015 -> winter 2015-16


def test_detrend_and_permutation_test():
    s = pd.Series(np.arange(10, dtype=float) * 0.5 + 3, index=range(2000, 2010))
    assert np.allclose(hist.detrend(s), 0)
    rng = np.random.default_rng(6)
    assert hist.permutation_pvalue(rng.normal(1, 0.5, 15), rng.normal(0, 0.5, 30), n=2000) < 0.01
    assert hist.permutation_pvalue(rng.normal(0, 1, 15), rng.normal(0, 1, 30), n=2000) > 0.05
