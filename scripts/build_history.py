#!/usr/bin/env python
"""Build viewer/data/history.json: "Season in context" for both stations.

For temperature (winter and summer) and snow depth it gives, per station:
  - this season's line, past seasons, and a 10-90 % / 25-75 % band of what is
    normal for each date;
  - a summary (anomaly so far, its percentile, the station's own rank);
  - how unusual the last 7 days and the next 7 forecast days are (percentiles);
  - snow milestones at the bottom station (first 10 cm, peak, melt-out).

Sources:
  - stations: the observation log (#58: PCDS history + Avalanche Canada; top:
    powOmeter v1), and powOmeter's best_hourly.csv for the top's snow depth,
    current site only (depths aren't comparable across the 2026-09-26 move);
  - normals: ERA5-Land 1991-2020 from data/reanalysis/ (scripts/fetch_reanalysis.py),
    adjusted to each station by its monthly mean difference, because the
    stations' records are too short for a 30-year normal;
  - forecast days: the powWX blend (viewer/data/blend.json, written earlier in
    the same workflow by build_verification.py), skipped if missing.

config/history.yaml holds the reference period and the station #58 kill switch.
Each station is built separately, so one failure leaves the other on the page.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from powwx import _netcompat  # noqa: E402

_netcompat.enable()

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from powwx import climate_sources as cs  # noqa: E402
from powwx import config as cfg  # noqa: E402
from powwx import history as hist  # noqa: E402
from powwx import obs_logger  # noqa: E402
from powwx import observations as obs  # noqa: E402
from powwx import reanalysis as ra  # noqa: E402
from powwx import verification as ver  # noqa: E402

TEMP_KINDS = ("winter", "summer")
PAST_MIN_COVERAGE = 0.3      # a past season is drawn if >= 30 % of its days have data
SUMMARY_MIN_DAYS = 7         # "so far" numbers need a week of this season
AVERAGE_MIN_SEASONS = 10     # station average line needs >= 10 seasons at a date
SNOW_BAND_MIN_SEASONS = 8    # measured snow band needs >= 8 seasons at a date
SNOW_BAND_WINDOW = 3         # ... pooling ±3 days per season
SNOW_PCT_MIN_MEDIAN_CM = 20  # no "% of median" while the median is this shallow
MODEL_SNOW_MIN_MEDIAN_M = 0.2
CHIP_DAYS = 7
TREND_START = 1980           # headline trends: the satellite era, when reanalysis is best constrained
STATION_TREND_START = 2011   # #58's first full year
APR1_WINDOW = ("03-25", "04-07")   # "1 April snowpack" = mean of these days (smooths one-day noise)
DEC1 = 61                    # position of 1 Dec on the snow-season axis (1 Oct = 0)
# Percentile classes for the "how unusual" chips (upper bounds, exclusive).
CLASSES = [(10, "very cold"), (33, "cooler"), (67, "near normal"), (90, "warmer"),
           (101, "very warm")]


def load_settings() -> dict:
    s = cfg.load_yaml("history.yaml")
    st = s["station_58"]
    if st.get("show_station_values") and not st.get("permission"):
        raise ValueError("history.yaml: station_58.show_station_values is true but no "
                         "`permission` is recorded; fill it in or set it false.")
    return s


def _r(x, nd=1):
    """Round for the JSON; NaN/None -> None."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return None
    return round(float(x), nd) if nd else int(round(float(x)))


def _arr(values, nd=1) -> list:
    return [_r(v, nd) for v in values]


def _iso(ts) -> str | None:
    return None if ts is None or pd.isna(ts) else pd.Timestamp(ts).strftime("%Y-%m-%d")


def axis_end(end_year: int, kind: str) -> pd.Timestamp:
    """Last day of a season (so a finished season isn't called "so far")."""
    return hist.season_start(end_year, kind) + pd.Timedelta(days=len(hist.axis_dates(kind)) - 1)


def _hourly_from_log(lid: str, variable: str) -> pd.Series:
    df = ver.observations_frame(
        obs_logger.load_observation_log(cfg.DATA_DIR, location=lid, variable=variable))
    return pd.Series(df["observed"].to_numpy(dtype=float), index=pd.DatetimeIndex(df["time"]))


def top_snow_hourly() -> pd.Series:
    """POW-O-METER snow depth (m) from powOmeter v1, current site only."""
    recs = [r for r in obs.fetch_powometer_v1() if r["variable"] == "snow_depth"]
    if not recs:
        return pd.Series(dtype=float)
    site = max(recs, key=lambda r: r["time"])["site_id"]
    recs = [r for r in recs if r["site_id"] == site]
    return pd.Series([r["value"] for r in recs],
                     index=pd.to_datetime([r["time"] for r in recs], utc=True))


def blend_daily(blend: dict | None, lid: str) -> pd.DataFrame | None:
    """Daily means (BC days with >= 20 forecast hours) of the blend and its band."""
    node = ((blend or {}).get("locations") or {}).get(lid, {}).get("temperature_2m")
    if not node or not node.get("times"):
        return None
    idx = pd.to_datetime(node["times"], utc=True)
    cols = {k: hist.daily_values(pd.Series(node[k], index=idx, dtype=float))
            for k in ("values", "lower", "upper") if node.get(k)}
    df = pd.DataFrame(cols).dropna()
    return df if not df.empty else None


# --------------------------------------------------------------------------- #
# Temperature
# --------------------------------------------------------------------------- #

def temperature_chart(kind: str, *, today, daily, adj, settings, fc_daily, show_values: bool,
                      provisional: bool) -> dict:
    """One season chart: band (ERA5-Land normal), this season, past seasons,
    station average, forecast extension and a summary."""
    years = tuple(settings["reference_years"])
    window, rm_days = settings["pool_window_days"], settings["running_mean_days"]
    axis = hist.axis_dates(kind)
    end_year = hist.latest_season(today, kind)
    out = {"kind": kind, "variable": "temperature_2m", "unit": "°C",
           "statistic": f"{rm_days}-day mean", "axis_start": _iso(axis[0]), "n_days": len(axis),
           "season": hist.season_label(end_year, kind), "season_start": _iso(hist.season_start(end_year, kind)),
           "complete": bool(today > axis_end(end_year, kind)),
           "band": None, "current": None, "forecast": None, "past": [], "average": None,
           "summary": None}

    if adj is not None and not adj.empty:
        clim = hist.doy_climatology(hist.running_mean(adj, rm_days), years, window)
        q = clim.loc[hist.doy_key(axis)]
        out["band"] = {f"p{int(k * 100)}": _arr(q[k]) for k in hist.QUANTILES}
        out["band"].update(n_years=int(clim["n_years"].iloc[0]), source="modelled",
                           provisional=provisional)

    station_rm = hist.season_matrix(hist.running_mean(daily, rm_days), kind)
    if show_values and end_year in station_rm.index:
        out["current"] = {"values": _arr(station_rm.loc[end_year]), "source": "measured"}
    elif not show_values and adj is not None and not adj.empty:
        model_rm = hist.season_matrix(hist.running_mean(adj, rm_days), kind)
        if end_year in model_rm.index:
            out["current"] = {"values": _arr(model_rm.loc[end_year]), "source": "modelled"}

    if show_values and fc_daily is not None and not daily.empty:
        out["forecast"] = _forecast_extension(kind, end_year, daily, fc_daily, rm_days)

    if show_values:
        past = station_rm.drop(index=end_year, errors="ignore")
        for y, row in past.iterrows():
            if row.notna().mean() >= PAST_MIN_COVERAGE:
                out["past"].append({"year": int(y), "label": hist.season_label(int(y), kind),
                                    "values": _arr(row)})
        counts = past.notna().sum(axis=0)
        if len(past) >= AVERAGE_MIN_SEASONS:
            med = past.median(axis=0).where(counts >= AVERAGE_MIN_SEASONS)
            out["average"] = {"values": _arr(med), "n_seasons": int(counts.max())}

    out["summary"] = _temperature_summary(kind, end_year, daily, adj, years)
    return out


def _forecast_extension(kind, end_year, daily, fc_daily, rm_days) -> dict | None:
    """Continue this season's running mean into the forecast days (blend daily
    means), with the blend's band carried through the same running mean."""
    last_obs = daily.index.max()
    fc = fc_daily[fc_daily.index > last_obs].head(CHIP_DAYS)
    if fc.empty:
        return None
    lines = {}
    for col in ("values", "lower", "upper"):
        if col not in fc:
            continue
        ext = hist.running_mean(pd.concat([daily, fc[col]]), rm_days)
        ext = ext[(ext.index >= last_obs)]
        ext = ext[hist.in_season(ext.index, kind)]
        ext = ext[hist.season_end_year(ext.index, kind) == end_year]
        lines[col] = ext
    if "values" not in lines or lines["values"].empty:
        return None
    pos = hist.season_position(lines["values"].index, kind)
    return {"positions": [int(p) for p in pos],
            **{k: _arr(v.to_numpy()) for k, v in lines.items()}}


def _temperature_summary(kind, end_year, daily, adj, years) -> dict | None:
    """Mean of this season's days so far against the same days in each normal
    season (ERA5-Land, adjusted) and in each of the station's past seasons."""
    m = hist.season_matrix(daily, kind)
    if end_year not in m.index:
        return None
    cur = m.loc[end_year].dropna()
    if len(cur) < SUMMARY_MIN_DAYS:
        return None
    positions = list(cur.index)
    cur_mean = float(cur.mean())
    out = {"n_days": len(cur), "mean": _r(cur_mean)}
    if adj is not None and not adj.empty:
        ref = hist.season_matrix(adj, kind)
        ref = ref[(ref.index >= years[0] + (hist._months(kind)[0] > hist._months(kind)[1]))
                  & (ref.index <= years[1])]
        ref_means = hist.same_days_means(ref, positions)
        if len(ref_means):
            out.update(anomaly=_r(cur_mean - float(ref_means.median())),
                       percentile=_r(hist.percentile_of(cur_mean, ref_means), 0),
                       n_ref=len(ref_means))
    others = hist.same_days_means(m.drop(index=end_year), positions)
    if len(others) >= 4:
        rank = hist.rank_of(cur_mean, others)
        out.update(rank=rank[0], n_seasons=rank[1],
                   station_since=hist.season_label(int(others.index.min()), kind))
    return out


def unusual_days(*, today, daily, adj, fc_daily, settings, show_values: bool) -> dict | None:
    """Percentile of each of the last 7 days (observed daily mean) and the next 7
    (blend daily mean, with its band as a percentile range) among the same dates
    +/- the pool window in the normal period."""
    if adj is None or adj.empty:
        return None
    years, window = tuple(settings["reference_years"]), settings["pool_window_days"]
    pools = hist.doy_pools(adj, years, window)

    def pct(day, v):
        return _r(hist.percentile_of(v, pools[int(hist.doy_key(pd.DatetimeIndex([day]))[0])]), 0)

    days = []
    recent = daily[(daily.index < today) & (daily.index >= today - pd.Timedelta(days=CHIP_DAYS))]
    for d, v in recent.items():
        chip = {"date": _iso(d), "kind": "observed", "pct": pct(d, v)}
        if show_values:
            chip["value"] = _r(v)
        days.append(chip)
    if fc_daily is not None:
        fc = fc_daily[fc_daily.index >= today].head(CHIP_DAYS)
        for d, row in fc.iterrows():
            chip = {"date": _iso(d), "kind": "forecast", "value": _r(row["values"]),
                    "pct": pct(d, row["values"])}
            if "lower" in row and "upper" in row:
                chip.update(pct_lo=pct(d, row["lower"]), pct_hi=pct(d, row["upper"]))
            days.append(chip)
    if not days:
        return None
    return {"days": days, "classes": [{"below": b, "label": lab} for b, lab in CLASSES]}


# --------------------------------------------------------------------------- #
# Snow depth
# --------------------------------------------------------------------------- #

def snow_chart(*, today, daily_cm, show_values: bool, history_note: str | None) -> dict:
    """Measured snow depth on the 1 Oct-30 Jun axis: this season, past seasons
    and a band from them (only where enough seasons have data)."""
    kind = "snow"
    axis = hist.axis_dates(kind)
    end_year = hist.latest_season(today, kind)
    m = hist.season_matrix(daily_cm, kind)
    out = {"kind": kind, "variable": "snow_depth", "unit": "cm", "statistic": "daily median",
           "axis_start": _iso(axis[0]), "n_days": len(axis),
           "season": hist.season_label(end_year, kind), "season_start": _iso(hist.season_start(end_year, kind)),
           "complete": bool(today > axis_end(end_year, kind)),
           "band": None, "current": None, "past": [], "summary": None, "note": history_note}
    past = m.drop(index=end_year, errors="ignore")
    past = past[past.notna().mean(axis=1) >= PAST_MIN_COVERAGE]
    cur = m.loc[end_year] if end_year in m.index else None

    band_q, n_seasons = _snow_band(past)
    if band_q is not None:
        out["band"] = {**{f"p{int(k * 100)}": _arr(v, 0) for k, v in band_q.items()},
                       "n_seasons": n_seasons, "source": "measured",
                       "since": hist.season_label(int(past.index.min()), kind)}
    if show_values:
        if cur is not None:
            out["current"] = {"values": _arr(cur, 0), "source": "measured"}
        out["past"] = [{"year": int(y), "label": hist.season_label(int(y), kind),
                        "values": _arr(row, 0)} for y, row in past.iterrows()]

    if cur is not None and cur.notna().any():
        p = int(cur.last_valid_index())
        depth = float(cur[p])
        s = {"position": p, "depth_cm": _r(depth, 0) if show_values else None}
        at_p = past.loc[:, max(0, p - 1):p + 1].mean(axis=1).dropna()
        if len(at_p) >= 4:
            s["rank"], s["n_seasons"] = hist.rank_of(depth, at_p)
            med = float(at_p.median())
            if p >= DEC1 and med >= SNOW_PCT_MIN_MEDIAN_CM:
                s["pct_of_median"] = _r(100 * depth / med, 0)
        out["summary"] = s
    return out


def _snow_band(past: pd.DataFrame):
    if len(past) < SNOW_BAND_MIN_SEASONS:
        return None, None
    n = past.shape[1]
    qs = {k: np.full(n, np.nan) for k in hist.QUANTILES}
    n_seasons = []
    vals = past.to_numpy()
    for p in range(n):
        lo, hi = max(0, p - SNOW_BAND_WINDOW), min(n, p + SNOW_BAND_WINDOW + 1)
        block = vals[:, lo:hi]
        have = np.isfinite(block).any(axis=1).sum()
        n_seasons.append(int(have))
        if have >= SNOW_BAND_MIN_SEASONS:
            pool = block[np.isfinite(block)]
            for k in hist.QUANTILES:
                qs[k][p] = np.quantile(pool, k)
    return qs, n_seasons


def milestones(daily_cm: pd.Series, *, today, show_values: bool) -> dict | None:
    """Snow milestones per season (first 10 cm, peak, melt-out) as season
    positions, with the median and range over past seasons."""
    end_year = hist.latest_season(today, "snow")
    if daily_cm.empty:
        return None
    sel = daily_cm[hist.in_season(daily_cm.index, "snow")]
    years = hist.season_end_year(sel.index, "snow")
    seasons = []
    for y in sorted(set(years)):
        s = sel[years == y]
        m = hist.snow_milestones(s)
        if m is None:
            continue
        pos = {k: (int(hist.season_position(pd.DatetimeIndex([m[k]]), "snow")[0])
                   if m[k] is not None else None) for k in ("onset", "peak_date", "meltout")}
        seasons.append({"year": int(y), "label": hist.season_label(int(y), "snow"),
                        "onset": pos["onset"], "peak_cm": _r(m["peak_cm"], 0),
                        "peak": pos["peak_date"], "meltout": pos["meltout"]})
    past = [s for s in seasons if s["year"] != end_year]
    stats = {}
    for key in ("onset", "peak_cm", "peak", "meltout"):
        v = [s[key] for s in past if s[key] is not None]
        if len(v) >= 4:
            stats[key] = {"median": _r(float(np.median(v)), 0), "min": min(v), "max": max(v), "n": len(v)}
    current = next((s for s in seasons if s["year"] == end_year), None)
    out = {"axis_start": _iso(hist.axis_dates("snow")[0]), "threshold_cm": 10, "stats": stats,
           "current": current and {"onset": current["onset"]}}
    if show_values:
        out["seasons"] = past
    return out


def modelled_snowpack(snow_m: pd.Series, *, today, settings) -> dict | None:
    """ERA5-Land snow depth as % of its own 1991-2020 median for the date. Never
    in metres: ERA5-Land runs ~2.4x deeper than #58 but ranks seasons well."""
    if snow_m is None or snow_m.dropna().empty:
        return None
    years, window = tuple(settings["reference_years"]), settings["pool_window_days"]
    snow_m = snow_m.dropna()
    clim = hist.doy_climatology(snow_m, years, window, quantiles=(0.5,))
    med = pd.Series(clim[0.5].reindex(hist.doy_key(snow_m.index)).to_numpy(), index=snow_m.index)
    # Only 1 Dec-30 Jun: earlier, % of a small median blows up; later, ERA5-Land
    # keeps unrealistic summer snow in this cell.
    in_window = hist.in_season(snow_m.index, "snow")
    in_window[in_window] = hist.season_position(snow_m.index[in_window], "snow") >= DEC1
    ratio = (100 * snow_m / med).where((med >= MODEL_SNOW_MIN_MEDIAN_M) & in_window)
    rclim = hist.doy_climatology(ratio.dropna(), years, window)
    axis = hist.axis_dates("snow")
    q = rclim.reindex(hist.doy_key(axis))
    early = np.arange(len(axis)) < DEC1
    band = {f"p{int(k * 100)}": _arr(np.where(early, np.nan, q[k].to_numpy()), 0)
            for k in hist.QUANTILES}
    m = hist.season_matrix(ratio, "snow")
    m.loc[:, m.columns < DEC1] = np.nan
    end_year = hist.latest_season(today, "snow")
    lines = {}
    for y in (end_year, end_year - 1):
        if y in m.index and m.loc[y].notna().any():
            lines[str(y)] = {"label": hist.season_label(y, "snow"), "values": _arr(m.loc[y], 0)}
    last = ratio.dropna()
    last = last[last.index >= hist.season_start(end_year, "snow")]   # this season only
    summary = None
    if len(last):
        d, v = last.index[-1], float(last.iloc[-1])
        pool = hist.doy_pools(ratio.dropna(), years, window)[int(hist.doy_key(pd.DatetimeIndex([d]))[0])]
        summary = {"date": _iso(d), "pct_of_median": _r(v, 0),
                   "percentile": _r(hist.percentile_of(v, pool), 0)}
    return {"axis_start": _iso(axis[0]), "n_days": len(axis), "unit": "% of median",
            "season": hist.season_label(end_year, "snow"), "band": band,
            "lines": lines, "summary": summary, "n_years": int(rclim["n_years"].iloc[0]),
            "data_to": _iso(snow_m.index.max())}


# --------------------------------------------------------------------------- #
# Long view: trends and El Nino
# --------------------------------------------------------------------------- #

LONG_SOURCES = {
    "era5_land": "ERA5-Land (modelled, ~10 km, at 740 m)",
    "era5": "ERA5 (modelled, ~25 km, at 740 m)",
    "ahccd": "Terrace, homogenized (measured, 217 m, ECCC AHCCD)",
}


def _guarded(name, fn, *args, **kwargs):
    """Optional blocks: a failure is reported and leaves the block out."""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the rest of the section still builds
        print(f"WARNING: {name} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def long_view(daily_58: pd.Series | None, settings) -> dict | None:
    """Winter, summer and annual mean temperature over the decades from three
    sources, each as an anomaly from its own 1991-2020 mean, with Sen's-slope
    trends since 1980 and the page's rule for calling it warming."""
    e5l = ra.load_reanalysis(cfg.DATA_DIR, location="station_58", model="era5_land")
    e5 = ra.load_reanalysis(cfg.DATA_DIR, location="station_58", model="era5")
    ah = cs.load_latest(cfg.DATA_DIR, name="ahccd", prefix=cs.AHCCD_TERRACE)
    daily = {k: df["temperature_2m_mean"] for k, df in (("era5_land", e5l), ("era5", e5))
             if not df.empty and "temperature_2m_mean" in df}
    monthly = (pd.Series(ah["temp_mean"].to_numpy(float), index=pd.to_datetime(ah["date"] + "-01"))
               if ah is not None and len(ah) else None)
    if not daily and monthly is None:
        return None
    y0, y1 = settings["reference_years"]
    kinds = {}
    for kind in ("winter", "summer", "year"):
        series, recent, full = {}, [], []
        for key in LONG_SOURCES:
            if key == "ahccd":
                s = hist.monthly_season_means(monthly, kind) if monthly is not None else pd.Series(dtype=float)
            else:
                s = hist.season_means(daily[key], kind) if key in daily else pd.Series(dtype=float)
            if s.empty:
                continue
            ref = s[(s.index >= y0) & (s.index <= y1)]
            anom = s - ref.mean()
            series[key] = {"years": [int(y) for y in anom.index], "anomaly": _arr(anom, 2),
                           "normal_mean": _r(ref.mean(), 2), "n_normal": len(ref)}
            r = hist.sen_slope_ci(s.index[s.index >= TREND_START], s[s.index >= TREND_START])
            f = hist.sen_slope_ci(s.index, s)
            if r:
                recent.append({"source": key, **r})
            if f:
                full.append({"source": key, **f})
        station = None
        if daily_58 is not None and not daily_58.empty:
            st = hist.season_means(daily_58, kind)
            st = st[st.index >= STATION_TREND_START]
            station = {"n_seasons": len(st), "trend": hist.sen_slope_ci(st.index, st)}
        # Two verdicts: over each record's full length, and since 1980 (where the
        # measured Terrace record, ending 2020, may disagree with the reanalyses).
        kinds[kind] = {"series": series, "trends_since": recent, "trends_full": full,
                       "statement": hist.trend_statement(recent),
                       "statement_full": hist.trend_statement(full), "station": station}
    return {"trend_start": TREND_START, "normal_years": [y0, y1], "sources": LONG_SOURCES,
            "kinds": kinds}


def _apr1(daily: pd.Series) -> pd.Series:
    """Mean over the 1 April window each year, keyed by year."""
    md = daily.index.strftime("%m-%d")
    sel = daily[(md >= APR1_WINDOW[0]) & (md <= APR1_WINDOW[1])].dropna()
    return sel.groupby(sel.index.year).mean()


def enso_card(snow_model: pd.Series | None, snow_cm_58: pd.Series | None, *, today,
              show_values: bool) -> dict | None:
    """Past winters by El Nino / La Nina phase (RONI, Nov-Jan): ERA5-Land winter
    temperature with the warming trend removed, and whether the modelled 1 April
    snowpack beat its long-term median; #58's 1 April rank where it has one."""
    roni = cs.load_latest(cfg.DATA_DIR, name="roni", prefix="roni")
    e5l = ra.load_reanalysis(cfg.DATA_DIR, location="station_58", model="era5_land")
    if roni is None or e5l.empty:
        return None
    wi = hist.winter_index(roni)
    t_anom = hist.detrend(hist.season_means(e5l["temperature_2m_mean"], "winter"))
    snow = _apr1(snow_model.dropna()) if snow_model is not None else pd.Series(dtype=float)
    snow_pct = 100 * snow / snow.median() if len(snow) else snow
    st = _apr1(snow_cm_58) if snow_cm_58 is not None and len(snow_cm_58) else pd.Series(dtype=float)

    rows = []
    for y in sorted(set(wi.index) & (set(t_anom.index) | set(snow_pct.index))):
        row = {"year": int(y), "label": hist.season_label(int(y), "winter"),
               "index": _r(wi[y], 2), "phase": hist.enso_class(wi[y]),
               "t_anom": _r(t_anom.get(y), 2), "snow_pct": _r(snow_pct.get(y), 0)}
        if show_values and y in st.index and len(st) >= 5:
            row["station_rank"], row["station_n"] = hist.rank_of(st[y], st.drop(y))
        rows.append(row)
    df = pd.DataFrame(rows)
    classes = []
    for phase in hist.ENSO_CLASSES:
        sub = df[df["phase"] == phase]
        snow_ok = sub["snow_pct"].dropna()
        classes.append({"phase": phase, "n": len(sub),
                        "t_anom": _r(sub["t_anom"].mean(), 2) if len(sub) else None,
                        "snow_above": int((snow_ok > 100).sum()), "snow_n": len(snow_ok)})
    strong = df["phase"] == hist.ENSO_CLASSES[-1]
    above = (df["snow_pct"] > 100).astype(float).where(df["snow_pct"].notna())
    last = roni.iloc[-1]
    upcoming = today.year + 1 if today.month >= 5 else today.year
    return {
        "index_name": "RONI",
        "current": {"season": str(last["season"]), "year": int(last["year"]),
                    "value": _r(float(last["anom"]), 2), "phase": hist.enso_class(float(last["anom"]))},
        "winter": hist.season_label(upcoming, "winter"),
        "classes": classes, "years": rows,
        # Is the moderate/strong El Nino group different from all other winters
        # by more than chance? (permutation test, two-sided)
        "p_temp": _r(hist.permutation_pvalue(df.loc[strong, "t_anom"], df.loc[~strong, "t_anom"]), 3),
        "p_snow": _r(hist.permutation_pvalue(above[strong], above[~strong]), 3),
        "snow_window": list(APR1_WINDOW),
    }


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #

def build_location(loc: dict, *, today, settings, blend) -> tuple[dict, dict]:
    lid = loc["id"]
    is_58 = lid == "station_58"
    show = bool(settings["station_58"]["show_station_values"]) if is_58 else True

    temp = hist.qc_hourly(_hourly_from_log(lid, "temperature_2m"), "temperature_2m")
    daily = hist.daily_values(temp, "mean", 20)
    snow_hourly = top_snow_hourly() if lid == "pow_o_meter" else _hourly_from_log(lid, "snow_depth")
    snow_cm = hist.daily_values(hist.qc_hourly(snow_hourly, "snow_depth"), "median", 6) * 100

    model = ra.load_reanalysis(cfg.DATA_DIR, location=lid, model=settings["reference_model"])
    tmean = model.get("temperature_2m_mean") if not model.empty else None
    adj, adjustment = None, None
    if tmean is not None and not daily.empty:
        since = (settings.get("adjust_since") or {}).get(lid)
        fit = daily[daily.index >= pd.Timestamp(since)] if since else daily
        offsets = hist.monthly_offsets(tmean, fit)
        adj = hist.apply_offsets(tmean, offsets)
        n_overlap = len(tmean.index.intersection(fit.index))
        adjustment = {
            "offsets": {int(mo): _r(r["offset"], 2) for mo, r in offsets.iterrows()},
            "since": since,
            "n_overlap_days": n_overlap,
            "coverage_daily": hist.band_coverage(tmean, fit),
            "coverage_7day": hist.band_coverage(tmean, fit, smooth=settings["running_mean_days"]),
            "step": hist.step_change(daily, tmean, since) if since else None,
            "provisional": n_overlap < 3 * 365,
        }
    provisional = bool(adjustment and adjustment["provisional"])
    fc_daily = blend_daily(blend, lid)

    charts = {}
    for kind in TEMP_KINDS:
        charts[f"temperature_{kind}"] = temperature_chart(
            kind, today=today, daily=daily, adj=adj, settings=settings, fc_daily=fc_daily,
            show_values=show, provisional=provisional)
    note = ("Snow-depth history starts this season: the station moved about 70 m on "
            "2026-09-26 and depths aren't comparable across the move."
            if lid == "pow_o_meter" else None)
    charts["snow_depth"] = snow_chart(today=today, daily_cm=snow_cm, show_values=show,
                                      history_note=note)

    out = {
        "label": loc["name"], "elevation_m": loc["elevation_m"], "role": loc["role"],
        "station_values": show,
        "record": {"temperature_from": _iso(daily.index.min()) if len(daily) else None,
                   "temperature_days": int(len(daily))},
        "adjustment": adjustment,
        "charts": charts,
        "unusual": unusual_days(today=today, daily=daily, adj=adj, fc_daily=fc_daily,
                                settings=settings, show_values=show),
        "milestones": (milestones(snow_cm, today=today, show_values=show) if is_58 else None),
    }
    extras = {"snow_model": model.get("snow_depth_mean") if not model.empty else None,
              "daily": daily, "snow_cm": snow_cm}
    return out, extras


def build(today: pd.Timestamp | None = None, blend: dict | None = None) -> dict:
    settings = load_settings()
    if today is None:
        today = pd.Timestamp.now(tz=hist.LOCAL_TZ).normalize().tz_localize(None)
    years = settings["reference_years"]
    locations, extras = {}, {}
    for loc in cfg.load_locations():
        try:
            locations[loc["id"]], extras[loc["id"]] = build_location(
                loc, today=today, settings=settings, blend=blend)
        except Exception as exc:  # noqa: BLE001 - one station shouldn't sink the build
            print(f"WARNING: history failed for {loc['id']}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
    b58 = extras.get("station_58", {})
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
        "today": _iso(today),
        "reference": {"years": years, "model": settings["reference_model"],
                      "window_days": settings["pool_window_days"]},
        "seasons": {k: {"label": v["label"], "months": list(v["months"])}
                    for k, v in hist.SEASONS.items()},
        "locations": locations,
        # One ERA5-Land cell covers both stations; the long view uses #58's point.
        "modelled_snowpack": modelled_snowpack(b58.get("snow_model"), today=today, settings=settings),
        "long_view": _guarded("long view", long_view, b58.get("daily"), settings),
        "enso": _guarded("El Niño card", enso_card, b58.get("snow_model"), b58.get("snow_cm"),
                         today=today,
                         show_values=locations.get("station_58", {}).get("station_values", True)),
        "notes": (
            f"Normal = {years[0]}–{years[1]} (the standard 30-year period), from ERA5-Land "
            "reanalysis: modelled weather, reconstructed from observations, adjusted to "
            "each station by its average monthly difference over the years both have. "
            "Bands show where 80 % (light) and 50 % (dark) of those years fell on each "
            "date, pooling the dates within ±"
            f"{settings['pool_window_days']} days. Recent years run warmer than that "
            "normal, so 'warmer than usual' comes up more often than 1 day in 3. "
            "Station lines are measured and provisional (raw sensor data, cleaned of "
            "obvious errors). Ranks compare with the station's own seasons, a short record."
        ),
        "credits": [
            {"text": "Climate normals: ERA5-Land reanalysis (Copernicus Climate Change Service; "
                     "contains modified Copernicus information; neither the European Commission "
                     "nor ECMWF is responsible for any use) via Open-Meteo.com (CC BY 4.0)",
             "url": "https://open-meteo.com/en/docs/historical-weather-api"},
            {"text": "Station #58: BC Ministry of Transportation and Infrastructure, used with "
                     "permission; MoTI is not liable for the accuracy of its data",
             "url": "https://avalanche.ca/"},
            {"text": "POW-O-METER data, Mount Remo Backcountry Society, CC BY 4.0",
             "url": "https://github.com/mount-remo-backcountry-society/powOmeter"},
            {"text": "Long view: ERA5 reanalysis via Open-Meteo.com (CC BY 4.0); Terrace homogenized "
                     "temperature, ECCC AHCCD (Open Government Licence – Canada)",
             "url": "https://www.canada.ca/en/environment-climate-change/services/climate-change/"
                    "science-research-data/climate-trends-variability/adjusted-homogenized-canadian-data.html"},
            {"text": "El Niño index (RONI): NOAA Climate Prediction Center",
             "url": "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/enso/roni/"},
        ],
    }


def main(out_dir: Path | None = None) -> int:
    out_dir = out_dir or cfg.REPO_ROOT / "viewer" / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    blend_path = out_dir / "blend.json"
    blend = json.loads(blend_path.read_text(encoding="utf-8")) if blend_path.is_file() else None
    payload = build(blend=blend)
    out = out_dir / "history.json"
    out.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    summary = {lid: {"adjustment": {k: v for k, v in (loc["adjustment"] or {}).items()
                                    if k != "offsets"},
                     **{k: c.get("summary") for k, c in loc["charts"].items()}}
               for lid, loc in payload["locations"].items()}
    print(json.dumps({"out": str(out), "kb": round(out.stat().st_size / 1024, 1),
                      "summary": summary}, indent=2, default=str))
    return 0 if payload["locations"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
