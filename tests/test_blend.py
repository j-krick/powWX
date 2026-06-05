"""Tests for the powWX blend — focused on the part that's easy to get wrong:
causality (no peeking at the future) and the bias-correct-then-weight math."""

import pandas as pd

from powwx import blend


def _pairs(model, dates, value, observed, lead_day=1):
    """Matched-pair rows for one model: constant forecast `value` vs `observed`."""
    rows = []
    for d in dates:
        vt = pd.Timestamp(d, tz="UTC")
        rows.append({
            "model": model, "issued_at": vt - pd.Timedelta(days=lead_day),
            "valid_time": vt, "value": float(value), "observed": float(observed),
            "error": float(value - observed), "abs_error": abs(value - observed),
            "lead_day": lead_day, "source": "x",
        })
    return rows


def test_learn_coefficients_is_causal():
    # m1 runs +4 warm. Pairs span Jan; ask for coefficients as of Jan 15.
    rows = _pairs("m1", pd.date_range("2025-01-01", "2025-01-31", freq="D"), 14.0, 10.0)
    a = pd.DataFrame(rows)
    coef = blend.learn_coefficients(a, asof=pd.Timestamp("2025-01-15", tz="UTC"),
                                    min_train=5)
    row = coef[coef["model"] == "m1"].iloc[0]
    assert round(row["bias"], 6) == 4.0
    # Only the pre-15th pairs may inform it: 14 days (Jan 1..14, valid_time < 15th).
    assert row["n"] == 14


def test_walk_forward_blend_corrects_bias_out_of_sample():
    # Two months. m1 always +4 warm, m2 perfect, both at the same valid times.
    dates = pd.date_range("2025-01-01", "2025-02-28", freq="D")
    a = pd.DataFrame(_pairs("m1", dates, 14.0, 10.0) + _pairs("m2", dates, 10.0, 10.0))
    blend_pairs = blend.walk_forward_blend(a, window_days=120, min_train=10, min_members=2)
    # Feb pairs are blended from Jan-trained coefficients (out-of-sample).
    feb = blend_pairs[blend_pairs["valid_time"] >= pd.Timestamp("2025-02-01", tz="UTC")]
    assert not feb.empty
    # Both members bias-corrected to ~10 -> blend ~10, error ~0 despite m1's +4 bias.
    assert feb["abs_error"].max() < 1e-6
    assert (blend_pairs["model"] == blend.BLEND_ID).all()


def test_no_blend_before_enough_training():
    # January can't be blended: nothing precedes it within the window.
    dates = pd.date_range("2025-01-01", "2025-01-31", freq="D")
    a = pd.DataFrame(_pairs("m1", dates, 14.0, 10.0) + _pairs("m2", dates, 10.0, 10.0))
    blend_pairs = blend.walk_forward_blend(a, window_days=120, min_train=10)
    jan = blend_pairs[blend_pairs["valid_time"] < pd.Timestamp("2025-02-01", tz="UTC")]
    assert jan.empty


def test_live_blend_weights_toward_lower_error_model():
    # 2025-03-01 00:00Z -> 16:00 local (PST) -> "Afternoon"; coef is keyed on that.
    coef = pd.DataFrame([
        {"model": "m1", "lead_day": 1, "part_of_day": "Afternoon", "bias": 0.0, "weight": 1 / 4.0},
        {"model": "m2", "lead_day": 1, "part_of_day": "Afternoon", "bias": 0.0, "weight": 1 / 1.0},
    ])
    live = pd.DataFrame([
        {"model": "m1", "valid_time": pd.Timestamp("2025-03-01T00:00Z"), "value": 0.0, "lead_day": 1},
        {"model": "m2", "valid_time": pd.Timestamp("2025-03-01T00:00Z"), "value": 10.0, "lead_day": 1},
    ])
    out = blend.live_blend(live, coef, min_members=2)
    # weighted mean = (0*0.25 + 10*1)/1.25 = 8.0 -> pulled toward the better model
    assert round(float(out.iloc[0]["value"]), 6) == 8.0


def test_residual_quantiles_and_coverage():
    # Blend pairs at lead 1 with errors spread -5..+5; 80% band -> ~[-4, +4].
    errs = list(range(-5, 6)) * 20  # 220 pairs, symmetric
    base = pd.Timestamp("2025-01-01T00:00Z")
    rows = [{"lead_day": 1, "error": float(e),
             "issued_at": base + pd.Timedelta(hours=i)} for i, e in enumerate(errs)]
    bp = pd.DataFrame(rows)
    q = blend.residual_quantiles(bp, level=0.8, min_n=20)
    lo, hi = q[1]
    assert lo < 0 < hi and abs(lo + hi) < 1.0          # roughly symmetric about 0
    cov = blend.band_coverage_oos(bp, level=0.8, split=0.7)
    assert cov is not None and 0.6 <= cov <= 0.95       # close to the 0.8 target
