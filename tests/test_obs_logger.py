"""Observation logger: POW-O-METER hours are appended only once (powOmeter serves
its whole history every time), every row keeps its site so snow depth is never
pooled across sites, and one failing source doesn't stop the other."""

from unittest import mock

import pandas as pd
import requests

from powwx import obs_logger as ol

AVCAN = [{"location": "station_58", "time": "2026-10-16T20:00:00Z",
          "variable": "temperature_2m", "value": 4.0}]


def _pom(*hours):
    recs = []
    for h in hours:
        t = f"2026-10-16T{h:02d}:00:00Z"
        recs += [
            {"location": "pow_o_meter", "time": t, "variable": "temperature_2m", "value": 1.0, "site_id": "shames_top_b"},
            {"location": "pow_o_meter", "time": t, "variable": "relative_humidity_2m", "value": 90.0, "site_id": "shames_top_b"},
            {"location": "pow_o_meter", "time": t, "variable": "snow_depth", "value": 0.1, "site_id": "shames_top_b"},
        ]
    return recs


def _run(tmp_path, *, avcan, pom):
    with mock.patch.object(ol.obs, "fetch_station_58", side_effect=avcan), \
         mock.patch.object(ol.obs, "fetch_powometer_v1", side_effect=pom):
        return ol.log_observations(data_dir=tmp_path)


def _logged(tmp_path):
    files = sorted((tmp_path / "observations").glob("obs_date=*/*.parquet"))
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def test_powometer_hours_are_logged_once_with_their_site(tmp_path):
    first = _run(tmp_path, avcan=lambda **_: [dict(r) for r in AVCAN], pom=lambda: _pom(17, 18))
    assert first["per_source"] == {"avalanche_canada": 1, "powometer_v1": 6}

    # Next run: powOmeter serves 17-19 again; only hour 19 is new.
    with mock.patch.object(ol.om, "utcnow", return_value=pd.Timestamp("2026-10-16T23:00Z").to_pydatetime()):
        second = _run(tmp_path, avcan=lambda **_: [dict(r) for r in AVCAN], pom=lambda: _pom(17, 18, 19))
    assert second["per_source"]["powometer_v1"] == 3

    df = _logged(tmp_path)
    top = df[df["location"] == "pow_o_meter"]
    assert set(top["source"]) == {"powometer_v1"}
    assert set(top["variable"]) == {"temperature_2m", "relative_humidity_2m", "snow_depth"}
    assert set(top["site"]) == {"shames_top_b"}
    assert df.loc[df["location"] == "station_58", "site"].isna().all()
    assert not top.duplicated(["time", "variable"]).any()


def test_log_reader_handles_files_without_a_site_column(tmp_path):
    part = tmp_path / "observations" / "obs_date=2026-10-01"
    part.mkdir(parents=True)
    pd.DataFrame([{"location": "pow_o_meter", "time": "2026-10-01T00:00:00Z",
                   "variable": "temperature_2m", "value": 1.0, "source": "powometer_v1",
                   "fetched_at": "2026-10-01T01:00:00Z"}]
                 ).to_parquet(part / "obs_20261001T010000Z.parquet")
    _run(tmp_path, avcan=lambda **_: [], pom=lambda: _pom(17))
    temps = ol.load_observation_log(tmp_path, location="pow_o_meter", variable="temperature_2m")
    assert [r["site"] for r in temps] == [None, "shames_top_b"]
    snow = ol.load_observation_log(tmp_path, location="pow_o_meter", variable="snow_depth")
    assert [(r["site"], r["value"]) for r in snow] == [("shames_top_b", 0.1)]


def test_one_failing_source_keeps_the_other(tmp_path):
    def avcan_down(**_):
        raise requests.ConnectionError("down")

    summary = _run(tmp_path, avcan=avcan_down, pom=lambda: _pom(17))
    assert list(summary["failed"]) == ["avalanche_canada"]
    assert summary["n_records"] == 3
    assert set(_logged(tmp_path)["location"]) == {"pow_o_meter"}
