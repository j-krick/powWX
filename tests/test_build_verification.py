"""The verification build must survive a station whose observations can't be
fetched (review F1): it still writes all three JSON files, without that station."""

import importlib.util
import json

import numpy as np
import pandas as pd

from powwx import config as cfg

_spec = importlib.util.spec_from_file_location(
    "build_verification", cfg.REPO_ROOT / "scripts" / "build_verification.py")
bv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bv)


def _synthetic_forecasts(location, variable):
    """Two models, one run a day for 40 days, hourly out to 48 h."""
    rows = []
    for day in pd.date_range("2025-01-01", periods=40, freq="D", tz="UTC"):
        for h in range(49):
            vt = day + pd.Timedelta(hours=h)
            truth = 5 * np.sin(vt.hour / 24 * 2 * np.pi)
            rows.append({"model": "gfs_seamless", "issued_at": day, "valid_time": vt,
                         "value": truth + 1.0, "source": "previous_runs"})
            rows.append({"model": "icon_global", "issued_at": day, "valid_time": vt,
                         "value": truth - 0.5, "source": "previous_runs"})
    return pd.DataFrame(rows)


def _obs_log(data_dir, *, location, variable):
    """Station #58 reads fine; the top station's observations can't be read."""
    if location == "pow_o_meter":
        raise RuntimeError("observation log unreadable")
    times = pd.date_range("2025-01-01", periods=42 * 24, freq="h", tz="UTC")
    return [{"location": location, "variable": variable,
             "time": t.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "value": round(float(5 * np.sin(t.hour / 24 * 2 * np.pi)), 3)}
            for t in times]


def test_build_writes_all_outputs_when_a_station_fetch_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(bv.obs_logger, "load_observation_log", _obs_log)
    monkeypatch.setattr(bv.ver, "load_forecast_log",
                        lambda data_dir, *, variable, location: _synthetic_forecasts(location, variable))

    assert bv.main(out_dir=tmp_path) == 0

    for name in ("verification", "blend", "freezing_level"):
        assert (tmp_path / f"{name}.json").is_file()
    verification = json.loads((tmp_path / "verification.json").read_text(encoding="utf-8"))
    assert "station_58" in verification["locations"]       # the healthy station still verifies
    assert "pow_o_meter" not in verification["locations"]  # the broken one is left out
    fl_payload = json.loads((tmp_path / "freezing_level.json").read_text(encoding="utf-8"))
    assert fl_payload["estimate"] is None                  # needs both stations
