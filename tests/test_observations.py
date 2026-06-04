"""Tests for the PCDS CSV parser — especially the DST-aware timezone handling,
which is the subtle, easy-to-get-wrong part (a fixed -8 offset is an hour wrong
all summer)."""

import textwrap

from powwx import observations as obs

# Minimal PCDS-shaped CSV: line 1 banner, line 2 header, "None" nulls, naive
# local times. One winter row (PST = UTC-8) and one summer row (PDT = UTC-7).
PCDS_SAMPLE = textwrap.dedent("""\
    station_observations
    air_temp, snw_dpth, snwfl_amt_pst1hr, pcpn_amt_pst1hr, rel_hum, time
    -3.0, 140.0, -1.0, 0.5, None, 2026-01-01 00:00:00
    12.5, 0.0, 0.0, 0.0, None, 2026-07-01 12:00:00
""")


def _write(tmp_path):
    p = tmp_path / "52401.csv"
    p.write_text(PCDS_SAMPLE, encoding="utf-8")
    return p


def test_pcds_dst_aware_times(tmp_path):
    recs = obs.parse_pcds_csv(_write(tmp_path))
    temps = {r["time"]: r["value"] for r in recs if r["variable"] == "temperature_2m"}
    # Jan (PST, UTC-8): 00:00 local -> 08:00Z. Jul (PDT, UTC-7): 12:00 local -> 19:00Z.
    assert temps["2026-01-01T08:00:00Z"] == -3.0
    assert temps["2026-07-01T19:00:00Z"] == 12.5


def test_pcds_units_and_clamp(tmp_path):
    recs = obs.parse_pcds_csv(_write(tmp_path))
    by = {(r["time"], r["variable"]): r["value"] for r in recs}
    # snow depth cm -> m
    assert by[("2026-01-01T08:00:00Z", "snow_depth")] == 1.4
    # negative snowfall (sensor noise) clamped to 0
    assert by[("2026-01-01T08:00:00Z", "snowfall")] == 0.0
    # precipitation passes through (mm)
    assert by[("2026-01-01T08:00:00Z", "precipitation")] == 0.5


def test_pcds_skips_all_null_variables(tmp_path):
    recs = obs.parse_pcds_csv(_write(tmp_path))
    # rel_hum is None in every row -> no relative_humidity_2m records emitted.
    assert not any(r["variable"] == "relative_humidity_2m" for r in recs)
    # location defaults to station_58.
    assert all(r["location"] == "station_58" for r in recs)
    assert all(r["source"] == "pcds" for r in recs)
