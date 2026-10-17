"""RONI and AHCCD parsing, and saving a new copy only when it changed."""

from datetime import datetime, timezone

from powwx import climate_sources as cs


def test_parse_roni_table():
    df = cs.parse_roni("SEAS   YR  ANOM\nDJF  1950 -1.19\nJAS  2026  1.69\n")
    assert list(df.columns) == ["season", "year", "anom"]
    assert df.iloc[-1].to_dict() == {"season": "JAS", "year": 2026, "anom": 1.69}


def test_parse_ahccd_drops_missing_code():
    payload = {"features": [
        {"properties": {"date": "1912-02", "temp_mean__temp_moyenne": -9999.9}},
        {"properties": {"date": "1912-11", "temp_mean__temp_moyenne": -2.5}},
        {"properties": {"date": "1912-10", "temp_mean__temp_moyenne": 5.1}},
        {"properties": {"date": "1913-01", "temp_mean__temp_moyenne": None}}]}
    df = cs.parse_ahccd(payload)
    assert df["date"].tolist() == ["1912-10", "1912-11"]


def test_save_only_when_changed_and_load_latest(tmp_path):
    df = cs.parse_roni("SEAS YR ANOM\nJAS 2026 1.69\n")
    t1, t2 = datetime(2026, 10, 1, tzinfo=timezone.utc), datetime(2026, 10, 8, tzinfo=timezone.utc)
    assert cs.save_if_changed(df, data_dir=tmp_path, name="roni", prefix="roni", fetched_at=t1)
    assert cs.save_if_changed(df, data_dir=tmp_path, name="roni", prefix="roni", fetched_at=t2) is None
    revised = cs.parse_roni("SEAS YR ANOM\nJAS 2026 1.72\n")
    assert cs.save_if_changed(revised, data_dir=tmp_path, name="roni", prefix="roni", fetched_at=t2)
    assert cs.load_latest(tmp_path, name="roni", prefix="roni")["anom"].tolist() == [1.72]
    assert cs.load_latest(tmp_path, name="ahccd", prefix="x") is None
