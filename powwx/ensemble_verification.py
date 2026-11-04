"""Ensemble (GEPS) verification: is the 21-member GEM ensemble useful here?

Pure functions over the ensemble log (``data/ensembles/``) and the observation
frame. Per forecast instance ``(issued_at, valid_time)`` the members give:

- ``mean``: the ensemble mean, scored like a single model (absolute error);
- ``spread``: the member standard deviation; a well-calibrated ensemble has an
  average spread close to the RMSE of its mean (spread/skill ratio about 1);
- ``crps``: the continuous ranked probability score, the "fair" form for a
  finite ensemble (Ferro 2014). In the variable's units, and equal to the
  absolute error for a single forecast, so it compares directly with a
  deterministic model's MAE: lower is better;
- ``inside``: whether the observation fell inside the central 80 % of the
  members (should happen about 80 % of the time).

Each is also given after removing the ensemble's bias (``*_bc``): all members
shifted by the mean error at that lead day over the previous BIAS_WINDOW_DAYS,
using only hours observed before the forecast was fetched (strictly causal).

Lead time counts from the run's start, as for the deterministic models.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import verification as ver

COVERAGE_LEVEL = 0.8
MIN_MEMBERS = 15          # skip instances with fewer members (a partial fetch)
MIN_DAYS = 5              # a lead day needs forecasts issued on this many days
BIAS_WINDOW_DAYS = 30     # trailing window for the bias correction
BIAS_MIN_N = 48           # matched hours needed before a bias is trusted
SAME_JOB = pd.Timedelta(minutes=30)  # ensemble fetched just after the models, same job

_COLS = ["issued_at", "valid_time", "lead_day", "observed", "mean", "spread", "crps",
         "inside", "n_members", "bias", "crps_bc", "inside_bc"]


def _crps(x: np.ndarray, y: np.ndarray, n: np.ndarray, pair: np.ndarray) -> np.ndarray:
    # Fair CRPS: E|X - y| - E|X - X'| / 2 with the unbiased pair term.
    return np.nanmean(np.abs(x - y[:, None]), axis=1) - pair / (2 * n * (n - 1))


def causal_bias(valid: pd.Series, issued: pd.Series, err: pd.Series, *,
                window_days: int = BIAS_WINDOW_DAYS, min_n: int = BIAS_MIN_N) -> np.ndarray:
    """For each row, the mean ``err`` of the rows whose valid time is before this
    row's issue time and within the trailing window; NaN with fewer than ``min_n``."""
    epoch = pd.Timestamp("1970-01-01", tz="UTC")
    secs = lambda s: ((s - epoch) // pd.Timedelta(seconds=1)).to_numpy(dtype=np.int64)  # noqa: E731
    vs = secs(valid)
    order = np.argsort(vs)
    v = vs[order]
    c = np.concatenate([[0.0], np.cumsum(err.to_numpy(dtype=float)[order])])
    t = secs(issued)
    hi = np.searchsorted(v, t, side="left")
    lo = np.searchsorted(v, t - window_days * 86400, side="left")
    n = hi - lo
    with np.errstate(invalid="ignore", divide="ignore"):
        out = (c[hi] - c[lo]) / n
    out[n < min_n] = np.nan
    return out


def instance_scores(ens: pd.DataFrame, obs: pd.DataFrame, *,
                    tolerance: pd.Timedelta = ver.DEFAULT_TOLERANCE,
                    level: float = COVERAGE_LEVEL) -> pd.DataFrame:
    """Score each ensemble forecast instance against the nearest observation.

    ``ens`` is :func:`verification.load_forecast_log` output for the ensemble
    log (members as ``<id>_memberNN`` models). Returns one row per instance with
    the columns in ``_COLS``; hours already past at fetch time are dropped."""
    if ens.empty or obs.empty:
        return pd.DataFrame(columns=_COLS)
    e = ens[ens["valid_time"] >= ens["issued_at"]].copy()
    e["_start"] = ver.run_start(e)
    wide = e.pivot_table(index=["issued_at", "valid_time", "_start"], columns="model",
                         values="value", aggfunc="last")
    x = wide.to_numpy(dtype=float)
    n = np.sum(~np.isnan(x), axis=1)
    keep = n >= MIN_MEMBERS
    wide, x, n = wide[keep], x[keep], n[keep]
    if not len(wide):
        return pd.DataFrame(columns=_COLS)

    inst = wide.index.to_frame(index=False)
    inst["_i"] = np.arange(len(inst))
    m = pd.merge_asof(inst.sort_values("valid_time"), obs.sort_values("time"),
                      left_on="valid_time", right_on="time",
                      direction="nearest", tolerance=tolerance).dropna(subset=["observed"])
    if m.empty:
        return pd.DataFrame(columns=_COLS)
    idx = m["_i"].to_numpy()
    x, n = x[idx], n[idx]
    y = m["observed"].to_numpy(dtype=float)
    pair = np.nansum(np.abs(x[:, :, None] - x[:, None, :]), axis=(1, 2))
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    lo, hi = np.nanquantile(x, lo_q, axis=1), np.nanquantile(x, hi_q, axis=1)

    out = m[["issued_at", "valid_time", "_start", "observed"]].reset_index(drop=True)
    lead_h = (out["valid_time"] - out["_start"]).dt.total_seconds() / 3600.0
    out["lead_day"] = (lead_h / 24.0).round().astype(int)
    out["mean"] = np.nanmean(x, axis=1)
    out["spread"] = np.nanstd(x, axis=1, ddof=1)
    out["crps"] = _crps(x, y, n, pair)
    out["inside"] = (y >= lo) & (y <= hi)
    out["n_members"] = n

    # Bias-corrected: shifting every member by b is the same as shifting y by +b.
    bias = np.full(len(out), np.nan)
    err = out["mean"] - out["observed"]
    for _, g in out.groupby("lead_day"):
        bias[g.index] = causal_bias(g["valid_time"], g["issued_at"], err[g.index])
    out["bias"] = bias
    yb = y + np.nan_to_num(bias)
    out["crps_bc"] = np.where(np.isnan(bias), np.nan, _crps(x, yb, n, pair))
    out["inside_bc"] = np.where(np.isnan(bias), np.nan, (yb >= lo) & (yb <= hi))
    return out[_COLS]


def _same_job(ens_issued: pd.Series, det_issued: pd.Series) -> pd.Series:
    """Map each ensemble fetch time to the deterministic fetch of the same logger
    job (the newest one up to SAME_JOB earlier); NaT when there is none."""
    e = pd.DataFrame({"e": sorted(ens_issued.unique())})
    d = pd.DataFrame({"d": sorted(det_issued.unique())})
    if e.empty or d.empty:
        return pd.Series(pd.NaT, index=ens_issued.index)
    m = pd.merge_asof(e, d, left_on="e", right_on="d", direction="backward",
                      tolerance=SAME_JOB)
    return ens_issued.map(dict(zip(m["e"], m["d"])))


def _mae_on(det_ld: pd.DataFrame, subset: pd.DataFrame, model: str | None,
            min_n: int) -> float | None:
    if not model or len(subset) < min_n:
        return None
    keys = set(zip(subset["det_issued"], subset["valid_time"]))
    d = det_ld[det_ld["model"] == model]
    d = d[[k in keys for k in zip(d["det_issued"], d["valid_time"])]]
    return round(float(d["abs_error"].mean()), 3) if len(d) >= 0.95 * len(subset) else None


def scores_by_lead(inst: pd.DataFrame, det_aligned: pd.DataFrame, *, min_n: int = 20,
                   min_days: int = MIN_DAYS, sibling: str | None = None,
                   blend_id: str | None = None) -> list[dict]:
    """Per lead day: the ensemble's scores (raw and bias-corrected) and, on exactly
    the same forecast hours from the same logger job, the deterministic MAEs: the
    best raw model, ``sibling`` (GEM GDPS for GEPS) and ``blend_id`` (the powWX
    blend, also bias-corrected and causal). ``det_aligned`` holds aligned
    deterministic pairs (live rows, optionally plus blend pseudo-pairs)."""
    if inst.empty:
        return []
    det = det_aligned[["model", "issued_at", "valid_time", "abs_error"]]
    inst = inst.assign(det_issued=_same_job(inst["issued_at"], det["issued_at"]))
    keyed = inst.merge(det.rename(columns={"issued_at": "det_issued"}),
                       on=["det_issued", "valid_time"], how="left")
    rows = []
    for ld, g in inst.groupby("lead_day"):
        days = g["issued_at"].dt.floor("D").nunique()
        if len(g) < min_n or days < min_days:
            continue
        err = g["mean"] - g["observed"]
        rmse = float(np.sqrt(np.mean(err ** 2)))
        spread = float(np.sqrt(np.mean(g["spread"] ** 2)))
        bc = g.dropna(subset=["bias"])
        if bc["issued_at"].dt.floor("D").nunique() < min_days:
            bc = bc.iloc[0:0]   # too few days to say anything after the correction
        err_bc = bc["mean"] - bc["bias"] - bc["observed"]
        rmse_bc = float(np.sqrt(np.mean(err_bc ** 2))) if len(bc) else None
        spread_bc = float(np.sqrt(np.mean(bc["spread"] ** 2))) if len(bc) else None
        det_ld = keyed[keyed["lead_day"] == ld].dropna(subset=["model"])
        # Only models with a forecast for (nearly) every instance compete, so a
        # short-range model isn't compared on a subset.
        stats = det_ld.groupby("model")["abs_error"].agg(["mean", "size"])
        stats = stats[stats["size"] >= 0.95 * len(g)]
        raw = stats.drop(index=[blend_id], errors="ignore")
        best = raw["mean"].idxmin() if len(raw) else None
        r3 = lambda v: None if v is None or pd.isna(v) else round(float(v), 3)  # noqa: E731
        rows.append({
            "lead_day": int(ld), "n": int(len(g)), "days": int(days),
            "crps": r3(g["crps"].mean()),
            "mean_mae": r3(err.abs().mean()),
            "bias": r3(err.mean()),
            "spread": r3(spread),
            "spread_skill": round(spread / rmse, 2) if rmse > 0 else None,
            "coverage": r3(g["inside"].mean()),
            "n_bc": int(len(bc)),
            "crps_bc": r3(bc["crps_bc"].mean()) if len(bc) >= min_n else None,
            "mean_mae_bc": r3(err_bc.abs().mean()) if len(bc) >= min_n else None,
            "spread_skill_bc": (round(spread_bc / rmse_bc, 2)
                                if len(bc) >= min_n and rmse_bc else None),
            "coverage_bc": r3(bc["inside_bc"].mean()) if len(bc) >= min_n else None,
            "best_model": best,
            "best_mae": r3(raw.loc[best, "mean"]) if best else None,
            "sibling_mae": r3(stats.loc[sibling, "mean"]) if sibling in stats.index else None,
            "blend_mae": r3(stats.loc[blend_id, "mean"]) if blend_id in stats.index else None,
            # The blend on the bias-corrected hours only, to compare with crps_bc.
            "blend_mae_bc": _mae_on(det_ld, bc, blend_id, min_n),
        })
    return rows
