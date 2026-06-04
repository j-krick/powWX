"""Unit tests for the verification join/metrics core (no network, synthetic data).

These lock down the correctness-critical semantics: the tolerance match, the
error *sign* (forecast − observed), lead-day binning, and the aggregates.
"""

import pandas as pd

from powwx import verification as ver


def _fc(model, issued, valid, value, source="previous_runs"):
    return {"model": model, "issued_at": issued, "valid_time": valid,
            "value": value, "source": source}


def test_align_matches_within_tolerance_and_drops_outside():
    forecasts = pd.DataFrame([
        _fc("m1", "2025-01-01T00:00:00Z", "2025-01-02T00:00:00Z", 5.0),   # obs at 00:10 -> match
        _fc("m1", "2025-01-01T00:00:00Z", "2025-01-02T06:00:00Z", 8.0),   # obs 2h away -> drop
    ])
    forecasts["issued_at"] = pd.to_datetime(forecasts["issued_at"], utc=True)
    forecasts["valid_time"] = pd.to_datetime(forecasts["valid_time"], utc=True)

    obs = ver.observations_frame([
        {"time": "2025-01-02T00:10:00Z", "value": 4.0},
        {"time": "2025-01-02T08:00:00Z", "value": 9.0},
    ])

    aligned = ver.align_to_observations(forecasts, obs, tolerance=pd.Timedelta(minutes=30))
    assert len(aligned) == 1                      # the far one is dropped
    row = aligned.iloc[0]
    assert row["observed"] == 4.0
    assert row["error"] == 1.0                    # forecast 5 − observed 4
    assert row["lead_day"] == 1                   # exactly one day ahead


def test_lead_day_rounds_to_nearest_whole_day():
    forecasts = pd.DataFrame([
        _fc("m1", "2025-01-01T00:00:00Z", "2025-01-02T12:00:00Z", 1.0),  # 36h -> rounds to 2
        _fc("m1", "2025-01-01T00:00:00Z", "2025-01-02T10:00:00Z", 1.0),  # 34h -> rounds to 1
    ])
    forecasts["issued_at"] = pd.to_datetime(forecasts["issued_at"], utc=True)
    forecasts["valid_time"] = pd.to_datetime(forecasts["valid_time"], utc=True)
    obs = ver.observations_frame([
        {"time": "2025-01-02T12:00:00Z", "value": 0.0},
        {"time": "2025-01-02T10:00:00Z", "value": 0.0},
    ])
    aligned = ver.align_to_observations(forecasts, obs, tolerance=pd.Timedelta(minutes=30))
    assert sorted(aligned["lead_day"].tolist()) == [1, 2]


def test_metrics_and_best_by_lead():
    # m1 perfect, m2 always +2 warm, at lead day 1, with enough pairs to pass min_n.
    rows = []
    base = pd.Timestamp("2025-01-01T00:00:00Z")
    for i in range(30):
        valid = base + pd.Timedelta(days=1, hours=i)
        issued = valid - pd.Timedelta(days=1)
        rows.append(_fc("m1", issued.isoformat(), valid.isoformat(), 10.0))
        rows.append(_fc("m2", issued.isoformat(), valid.isoformat(), 12.0))
    forecasts = pd.DataFrame(rows)
    forecasts["issued_at"] = pd.to_datetime(forecasts["issued_at"], utc=True)
    forecasts["valid_time"] = pd.to_datetime(forecasts["valid_time"], utc=True)
    obs = ver.observations_frame(
        [{"time": (base + pd.Timedelta(days=1, hours=i)).isoformat(), "value": 10.0}
         for i in range(30)]
    )

    aligned = ver.align_to_observations(forecasts, obs, tolerance=pd.Timedelta(minutes=30))
    overall = ver.overall_metrics(aligned, min_n=10)
    by_lead = ver.metrics_by_lead(aligned, min_n=10)

    m1 = overall[overall["model"] == "m1"].iloc[0]
    m2 = overall[overall["model"] == "m2"].iloc[0]
    assert m1["mae"] == 0.0 and m1["bias"] == 0.0
    assert m2["mae"] == 2.0 and m2["bias"] == 2.0   # warm bias

    best = ver.best_by_lead(by_lead)
    assert best.iloc[0]["model"] == "m1"            # perfect model wins lead day 1


def test_empty_inputs_are_safe():
    empty = ver.align_to_observations(
        pd.DataFrame(columns=["model", "issued_at", "valid_time", "value", "source"]),
        ver.observations_frame([]),
    )
    assert empty.empty
    assert ver.metrics_by_lead(empty).empty
    assert ver.overall_metrics(empty).empty
