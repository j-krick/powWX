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


def test_negative_lead_hours_do_not_land_in_lead_day_0():
    # A live fetch at 06:00Z gets the day from 00:00Z: hours before the fetch are
    # hindcast, not forecast, and must not be scored as same-day skill (review F5).
    forecasts = pd.DataFrame([
        _fc("m1", "2025-01-02T06:00:00Z", "2025-01-02T00:00:00Z", 1.0, source="live"),  # -6 h
        _fc("m1", "2025-01-02T06:00:00Z", "2025-01-02T12:00:00Z", 1.0, source="live"),  # +6 h
    ])
    forecasts["issued_at"] = pd.to_datetime(forecasts["issued_at"], utc=True)
    forecasts["valid_time"] = pd.to_datetime(forecasts["valid_time"], utc=True)
    obs = ver.observations_frame([
        {"time": "2025-01-02T00:00:00Z", "value": 0.0},
        {"time": "2025-01-02T12:00:00Z", "value": 0.0},
    ])
    aligned = ver.align_to_observations(forecasts, obs, tolerance=pd.Timedelta(minutes=30))
    assert (aligned["lead_hours"] >= 0).all()
    assert aligned["lead_day"].tolist() == [0]           # only the +6 h forecast


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

    assert set(by_lead["lead_day"]) == {1}


def _aligned(rows):
    """Build an aligned-shaped frame directly (model, valid_time UTC, observed,
    error, abs_error, lead_day) for stratification tests."""
    df = pd.DataFrame(rows)
    df["valid_time"] = pd.to_datetime(df["valid_time"], utc=True)
    df["abs_error"] = df["error"].abs()
    return df


def test_add_strata_season_obs_and_local_time_of_day():
    a = _aligned([
        # Jan, UTC noon -> PST local 04:00 -> Night; cold -> "≤ −10"
        {"model": "m1", "valid_time": "2025-01-15T12:00:00Z", "observed": -15.0, "error": 0.0, "lead_day": 1},
        # Jul, UTC midnight -> PDT local 17:00 prev day -> Afternoon; mild -> "0 to 10"
        {"model": "m1", "valid_time": "2025-07-15T00:00:00Z", "observed": 5.0, "error": 0.0, "lead_day": 1},
    ])
    s = ver.add_strata(a, variable="temperature_2m")
    assert s.loc[0, "season"] == "Winter" and s.loc[1, "season"] == "Summer"
    assert s.loc[0, "part_of_day"] == "Night" and s.loc[1, "part_of_day"] == "Afternoon"
    assert s.loc[0, "obs_temp"] == "≤ −10" and s.loc[1, "obs_temp"] == "0 to 10"


def test_stratified_metrics_fixed_lead_and_winner():
    rows = []
    for i in range(30):
        vt = f"2025-01-{1 + i % 28:02d}T12:00:00Z"
        rows.append({"model": "m1", "valid_time": vt, "observed": -12.0, "error": 0.0, "lead_day": 1})
        rows.append({"model": "m2", "valid_time": vt, "observed": -12.0, "error": 3.0, "lead_day": 1})
        # day-2 rows should be ignored by the default lead window
        rows.append({"model": "m2", "valid_time": vt, "observed": -12.0, "error": 0.0, "lead_day": 2})
    s = ver.add_strata(_aligned(rows), variable="temperature_2m")
    sm = ver.stratified_metrics(s, by="season", min_n=10)
    m2 = sm[sm["model"] == "m2"].iloc[0]
    assert m2["season"] == "Winter"
    assert m2["mae"] == 3.0          # day-2 (error 0) excluded -> stays 3.0


def test_empty_inputs_are_safe():
    empty = ver.align_to_observations(
        pd.DataFrame(columns=["model", "issued_at", "valid_time", "value", "source"]),
        ver.observations_frame([]),
    )
    assert empty.empty
    assert ver.metrics_by_lead(empty).empty
    assert ver.overall_metrics(empty).empty


# --- fair ranking (review F2, F7; F19 item 3) ------------------------------- #

def _pairs(model, lead_day, abs_errors, *, days=20, start="2025-01-01"):
    """Matched pairs for one model at one lead day: one per hour of ``days`` days,
    cycling through ``abs_errors`` (all positive errors)."""
    vts = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    errs = [abs_errors[i % len(abs_errors)] for i in range(len(vts))]
    return pd.DataFrame({
        "model": model, "lead_day": lead_day, "valid_time": vts,
        "issued_at": vts - pd.Timedelta(days=lead_day), "error": errs, "abs_error": errs,
    })


def test_short_horizon_model_wins_pooled_but_not_lead_1():
    # S only forecasts days 0-1 and is great at day 0; L forecasts days 0-5 and is
    # better at day 1 but poor later. Pooled MAE favours S (0.6 vs 1.55); a fair
    # day-1 ranking must name L.
    a = pd.concat(
        [_pairs("S", 0, [0.2]), _pairs("S", 1, [1.0]),
         _pairs("L", 0, [0.5]), _pairs("L", 1, [0.8])]
        + [_pairs("L", d, [2.0]) for d in range(2, 6)], ignore_index=True)
    overall = ver.overall_metrics(a)
    assert overall.iloc[0]["model"] == "S"
    by_lead = {w["lead_day"]: w for w in ver.winners_by_lead(a)}
    assert by_lead[1]["model"] == "L" and by_lead[1]["decisive"]
    assert by_lead[0]["model"] == "S" and by_lead[0]["decisive"]
    assert not by_lead[2]["decisive"]          # L alone at day 2: nothing to beat


def test_close_models_are_a_tie():
    # Same error distribution, shuffled: a tiny MAE gap is noise, not a winner.
    a = pd.concat([_pairs("A", 1, [0.4, 1.9, 1.1, 0.2, 2.5, 0.9]),
                   _pairs("B", 1, [1.1, 0.4, 2.5, 0.9, 0.2, 1.9])], ignore_index=True)
    w = ver.rank_with_ties(a)
    assert not w["decisive"] and sorted(w["tied"]) == ["A", "B"]


def test_paired_comparison_uses_only_shared_forecasts():
    a = _pairs("A", 1, [1.0], days=20)
    b = pd.concat([_pairs("B", 1, [2.0], days=10),                       # shared with A
                   _pairs("B", 1, [0.0], days=10, start="2026-01-01")])  # B only: ignored
    c = ver.paired_comparison(a, b)
    assert c["n"] == 10 * 24 and c["days"] == 10
    assert c["mae_a"] == 1.0 and c["mae_b"] == 2.0 and c["pct"] == 50.0
