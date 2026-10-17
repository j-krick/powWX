"""Historical context for station data: QC, seasons, climatology, percentiles.

Pure functions on pandas Series/DataFrames, with no powWX config or I/O, so the
module can be copied as-is into powOmeter (which must never import powWX).

Conventions:
  - Hourly inputs are Series indexed by tz-aware UTC timestamps.
  - Daily series are indexed by naive Timestamps = BC local calendar dates.
  - Seasons (owner's choice, 2026-10): Winter 1 Nov-30 Apr, Summer 1 May-31 Oct,
    and a snow-season axis 1 Oct-30 Jun so snowpack curves aren't cut off. A
    season is named by the year it ends in ("winter 2027" = Nov 2026-Apr 2027).
  - Within a season, a date's *position* is its day index on a fixed non-leap
    calendar (Feb 29 shares Feb 28's slot), so every season has the same axis.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

LOCAL_TZ = "America/Vancouver"
SEASONS = {
    "winter": {"label": "Winter", "months": (11, 4)},
    "summer": {"label": "Summer", "months": (5, 10)},
    "snow": {"label": "Snow season", "months": (10, 6)},
}
REF_YEAR = 2001          # non-leap reference calendar for season positions
# Hard limits per variable (sentinels like -6999 and -79.99 fall outside), and
# a spike filter: (centred window in hours, largest allowed jump from its median).
QC_RANGE = {"temperature_2m": (-45.0, 40.0), "snow_depth": (-0.3, 6.0)}
QC_SPIKE = {"temperature_2m": (5, 10.0), "snow_depth": (13, 0.5)}
# Snow depths below this are sensor noise on bare ground (sonic sensors read a
# few cm either side of zero), so they count as no snow.
SNOW_NOISE_FLOOR_M = 0.05
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)
_MID_MONTH_DOY = np.array([15, 46, 74, 105, 135, 166, 196, 227, 258, 288, 319, 349])


# --------------------------------------------------------------------------- #
# Quality control and daily values
# --------------------------------------------------------------------------- #

def qc_hourly(s: pd.Series, variable: str) -> pd.Series:
    """Drop out-of-range values (sentinels) and isolated spikes; snow depths
    below the noise floor count as 0. Duplicate times keep the last."""
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated(keep="last")]
    lo, hi = QC_RANGE[variable]
    s = s[(s >= lo) & (s <= hi)]
    if s.empty:
        return s
    window, max_jump = QC_SPIKE[variable]
    med = s.rolling(f"{window}h", center=True, min_periods=3).median()
    s = s[~((s - med).abs() > max_jump)]
    if variable == "snow_depth":
        s = s.where(s >= SNOW_NOISE_FLOOR_M, 0.0)
    return s


def daily_values(s: pd.Series, how: str = "mean", min_count: int = 20,
                 tz: str = LOCAL_TZ) -> pd.Series:
    """Aggregate an hourly UTC series to BC local days. Days with fewer than
    ``min_count`` hourly values are left out (a part day isn't a daily mean)."""
    if s.empty:
        return pd.Series(dtype=float)
    idx = s.index if s.index.tz is not None else s.index.tz_localize("UTC")
    key = idx.tz_convert(tz).tz_localize(None).normalize()
    g = pd.Series(s.values, index=key).groupby(level=0)
    out = g.agg(how)
    out = out[g.size() >= min_count]
    out.index.name = None
    return out.astype(float)


def running_mean(daily: pd.Series, window: int = 7, min_valid: int = 5) -> pd.Series:
    """Trailing ``window``-day mean (the value on a day averages it and the
    days before it), needing ``min_valid`` days present."""
    if daily.empty:
        return daily
    full = daily.reindex(pd.date_range(daily.index.min(), daily.index.max(), freq="D"))
    return full.rolling(window, min_periods=min_valid).mean().dropna()


# --------------------------------------------------------------------------- #
# Seasons and positions
# --------------------------------------------------------------------------- #

def _months(kind: str) -> tuple[int, int]:
    return SEASONS[kind]["months"]


def in_season(idx: pd.DatetimeIndex, kind: str) -> np.ndarray:
    first, last = _months(kind)
    m = idx.month
    return np.asarray((m >= first) | (m <= last) if first > last else (m >= first) & (m <= last))


def season_end_year(idx: pd.DatetimeIndex, kind: str) -> np.ndarray:
    """Year each (in-season) date's season ends in."""
    first, last = _months(kind)
    return np.asarray(idx.year + ((idx.month >= first) & (first > last)).astype(int))


def season_start(end_year: int, kind: str) -> pd.Timestamp:
    first, last = _months(kind)
    return pd.Timestamp(end_year - 1 if first > last else end_year, first, 1)


def season_label(end_year: int, kind: str) -> str:
    """'2026–27' for seasons spanning New Year, '2027' otherwise."""
    first, last = _months(kind)
    return f"{end_year - 1}–{str(end_year)[2:]}" if first > last else str(end_year)


def axis_dates(kind: str) -> pd.DatetimeIndex:
    """The season's days on the non-leap reference calendar (one per position)."""
    first, last = _months(kind)
    start = pd.Timestamp(REF_YEAR, first, 1)
    end = pd.Timestamp(REF_YEAR + (first > last), last, 1) + pd.offsets.MonthEnd(0)
    return pd.date_range(start, end, freq="D")


def season_position(idx: pd.DatetimeIndex, kind: str) -> np.ndarray:
    """Day index of each (in-season) date within its season; Feb 29 -> Feb 28."""
    first, last = _months(kind)
    m = np.asarray(idx.month)
    d = np.where((m == 2) & (np.asarray(idx.day) == 29), 28, np.asarray(idx.day))
    year = REF_YEAR + ((m < first) & (first > last)).astype(int)
    ref = pd.to_datetime(pd.DataFrame({"year": year, "month": m, "day": d}))
    return np.asarray((ref - pd.Timestamp(REF_YEAR, first, 1)).dt.days)


def latest_season(today: pd.Timestamp, kind: str) -> int:
    """End year of the most recent season of ``kind`` that has begun by ``today``
    (in a gap between seasons, the one that ended last)."""
    first, last = _months(kind)
    if in_season(pd.DatetimeIndex([today]), kind)[0]:
        return int(season_end_year(pd.DatetimeIndex([today]), kind)[0])
    return today.year if today.month > last else today.year - 1


def season_matrix(daily: pd.Series, kind: str) -> pd.DataFrame:
    """Daily values arranged as rows = season end year, columns = position."""
    n = len(axis_dates(kind))
    d = daily[in_season(daily.index, kind)].dropna()
    if d.empty:
        return pd.DataFrame(columns=range(n), dtype=float)
    df = pd.DataFrame({"y": season_end_year(d.index, kind),
                       "p": season_position(d.index, kind), "v": d.values})
    m = df.pivot_table(index="y", columns="p", values="v", aggfunc="first")
    return m.reindex(columns=range(n)).astype(float)


# --------------------------------------------------------------------------- #
# Climatology, percentiles and ranks
# --------------------------------------------------------------------------- #

def doy_key(idx: pd.DatetimeIndex) -> np.ndarray:
    """Day of year 1..365 on the non-leap calendar (Feb 29 -> Feb 28)."""
    m = np.asarray(idx.month)
    d = np.where((m == 2) & (np.asarray(idx.day) == 29), 28, np.asarray(idx.day))
    ref = pd.to_datetime(pd.DataFrame({"year": REF_YEAR, "month": m, "day": d}))
    return np.asarray(ref.dt.dayofyear)


def doy_pools(daily: pd.Series, years: tuple[int, int], window: int = 7) -> dict[int, np.ndarray]:
    """For each day of year, all values within ±``window`` days of it (wrapping
    round New Year) in the reference ``years``. Pooling neighbouring days gives
    ~15x more values per date than a single calendar day, so percentiles are
    stable without smoothing away the seasonal cycle."""
    sel = daily[(daily.index.year >= years[0]) & (daily.index.year <= years[1])].dropna()
    keys, vals = doy_key(sel.index), sel.to_numpy()
    pools = {}
    for d in range(1, 366):
        dist = np.abs((keys - d + 182) % 365 - 182)
        pools[d] = vals[dist <= window]
    return pools


def doy_climatology(daily: pd.Series, years: tuple[int, int], window: int = 7,
                    quantiles=QUANTILES) -> pd.DataFrame:
    """Quantiles of the ±window pool for each day of year (index 1..365), with
    ``n`` values and ``n_years`` distinct years behind each row."""
    pools = doy_pools(daily, years, window)
    sel = daily[(daily.index.year >= years[0]) & (daily.index.year <= years[1])].dropna()
    rows = {d: [np.quantile(v, q) if len(v) else np.nan for q in quantiles] + [len(v)]
            for d, v in pools.items()}
    out = pd.DataFrame.from_dict(rows, orient="index", columns=[*quantiles, "n"])
    out["n_years"] = sel.index.year.nunique()
    return out


def percentile_of(value: float, ref) -> float | None:
    """Percentile rank (0-100) of ``value`` among ``ref``: share below, counting
    ties as half. None when there is nothing to compare with."""
    ref = np.asarray(ref, dtype=float)
    ref = ref[np.isfinite(ref)]
    if not len(ref) or value is None or not np.isfinite(value):
        return None
    return float(100.0 * ((ref < value).sum() + 0.5 * (ref == value).sum()) / len(ref))


def rank_of(value: float, others, *, descending: bool = True) -> tuple[int, int] | None:
    """Rank of ``value`` among itself plus ``others`` (1 = highest when
    ``descending``); ties share the better rank. Returns (rank, n)."""
    others = np.asarray(others, dtype=float)
    others = others[np.isfinite(others)]
    if value is None or not np.isfinite(value):
        return None
    beaten = (others > value).sum() if descending else (others < value).sum()
    return int(beaten) + 1, len(others) + 1


def same_days_means(matrix: pd.DataFrame, positions, min_coverage: float = 0.8) -> pd.Series:
    """Each season's mean over ``positions`` (the days observed so far this
    season), for seasons with data on at least ``min_coverage`` of them."""
    positions = list(positions)
    if not positions or matrix.empty:
        return pd.Series(dtype=float)
    sub = matrix.reindex(columns=positions)
    ok = sub.notna().sum(axis=1) >= min_coverage * len(positions)
    return sub[ok].mean(axis=1)


# --------------------------------------------------------------------------- #
# Adjusting reanalysis to a station
# --------------------------------------------------------------------------- #

def monthly_offsets(model: pd.Series, station: pd.Series, min_days: int = 10) -> pd.DataFrame:
    """Mean station-minus-model difference per calendar month over the days both
    have. Months with fewer than ``min_days`` shared days get the overall mean
    difference (and their ``n`` shows why)."""
    j = pd.concat([station.rename("st"), model.rename("mo")], axis=1, join="inner").dropna()
    diff = j["st"] - j["mo"]
    g = diff.groupby(j.index.month)
    out = pd.DataFrame({"offset": g.mean(), "n": g.size()}).reindex(range(1, 13))
    out["n"] = out["n"].fillna(0).astype(int)
    overall = float(diff.mean()) if len(diff) else 0.0
    out.loc[out["n"] < min_days, "offset"] = overall
    return out


def apply_offsets(model: pd.Series, offsets: pd.DataFrame) -> pd.Series:
    """Add the monthly offsets to a daily model series, interpolated smoothly
    between mid-months so the adjusted series has no steps at month ends."""
    doy = doy_key(model.index)
    off = np.interp(doy, _MID_MONTH_DOY, offsets["offset"].to_numpy(), period=365)
    return model + off


def band_coverage(model: pd.Series, station: pd.Series, *, smooth: int = 1, window: int = 7,
                  lo: float = 0.1, hi: float = 0.9, train_frac: float = 0.7) -> dict | None:
    """Out-of-sample check that an adjusted-model band fits the station.

    Offsets are fitted on the first ``train_frac`` of the shared days; the model,
    adjusted with them, gives a ±window day-of-year band over the remaining
    (test) years; the result is the share of the station's test days below the
    ``lo`` and above the ``hi`` quantile. A fair band gives about lo and 1-hi.
    ``smooth`` > 1 checks ``smooth``-day running means instead of daily values.
    """
    if smooth > 1:
        model, station = running_mean(model, smooth), running_mean(station, smooth)
    shared = model.index.intersection(station.dropna().index)
    if len(shared) < 120:
        return None
    cut = shared[int(len(shared) * train_frac)]
    offsets = monthly_offsets(model[model.index < cut], station[station.index < cut])
    adjusted = apply_offsets(model, offsets)
    test = station[(station.index >= cut)].reindex(shared[shared >= cut]).dropna()
    years = (int(test.index.year.min()), int(test.index.year.max()))
    pools = doy_pools(adjusted, years, window)
    keys = doy_key(test.index)
    below = above = 0
    for k, v in zip(keys, test.to_numpy()):
        pool = pools[int(k)]
        below += v < np.quantile(pool, lo)
        above += v > np.quantile(pool, hi)
    n = len(test)
    return {"below": round(below / n, 3), "above": round(above / n, 3), "n_days": n,
            "expected": [lo, round(1 - hi, 3)]}


def step_change(station: pd.Series, model: pd.Series, cut, *, nboot: int = 2000,
                seed: int = 0) -> dict | None:
    """How much the station-minus-model difference shifted at ``cut`` (after
    minus before), a check for an instrument change. The difference's seasonal
    cycle is removed first, so unevenly sampled seasons can't fake a step; the
    90 % range comes from resampling whole calendar months."""
    diff = (station - model).dropna()
    if diff.empty:
        return None
    des = diff - diff.groupby(diff.index.month).transform("mean")
    cut = pd.Timestamp(cut)
    pre, post = des[des.index < cut], des[des.index >= cut]
    if len(pre) < 90 or len(post) < 90:
        return None
    rng = np.random.default_rng(seed)

    def boot(x):
        blocks = [g.to_numpy() for _, g in x.groupby(x.index.to_period("M"))]
        return np.array([np.concatenate([blocks[i] for i in rng.integers(0, len(blocks), len(blocks))]).mean()
                         for _ in range(nboot)])

    b = boot(post) - boot(pre)
    lo, hi = np.percentile(b, [5, 95])
    return {"cut": cut.strftime("%Y-%m-%d"), "step": round(float(post.mean() - pre.mean()), 2),
            "lo": round(float(lo), 2), "hi": round(float(hi), 2),
            "n_before": len(pre), "n_after": len(post)}


# --------------------------------------------------------------------------- #
# Long view: seasonal means, trends, El Nino
# --------------------------------------------------------------------------- #

def season_means(daily: pd.Series, kind: str, min_coverage: float = 0.9) -> pd.Series:
    """Mean of each season (``kind`` from SEASONS, or "year" for calendar
    years), indexed by end year; seasons with under ``min_coverage`` of their
    days present (including one still under way) are left out."""
    daily = daily.dropna()
    if daily.empty:
        return pd.Series(dtype=float)
    if kind == "year":
        g = daily.groupby(daily.index.year)
        n_days = pd.Series({y: 366 if pd.Timestamp(y, 12, 31).dayofyear == 366 else 365 for y in g.groups})
    else:
        sel = daily[in_season(daily.index, kind)]
        g = sel.groupby(season_end_year(sel.index, kind))
        n_days = pd.Series({y: (season_start(y, kind) + pd.DateOffset(months=len(axis_dates(kind).month.unique()))
                                - season_start(y, kind)).days for y in g.groups})
    means, counts = g.mean(), g.size()
    return means[counts >= min_coverage * n_days.reindex(means.index)].astype(float)


def monthly_season_means(monthly: pd.Series, kind: str) -> pd.Series:
    """Season means from monthly means (index = month-start Timestamps), only
    for seasons with every month present; months weighted equally."""
    monthly = monthly.dropna()
    if kind == "year":
        g = monthly.groupby(monthly.index.year)
        need = 12
    else:
        sel = monthly[in_season(monthly.index, kind)]
        g = sel.groupby(season_end_year(sel.index, kind))
        need = len(axis_dates(kind).month.unique())
    means, counts = g.mean(), g.size()
    return means[counts == need].astype(float)


def sen_slope_ci(years, values, *, block: int = 3, nboot: int = 2000, seed: int = 0,
                 level: float = 0.9) -> dict | None:
    """Sen's slope (median of all pairwise slopes; robust to outliers) per
    decade, with a ``level`` range from a moving-block bootstrap of the
    (year, value) pairs, so year-to-year persistence widens it honestly."""
    x, y = np.asarray(years, float), np.asarray(values, float)
    ok = np.isfinite(x) & np.isfinite(y)
    x, y = x[ok], y[ok]
    if len(x) < 8:
        return None

    n, b = len(x), min(block, len(x))
    i, j = np.triu_indices(n, 1)

    def sen(xx, yy):
        dx = xx[j] - xx[i]
        m = dx != 0          # resampled years repeat
        return np.median((yy[j] - yy[i])[m] / dx[m])

    rng = np.random.default_rng(seed)
    starts = np.arange(n - b + 1)
    boots = []
    for _ in range(nboot):
        idx = np.concatenate([np.arange(s, s + b) for s in rng.choice(starts, int(np.ceil(n / b)))])[:n]
        boots.append(sen(x[idx], y[idx]))
    alpha = (1 - level) / 2
    lo, hi = np.quantile(boots, [alpha, 1 - alpha])
    return {"slope": round(float(sen(x, y)) * 10, 2), "lo": round(float(lo) * 10, 2),
            "hi": round(float(hi) * 10, 2), "n": int(n), "start": int(x.min()), "end": int(x.max())}


def trend_statement(trends: list[dict]) -> dict:
    """The page's rule for words: "warming" only when every source's range is
    above zero ("cooling" when all below); otherwise "no clear trend". Quotes
    the spread of the sources' central slopes."""
    trends = [t for t in trends if t]
    if not trends:
        return {"verdict": "unknown"}
    slopes = [t["slope"] for t in trends]
    if all(t["lo"] > 0 for t in trends):
        verdict = "warming"
    elif all(t["hi"] < 0 for t in trends):
        verdict = "cooling"
    else:
        verdict = "no clear trend"
    return {"verdict": verdict, "min_slope": min(slopes), "max_slope": max(slopes), "n_sources": len(trends)}


ENSO_CLASSES = ["moderate/strong La Niña", "weak La Niña", "neutral", "weak El Niño",
                "moderate/strong El Niño"]


def enso_class(value: float) -> str | None:
    """Phase from a Nino index value: El Nino from +0.5 degC, moderate or
    stronger from +1.0 (and the same below zero for La Nina)."""
    if value is None or not np.isfinite(value):
        return None
    if value <= -1.0:
        return ENSO_CLASSES[0]
    if value <= -0.5:
        return ENSO_CLASSES[1]
    if value < 0.5:
        return ENSO_CLASSES[2]
    if value < 1.0:
        return ENSO_CLASSES[3]
    return ENSO_CLASSES[4]


def winter_index(index: pd.DataFrame, season: str = "NDJ") -> pd.Series:
    """The index for each winter, keyed by the winter's end year: NDJ of year Y
    (Nov Y - Jan Y+1, the usual peak) describes the winter ending Y+1."""
    sel = index[index["season"] == season]
    return pd.Series(sel["anom"].to_numpy(dtype=float), index=sel["year"].to_numpy() + 1)


def detrend(series: pd.Series) -> pd.Series:
    """Residuals from a straight-line fit over the index (years), so warming
    doesn't masquerade as an El Nino signal in recent decades."""
    s = series.dropna()
    if len(s) < 3:
        return s
    coef = np.polyfit(s.index.to_numpy(float), s.to_numpy(), 1)
    return s - np.polyval(coef, s.index.to_numpy(float))


def permutation_pvalue(a, b, *, n: int = 5000, seed: int = 0) -> float | None:
    """Two-sided p-value that groups ``a`` and ``b`` differ in mean only by
    chance (labels shuffled ``n`` times)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    a, b = a[np.isfinite(a)], b[np.isfinite(b)]
    if len(a) < 3 or len(b) < 3:
        return None
    pooled, k = np.concatenate([a, b]), len(a)
    obs = abs(a.mean() - b.mean())
    rng = np.random.default_rng(seed)
    hits = 0
    for _ in range(n):
        p = rng.permutation(pooled)
        hits += abs(p[:k].mean() - p[k:].mean()) >= obs
    return (hits + 1) / (n + 1)


# --------------------------------------------------------------------------- #
# Snow-season milestones
# --------------------------------------------------------------------------- #

def snow_milestones(daily_cm: pd.Series, *, threshold: float = 10.0,
                    max_gap_days: int = 14) -> dict | None:
    """First day with snow >= ``threshold`` cm, peak depth and date, and melt-out
    (first day after the peak back below the threshold) for ONE snow season.

    A milestone is None when the record can't pin it down: a gap of more than
    ``max_gap_days`` before it, (for onset) no snow-free day seen first, or (for
    the peak) Dec-Apr less than 80 % covered.
    """
    s = daily_cm.dropna().sort_index()
    if s.empty:
        return None
    gaps = s.index.to_series().diff().dt.days.fillna(0)

    def gap_ok(upto: pd.Timestamp) -> bool:
        return bool((gaps[:upto] <= max_gap_days).all())

    above = s[s >= threshold]
    onset = None
    if len(above):
        first = above.index[0]
        if (s[:first] < threshold).any() and gap_ok(first):
            onset = first
    peak_date = s.idxmax()
    winter = s[(s.index.month >= 12) | (s.index.month <= 4)]
    winter_gaps = winter.index.to_series().diff().dt.days.fillna(0)
    # The peak counts only if Dec-Apr (151 days) is at least 80 % covered.
    peak_ok = len(winter) >= 0.8 * 151 and bool((winter_gaps <= max_gap_days).all())
    meltout = None
    after = s[s.index > peak_date]
    below = after[after < threshold]
    if len(below) and gap_ok(below.index[0]):
        meltout = below.index[0]
    return {
        "onset": onset,
        "peak_cm": float(s.max()) if peak_ok else None,
        "peak_date": peak_date if peak_ok else None,
        "meltout": meltout,
    }
