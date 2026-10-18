"""history.json: the #58 kill switch, the permission rule, and surviving failures."""

import importlib.util
import json

import numpy as np
import pandas as pd
import pytest

from powwx import config as cfg

_spec = importlib.util.spec_from_file_location(
    "build_history", cfg.REPO_ROOT / "scripts" / "build_history.py")
bh = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bh)

TODAY = pd.Timestamp("2026-10-16")
SETTINGS = {"reference_years": [1991, 2020], "reference_model": "era5_land",
            "pool_window_days": 7, "running_mean_days": 7,
            "adjust_since": {"station_58": "2021-10-15"},
            "station_58": {"show_station_values": True, "permission": "on file"}}


def _seasonal(index):
    return 4 + 8 * np.sin(2 * np.pi * (index.dayofyear - 110) / 365)


def _obs_log(data_dir, *, location, variable):
    times = pd.date_range("2019-01-01", "2026-10-15T23:00", freq="h", tz="UTC")
    rng = np.random.default_rng(len(location))
    if variable == "temperature_2m":
        values = _seasonal(times) + rng.normal(0, 1, len(times))
    elif variable == "snow_depth" and location == "station_58":
        m = times.month
        values = np.where(m.isin([12, 1, 2, 3]), 1.5, np.where(m.isin([11, 4]), 0.5, 0.0))
    else:
        return []
    return [{"location": location, "variable": variable, "value": float(v),
             "time": t.strftime("%Y-%m-%dT%H:%M:%SZ")} for t, v in zip(times, values)]


def _reanalysis(data_dir, *, location, model="era5_land"):
    days = pd.date_range("1990-01-01", "2026-10-10", freq="D")
    rng = np.random.default_rng(7)
    snow = np.where(days.month.isin([12, 1, 2, 3, 4]), 3.0 + rng.normal(0, 0.5, len(days)), 0.0)
    return pd.DataFrame({"temperature_2m_mean": _seasonal(days) - 0.5 + rng.normal(0, 2, len(days)),
                         "snow_depth_mean": snow}, index=days)


def _top_snow():
    times = pd.date_range("2026-09-27", "2026-10-15", freq="h", tz="UTC")
    return [{"location": "pow_o_meter", "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "variable": "snow_depth", "value": 0.02, "site_id": "shames_top_b"} for t in times]


def _blend():
    times = pd.date_range("2026-10-16T07:00Z", periods=5 * 24, freq="h")
    vals = list(_seasonal(times))
    return {"locations": {lid: {"temperature_2m": {
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in times], "values": vals,
        "lower": [v - 2 for v in vals], "upper": [v + 2 for v in vals]}}
        for lid in ("station_58", "pow_o_meter")}}


def _climate(data_dir, *, name, prefix):
    """Small stand-ins for RONI (El Nino every 4th winter) and AHCCD Terrace."""
    if name == "roni":
        years = range(1989, 2027)
        return pd.DataFrame({"season": ["NDJ"] * len(years), "year": list(years),
                             "anom": [1.5 if y % 4 == 0 else 0.0 for y in years]})
    months = pd.date_range("1960-01-01", "2020-12-01", freq="MS")
    return pd.DataFrame({"date": months.strftime("%Y-%m"),
                         "temp_mean": _seasonal(months) + 0.01 * (months.year - 1960)})


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(bh.obs_logger, "load_observation_log", _obs_log)
    monkeypatch.setattr(bh.ra, "load_reanalysis", _reanalysis)
    monkeypatch.setattr(bh.obs, "fetch_powometer_v1", _top_snow)
    monkeypatch.setattr(bh.cs, "load_latest", _climate)

    def settings(show=True):
        s = json.loads(json.dumps(SETTINGS))
        s["station_58"]["show_station_values"] = show
        monkeypatch.setattr(bh, "load_settings", lambda: s)
    return settings


def test_station_values_shown_with_permission(wired):
    wired(show=True)
    out = bh.build(today=TODAY, blend=_blend())
    b = out["locations"]["station_58"]
    summer = b["charts"]["temperature_summer"]
    assert summer["current"]["source"] == "measured"
    assert summer["past"] and summer["band"]["n_years"] == 30
    assert summer["forecast"] is not None
    assert b["adjustment"]["since"] == "2021-10-15"
    assert any("value" in c for c in b["unusual"]["days"] if c["kind"] == "observed")
    assert out["modelled_snowpack"]["band"]["p50"][100] is not None


def test_kill_switch_removes_every_station_58_value(wired):
    wired(show=False)
    out = bh.build(today=TODAY, blend=_blend())
    b = out["locations"]["station_58"]
    assert b["station_values"] is False
    for key in ("temperature_winter", "temperature_summer"):
        c = b["charts"][key]
        assert c["current"]["source"] == "modelled"          # ERA5-Land stands in
        assert c["past"] == [] and c["average"] is None and c["forecast"] is None
    snow = b["charts"]["snow_depth"]
    assert snow["current"] is None and snow["past"] == []
    assert snow["summary"] is None or snow["summary"]["depth_cm"] is None
    assert all("value" not in c for c in b["unusual"]["days"] if c["kind"] == "observed")
    assert "seasons" not in (b["milestones"] or {})
    # Derived numbers stay: the season-so-far percentile and rank.
    assert b["charts"]["temperature_summer"]["summary"]["percentile"] is not None
    # The CC BY top station is unaffected.
    assert out["locations"]["pow_o_meter"]["charts"]["temperature_summer"]["current"]["source"] == "measured"


def test_showing_values_without_a_permission_record_fails(monkeypatch):
    s = json.loads(json.dumps(SETTINGS))
    s["station_58"]["permission"] = None
    monkeypatch.setattr(bh.cfg, "load_yaml", lambda name: s)
    with pytest.raises(ValueError):
        bh.load_settings()


def test_survives_missing_cache_blend_and_a_failing_station(wired, monkeypatch, tmp_path):
    wired(show=True)
    monkeypatch.setattr(bh.ra, "load_reanalysis", lambda *a, **k: pd.DataFrame())

    def broken():
        raise RuntimeError("powOmeter unreachable")

    monkeypatch.setattr(bh.obs, "fetch_powometer_v1", broken)
    assert bh.main(out_dir=tmp_path) == 0                    # no blend.json in tmp_path
    out = json.loads((tmp_path / "history.json").read_text(encoding="utf-8"))
    assert "station_58" in out["locations"] and "pow_o_meter" not in out["locations"]
    c = out["locations"]["station_58"]["charts"]["temperature_summer"]
    assert c["band"] is None and c["current"]["source"] == "measured"
    assert out["modelled_snowpack"] is None


def test_long_view_and_el_nino_blocks(wired):
    wired(show=True)
    out = bh.build(today=TODAY, blend=_blend())
    lv = out["long_view"]["kinds"]["winter"]
    assert set(lv["series"]) == {"era5_land", "era5", "ahccd"}
    assert {t["source"] for t in lv["trends_since"]} == {"era5_land", "era5", "ahccd"}
    assert lv["statement"]["verdict"] in {"warming", "cooling", "no clear trend"}
    assert lv["station"]["n_seasons"] >= 1
    e = out["enso"]
    assert e["current"]["phase"] == "neutral" and e["winter"] == "2026–27"
    strong = next(c for c in e["classes"] if c["phase"] == "moderate/strong El Niño")
    assert strong["n"] > 0
    assert any("station_rank" in r for r in e["years"])


def test_kill_switch_also_drops_station_ranks_from_el_nino(wired):
    wired(show=False)
    e = bh.build(today=TODAY, blend=_blend())["enso"]
    assert all("station_rank" not in r for r in e["years"])


def test_old_site_snow_seasons_are_shown_but_not_ranked(wired, monkeypatch):
    wired(show=True)
    old = pd.date_range("2025-01-26", "2026-06-30", freq="h", tz="UTC")
    recs = [{"location": "pow_o_meter", "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "variable": "snow_depth", "value": 2.0, "site_id": "shames_top_a"} for t in old]
    monkeypatch.setattr(bh.obs, "fetch_powometer_v1", lambda: recs + _top_snow())
    snow = bh.build(today=TODAY, blend=_blend())["locations"]["pow_o_meter"]["charts"]["snow_depth"]
    assert [p["label"] for p in snow["past"]] == [
        f"{bh.hist.season_label(y, 'snow')}, old site" for y in (2025, 2026)]
    assert all(p["old_site"] for p in snow["past"])
    assert snow["band"] is None and "rank" not in snow["summary"]
    assert max(v for v in snow["current"]["values"] if v is not None) < 10   # new site only
    assert "old site" in snow["note"]
