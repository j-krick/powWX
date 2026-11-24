"""Snow: score the models' snowfall against the measured change in snow depth,
and forecast snow depth at the stations (the "powWX snow blend").

Why not score the models' own snow depth? It describes the model's whole grid
cell and its own snowpack, which is often far from the station's (one model has
run 3-4x too deep all autumn, others melt theirs away). So the target here is the
**measured 24 h change in depth** at a station, and each model's **snowfall** over
the same 24 h is the predictor. Those have a long record: the forecast archive
has snowfall back to early 2025, and both stations have measured depth.

Per (model, lead day) a straight line maps snowfall to depth change::

    change ≈ a + b · snowfall      (a < 0 is settling/melt, b < 1 is settling of new snow)

The blend is the inverse-MAE²-weighted mean of those calibrated predictions, like
the temperature blend. The depth forecast starts from the latest measured depth
and adds the blended change day by day (never below 0).

Daily windows end at END_HOUR_UTC (16 UTC = 08:00 PST, a morning snow report).
"Lead day N" for a window = hours forecast N to N+1 days after the run started
(the archive's "previous day N").

**Strictly causal.** :func:`walk_forward` refits at the start of every month on
earlier days only, so every score and the band come from out-of-sample forecasts.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

END_HOUR_UTC = 16
MAX_LEAD = 7            # forecast / score lead days 1..MAX_LEAD
MIN_TRAIN = 60          # matched days needed to fit a (model, lead) line
MIN_R = 0.2             # a model whose snowfall barely tracks depth change is left out
MAE_FLOOR_CM = 1.0      # floor so a fluke-small error can't dominate the weights
WEIGHT_POWER = 2.0     # w ∝ 1/MAE**POWER
ACTIVE_SNOWFALL_CM = 1.0
BAND_LEVEL = 0.8
MIN_BAND_N = 40
MAX_ANCHOR_AGE_H = 36   # no depth forecast from a reading older than this
ANCHOR_HOURS = 5        # depth at a time = median of the readings in the hours up to it
BLEND_ID = "powwx_blend"
PERSISTENCE_ID = "persistence"

_DAY = pd.Timedelta(days=1)


def window_end(t: pd.Series | pd.DatetimeIndex) -> pd.Series:
    """The end of the daily window an hour (accumulated to ``t``) belongs to:
    windows are (end − 24 h, end] with end at END_HOUR_UTC."""
    t = pd.Series(t)
    shifted = t - pd.Timedelta(hours=END_HOUR_UTC)
    return shifted.dt.ceil("D") + pd.Timedelta(hours=END_HOUR_UTC)


# --------------------------------------------------------------------------- #
# Observations
# --------------------------------------------------------------------------- #

def depth_at(hourly_m: pd.Series, times) -> pd.Series:
    """Snow depth in cm at ``times``: median of the readings in the ANCHOR_HOURS up
    to each (needs 3); NaN where there are too few."""
    s = hourly_m.dropna().sort_index()
    times = pd.DatetimeIndex(times)
    if s.empty:
        return pd.Series(np.nan, index=times)
    out = []
    for t in times:
        w = s[(s.index > t - pd.Timedelta(hours=ANCHOR_HOURS)) & (s.index <= t)]
        out.append(float(w.median()) * 100 if len(w) >= 3 else np.nan)
    return pd.Series(out, index=times)


def observed_changes(hourly_by_site: dict[str, pd.Series]) -> pd.DataFrame:
    """Measured 24 h depth change (cm) per window, ``[end, depth_start, depth_end,
    change, site]``. Changes are only taken within one site (never across a move)."""
    frames = []
    for site, s in hourly_by_site.items():
        s = s.dropna()
        if s.empty:
            continue
        first = window_end(pd.DatetimeIndex([s.index.min()]))[0]
        last = window_end(pd.DatetimeIndex([s.index.max()]))[0]
        ends = pd.date_range(first, last, freq="D")
        d = depth_at(s, ends)
        df = pd.DataFrame({"end": ends, "depth_end": d.to_numpy()})
        df["depth_start"] = d.shift(1).to_numpy()
        df["change"] = df["depth_end"] - df["depth_start"]
        df["site"] = site
        frames.append(df.dropna(subset=["change"]))
    if not frames:
        return pd.DataFrame(columns=["end", "depth_start", "depth_end", "change", "site"])
    return pd.concat(frames, ignore_index=True)


# --------------------------------------------------------------------------- #
# Forecasts
# --------------------------------------------------------------------------- #

def daily_snowfall(fc: pd.DataFrame, *, min_hours: int = 20) -> pd.DataFrame:
    """Per (model, lead_day, end): the forecast snowfall (cm) over that window.
    ``fc`` is a forecast-log frame with ``init_time``. Each hour's value is the
    mean over runs at that hour's lead day; windows with fewer than ``min_hours``
    hours are dropped (sums scaled to 24 h)."""
    cols = ["model", "lead_day", "end", "snowfall"]
    f = fc.dropna(subset=["value"])
    if f.empty:
        return pd.DataFrame(columns=cols)
    lead_h = (f["valid_time"] - f["init_time"]).dt.total_seconds() / 3600
    f = f.loc[lead_h > 0, ["model", "valid_time", "value"]].copy()
    f["lead_day"] = (lead_h[f.index] // 24).astype(int)
    f = f[(f["lead_day"] >= 1) & (f["lead_day"] <= MAX_LEAD)]
    f["end"] = window_end(f["valid_time"]).to_numpy()
    hourly = f.groupby(["model", "lead_day", "end", "valid_time"])["value"].mean().reset_index()
    d = hourly.groupby(["model", "lead_day", "end"])["value"].agg(["sum", "count"]).reset_index()
    d = d[d["count"] >= min_hours]
    d["snowfall"] = d["sum"] * 24 / d["count"]
    return d[cols].reset_index(drop=True)


def pairs(daily_fc: pd.DataFrame, changes: pd.DataFrame) -> pd.DataFrame:
    """Join forecast snowfall to the measured change. ``active`` marks the days
    that count for scoring: snow on the ground at either end of the window, or
    some model forecasting snow (bare ground with nothing forecast is too easy)."""
    m = daily_fc.merge(changes, on="end", how="inner")
    if m.empty:
        return m.assign(active=pd.Series(dtype=bool))
    most = m.groupby(["end", "lead_day", "site"])["snowfall"].transform("max")
    m["active"] = (m["depth_start"] > 0) | (m["depth_end"] > 0) | (most >= ACTIVE_SNOWFALL_CM)
    return m


# --------------------------------------------------------------------------- #
# Fit, blend, walk forward
# --------------------------------------------------------------------------- #

def fit(train: pd.DataFrame, *, min_n: int = MIN_TRAIN, min_r: float = MIN_R) -> pd.DataFrame:
    """Per (model, lead_day): change ≈ a + b · snowfall on the active days, with
    its in-sample MAE as the weight basis. Models whose snowfall hardly tracks
    the change (r < ``min_r``) or with too few days are left out."""
    cols = ["model", "lead_day", "a", "b", "mae", "r", "n", "weight"]
    rows = []
    t = train[train["active"]]
    for (model, lead), g in t.groupby(["model", "lead_day"]):
        x, y = g["snowfall"].to_numpy(float), g["change"].to_numpy(float)
        if len(g) < min_n or x.std() == 0 or y.std() == 0:
            continue
        r = float(np.corrcoef(x, y)[0, 1])
        if not r >= min_r:
            continue
        b, a = np.polyfit(x, y, 1)
        mae = float(np.abs(y - (a + b * x)).mean())
        rows.append({"model": model, "lead_day": int(lead), "a": float(a), "b": float(b),
                     "mae": mae, "r": r, "n": len(g),
                     "weight": 1.0 / max(mae, MAE_FLOOR_CM) ** WEIGHT_POWER})
    return pd.DataFrame(rows, columns=cols)


def predict(rows: pd.DataFrame, coef: pd.DataFrame) -> pd.DataFrame:
    """Calibrated per-model change for ``rows`` (needs model, lead_day, snowfall;
    optional ``frac`` = share of the window still ahead, default 1) plus the
    weighted blend per remaining key. Returns per-model rows with ``pred`` and
    the blend as model BLEND_ID."""
    if rows.empty or coef.empty:
        return rows.iloc[0:0].assign(pred=pd.Series(dtype=float))
    m = rows.merge(coef[["model", "lead_day", "a", "b", "weight"]], on=["model", "lead_day"])
    frac = m["frac"] if "frac" in m else 1.0
    m["pred"] = m["a"] * frac + m["b"] * m["snowfall"]
    keys = [c for c in rows.columns if c not in ("model", "snowfall", "frac")]
    m["wp"] = m["weight"] * m["pred"]
    b = m.groupby(keys, dropna=False).agg(wp=("wp", "sum"), w=("weight", "sum"),
                                          n_models=("model", "size")).reset_index()
    b["pred"] = b["wp"] / b["w"]
    b["model"] = BLEND_ID
    per_model = m.drop(columns=["a", "b", "weight", "wp"])
    return pd.concat([per_model, b.drop(columns=["wp", "w"])], ignore_index=True)


def walk_forward(p: pd.DataFrame) -> pd.DataFrame:
    """Out-of-sample predictions: for each month, fit on windows that ended before
    it and predict that month's windows. Predicted change is floored so depth
    can't go below 0. Returns the prediction rows (per model + blend) with the
    measured change."""
    if p.empty:
        return p.assign(pred=pd.Series(dtype=float))
    p = p.copy()
    month = p["end"].dt.tz_convert(None).dt.to_period("M")
    out = []
    for per in sorted(month.unique()):
        start = per.to_timestamp().tz_localize("UTC")
        coef = fit(p[p["end"] < start])
        if coef.empty:
            continue
        cur = p[month == per]
        keys = cur[["model", "lead_day", "end", "site", "snowfall", "depth_start", "change", "active"]]
        out.append(predict(keys, coef))
    if not out:
        return p.iloc[0:0].assign(pred=pd.Series(dtype=float))
    res = pd.concat(out, ignore_index=True)
    res["pred"] = np.maximum(res["pred"], -res["depth_start"])
    return res


# --------------------------------------------------------------------------- #
# Scores and band
# --------------------------------------------------------------------------- #

def scores_by_lead(oos: pd.DataFrame, p: pd.DataFrame, *, min_n: int = 30) -> list[dict]:
    """Per lead day on active days: the blend's and each model's out-of-sample MAE
    on the days the blend has, the persistence ("no change") MAE on the same days,
    and each model's raw snowfall/change correlation over all active days."""
    rows = []
    act = oos[oos["active"]]
    pa = p[p["active"]]
    for lead in range(1, MAX_LEAD + 1):
        bl = act[(act["model"] == BLEND_ID) & (act["lead_day"] == lead)]
        if len(bl) < min_n:
            continue
        key = bl.set_index(["end", "site"])
        row = {"lead_day": lead, "n": len(bl),
               "blend_mae": float((bl["pred"] - bl["change"]).abs().mean()),
               "persistence_mae": float(bl["change"].abs().mean()),
               "models": {}}
        row["skill_pct"] = 100 * (1 - row["blend_mae"] / row["persistence_mae"]) \
            if row["persistence_mae"] > 0 else None
        for model, g in act[(act["lead_day"] == lead) & (act["model"] != BLEND_ID)].groupby("model"):
            g = g[g.set_index(["end", "site"]).index.isin(key.index)]
            raw = pa[(pa["model"] == model) & (pa["lead_day"] == lead)]
            r = (float(np.corrcoef(raw["snowfall"], raw["change"])[0, 1])
                 if len(raw) >= min_n and raw["snowfall"].std() > 0 else None)
            if len(g) >= min_n:
                row["models"][model] = {"mae": float((g["pred"] - g["change"]).abs().mean()),
                                        "n": len(g), "r": r}
        rows.append(row)
    return rows


def cumulative_band(oos: pd.DataFrame, *, level: float = BAND_LEVEL,
                    min_n: int = MIN_BAND_N) -> dict[int, tuple[float, float]]:
    """(low, high) quantiles of the k-day cumulative error (measured − forecast
    depth change, cm) for k = 1..MAX_LEAD, from out-of-sample blend predictions:
    the window ending k days after an anchor uses lead day k. Only stretches where
    snow was on the ground or forecast count."""
    bl = oos[oos["model"] == BLEND_ID].set_index(["site", "lead_day", "end"]).sort_index()
    if bl.empty:
        return {}
    lo_q, hi_q = (1 - level) / 2, 1 - (1 - level) / 2
    out = {}
    anchors = bl.reset_index()[["site", "end"]].drop_duplicates()
    for k in range(1, MAX_LEAD + 1):
        errs = []
        for site, e0 in anchors.itertuples(index=False):
            parts = []
            for j in range(1, k + 1):
                key = (site, j, e0 + (j - 1) * _DAY)
                if key not in bl.index:
                    break
                parts.append(bl.loc[key])
            if len(parts) < k or not any(bool(r["active"]) for r in parts):
                continue
            errs.append(sum(float(r["change"]) - float(r["pred"]) for r in parts))
        if len(errs) >= min_n:
            out[k] = (float(np.quantile(errs, lo_q)), float(np.quantile(errs, hi_q)))
    return out


def band_coverage(oos: pd.DataFrame, *, level: float = BAND_LEVEL, split: float = 0.7) -> float | None:
    """Honest check of the band: quantiles from the earliest ``split`` of windows,
    coverage of the later ones (target = ``level``)."""
    bl_ends = oos.loc[oos["model"] == BLEND_ID, "end"]
    if bl_ends.empty:
        return None
    cut = bl_ends.quantile(split)
    q = cumulative_band(oos[oos["end"] <= cut], level=level)
    test = oos[oos["end"] > cut]
    if not q or test.empty:
        return None
    covered = total = 0
    bl = test[test["model"] == BLEND_ID].set_index(["site", "lead_day", "end"]).sort_index()
    for site, e0 in bl.reset_index()[["site", "end"]].drop_duplicates().itertuples(index=False):
        cum = 0.0
        active = False
        for k in range(1, MAX_LEAD + 1):
            key = (site, k, e0 + (k - 1) * _DAY)
            if key not in bl.index or k not in q:
                break
            r = bl.loc[key]
            cum += float(r["change"]) - float(r["pred"])
            active = active or bool(r["active"])
            if active:
                covered += int(q[k][0] <= cum <= q[k][1])
                total += 1
    return round(covered / total, 3) if total else None


# --------------------------------------------------------------------------- #
# Live depth forecast
# --------------------------------------------------------------------------- #

def skilful_leads(rows: list[dict]) -> int:
    """How many lead days, from day 1 on, the blend beats "no change" in a row."""
    n = 0
    for r in sorted(rows, key=lambda r: r["lead_day"]):
        if r["lead_day"] != n + 1 or not (r["skill_pct"] or 0) > 0:
            break
        n += 1
    return n


def latest_runs(fc: pd.DataFrame) -> pd.DataFrame:
    """Each model's newest live run."""
    live = fc[(fc["source"] == "live")].dropna(subset=["value"])
    if live.empty:
        return live
    newest = live.groupby("model")["init_time"].transform("max")
    run = live[live["init_time"] == newest]
    return run.drop_duplicates(["model", "valid_time"], keep="last")


def live_forecast(fc: pd.DataFrame, hourly_m: pd.Series, coef: pd.DataFrame,
                  band: dict[int, tuple[float, float]], *, now: pd.Timestamp,
                  max_lead: int = MAX_LEAD) -> dict | None:
    """Depth forecast (m) from the latest measured depth plus the blended change,
    hourly (linear between window ends) with the out-of-sample band.

    The first window is only partly ahead: its snowfall sum covers the hours after
    the latest reading and the settling term is scaled by that share. The line
    stops after ``max_lead`` days (where the blend stops beating "no change")."""
    s = hourly_m.dropna().sort_index()
    if s.empty or coef.empty or not band:
        return None
    t0 = s.index.max()
    if now - t0 > pd.Timedelta(hours=MAX_ANCHOR_AGE_H):
        return None
    d0 = float(depth_at(s, [t0]).iloc[0])
    if not np.isfinite(d0):
        return None
    run = latest_runs(fc)
    run = run[run["valid_time"] > t0]
    if run.empty:
        return None
    first_end = window_end(pd.DatetimeIndex([t0]))[0]
    if first_end == t0:
        first_end += _DAY
    ends = [first_end + j * _DAY for j in range(min(max_lead, MAX_LEAD))]
    rows = []
    for k, e in enumerate(ends, start=1):
        start = max(e - _DAY, t0)
        hours = (e - start) / pd.Timedelta(hours=1)
        w = run[(run["valid_time"] > start) & (run["valid_time"] <= e)]
        for model, g in w.groupby("model"):
            if len(g) < 0.8 * hours:
                continue
            rows.append({"model": model, "lead_day": k, "end": e,
                         "snowfall": float(g["value"].sum()) * hours / len(g),
                         "frac": hours / 24})
    if not rows:
        return None
    pr = predict(pd.DataFrame(rows), coef)
    bl = pr[pr["model"] == BLEND_ID].set_index("end")["pred"]
    pts_t, pts_v, pts_lo, pts_hi, members = [t0], [d0], [d0], [d0], []
    depth = d0
    for k, e in enumerate(ends, start=1):
        if e not in bl.index or k not in band:
            break
        depth = max(0.0, depth + float(bl[e]))
        lo, hi = band[k]
        pts_t.append(e)
        pts_v.append(depth)
        pts_lo.append(max(0.0, depth + lo))
        pts_hi.append(max(0.0, depth + hi))
        members.append(sorted(pr[(pr["end"] == e) & (pr["model"] != BLEND_ID)]["model"]))
    if len(pts_t) < 2:
        return None
    idx = pd.DatetimeIndex(pts_t)
    grid = pd.date_range(t0.ceil("h"), idx[-1], freq="h")
    def interp(v):
        ser = pd.Series(v, index=idx)
        return ser.reindex(idx.union(grid)).interpolate("time").reindex(grid)
    vals, lo, hi = interp(pts_v), interp(pts_lo), interp(pts_hi)
    r = lambda x: [round(float(v) / 100, 3) for v in x]  # noqa: E731 - cm -> m
    return {
        "anchor": {"time": t0.strftime("%Y-%m-%dT%H:%MZ"), "depth_cm": round(d0, 1)},
        "daily": [{"end": t.strftime("%Y-%m-%dT%H:%MZ"), "depth_cm": round(v, 1),
                   "lower_cm": round(a, 1), "upper_cm": round(b, 1), "models": m}
                  for t, v, a, b, m in zip(pts_t[1:], pts_v[1:], pts_lo[1:], pts_hi[1:], members)],
        "times": [t.strftime("%Y-%m-%dT%H:%MZ") for t in grid],
        "values": r(vals), "lower": r(lo), "upper": r(hi),
    }
