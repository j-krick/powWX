"""Ensemble scoring: CRPS, coverage, the causal bias and same-job matching."""

import numpy as np
import pandas as pd

from powwx import ensemble_verification as ev
from powwx import verification as ver


def _ens(issued, valid, values, init=None):
    rows = [{"model": f"geps_member{i:02d}", "issued_at": issued, "valid_time": valid,
             "value": v, "source": "live"} for i, v in enumerate(values)]
    df = pd.DataFrame(rows)
    for c in ("issued_at", "valid_time"):
        df[c] = pd.to_datetime(df[c], utc=True)
    df["init_time"] = pd.to_datetime(init or issued, utc=True)
    return df


def test_crps_of_identical_members_is_the_absolute_error():
    ens = _ens("2026-10-20T06:00Z", "2026-10-21T06:00Z", [2.0] * 20)
    obs = ver.observations_frame([{"time": "2026-10-21T06:00:00Z", "value": 0.5}])
    out = ev.instance_scores(ens, obs)
    assert len(out) == 1
    r = out.iloc[0]
    assert abs(r["crps"] - 1.5) < 1e-9 and r["spread"] == 0 and not r["inside"]
    assert r["lead_day"] == 1


def test_fair_crps_matches_the_formula_and_coverage_uses_the_central_80pct():
    vals = list(np.linspace(-2, 2, 21))
    ens = _ens("2026-10-20T06:00Z", "2026-10-21T06:00Z", vals)
    obs = ver.observations_frame([{"time": "2026-10-21T06:00:00Z", "value": 0.3}])
    r = ev.instance_scores(ens, obs).iloc[0]
    x = np.array(vals)
    expected = np.mean(np.abs(x - 0.3)) - np.abs(x[:, None] - x[None, :]).sum() / (2 * 21 * 20)
    assert abs(r["crps"] - expected) < 1e-9
    assert r["inside"]                      # 0.3 is well inside [-1.6, 1.6]


def test_too_few_members_are_skipped():
    ens = _ens("2026-10-20T06:00Z", "2026-10-21T06:00Z", [1.0] * 10)
    obs = ver.observations_frame([{"time": "2026-10-21T06:00:00Z", "value": 0.5}])
    assert ev.instance_scores(ens, obs).empty


def test_causal_bias_uses_only_hours_observed_before_the_fetch():
    valid = pd.Series(pd.to_datetime(["2026-10-01T00:00Z", "2026-10-02T00:00Z",
                                      "2026-10-03T00:00Z"], utc=True))
    issued = pd.Series(pd.to_datetime(["2026-09-30T00:00Z", "2026-10-02T12:00Z",
                                       "2026-10-02T12:00Z"], utc=True))
    err = pd.Series([1.0, 3.0, 100.0])
    b = ev.causal_bias(valid, issued, err, window_days=30, min_n=1)
    assert np.isnan(b[0])                   # nothing observed yet
    assert b[1] == 2.0 and b[2] == 2.0      # mean of the first two; never its own hour


def test_same_job_pairs_the_ensemble_with_the_models_fetched_just_before():
    ens = pd.Series(pd.to_datetime(["2026-11-04T13:27:07Z", "2026-11-04T20:00:00Z"], utc=True))
    det = pd.Series(pd.to_datetime(["2026-11-04T13:27:00Z", "2026-11-04T03:48:57Z"], utc=True))
    got = ev._same_job(ens, det)
    assert got.iloc[0] == pd.Timestamp("2026-11-04T13:27:00Z")
    assert pd.isna(got.iloc[1])             # no model fetch within 30 min before
