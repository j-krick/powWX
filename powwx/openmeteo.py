"""Open-Meteo client + parser.

One request per location pulls every model at once via the comma-separated
``models=`` parameter. The response names each series ``<variable>_<model_id>``
(e.g. ``temperature_2m_gem_global``); :func:`parse_long` unpacks that wide shape
into the append-only long schema keyed on
``(location, model, issued_at, valid_time, variable, value)``.

NOTE on ``issued_at``: the Open-Meteo *forecast* endpoint does not expose each
model's run/initialisation time, so the live logger records ``fetched_at`` (the
moment of the request, UTC) as ``issued_at``. That is the honest best-available
reference; lead time is then ``valid_time - issued_at``. The Previous Runs API
(see :mod:`powwx.previous_runs`) gives exact integer-day lead offsets instead and
is the basis for backfilled verification.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import requests

# Previous Runs queries (many offsets over a month) can be slow, so the timeout
# is generous and transient failures are retried with backoff.
REQUEST_TIMEOUT = 180
MAX_RETRIES = 4
BACKOFF_BASE = 3  # seconds: 3, 6, 12, 24
RETRY_STATUS = {429, 500, 502, 503, 504}


def fetch_forecast(
    *,
    latitude: float,
    longitude: float,
    models: list[str],
    variables: list[str],
    forecast_url: str,
    forecast_days: int | None = 16,
    timezone_name: str = "GMT",
    extra_params: dict | None = None,
    timeout: int = REQUEST_TIMEOUT,
    max_retries: int = MAX_RETRIES,
) -> dict:
    """GET one multi-model forecast, with retry/backoff. Returns parsed JSON.

    Pass ``forecast_days=None`` to omit it (e.g. when using start_date/end_date).
    Retries on timeouts, connection errors, and transient HTTP statuses
    (429/5xx); raises the last error if all attempts fail.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(variables),
        "models": ",".join(models),
        "timezone": timezone_name,
    }
    if forecast_days is not None:
        params["forecast_days"] = forecast_days
    if extra_params:
        params.update(extra_params)

    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = requests.get(forecast_url, params=params, timeout=timeout)
            if resp.status_code in RETRY_STATUS:
                raise requests.HTTPError(f"transient HTTP {resp.status_code}", response=resp)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            if attempt == max_retries:
                break
            # Honour Retry-After on 429s if present, else exponential backoff.
            wait = BACKOFF_BASE * (2 ** attempt)
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code == 429:
                try:
                    wait = max(wait, int(resp.headers.get("Retry-After", 0)))
                except ValueError:
                    pass
            time.sleep(wait)
    raise last_exc


def _split_key(key: str, model_ids: list[str]) -> tuple[str, str] | None:
    """Split ``temperature_2m_gem_global`` -> ("temperature_2m", "gem_global").

    Matches the longest model-id suffix so ids don't collide. Returns ``None``
    if no known model id is a suffix of ``key``.
    """
    # Longest first: avoids a shorter id masking a longer one.
    for mid in sorted(model_ids, key=len, reverse=True):
        suffix = "_" + mid
        if key.endswith(suffix):
            return key[: -len(suffix)], mid
    return None


def parse_long(
    raw: dict,
    *,
    model_ids: list[str],
    variables: set[str] | list[str],
    location_id: str,
    issued_at: datetime,
    fetched_at: datetime,
    source: str = "live",
) -> list[dict]:
    """Unpack a multi-model response into long records, dropping null values."""
    variables = set(variables)
    hourly = raw.get("hourly", {})
    times = hourly.get("time", [])
    issued_iso = _iso(issued_at)
    fetched_iso = _iso(fetched_at)

    records: list[dict] = []
    for key, series in hourly.items():
        if key == "time":
            continue
        split = _split_key(key, model_ids)
        if split is None:
            continue
        variable, model = split
        if variable not in variables:
            continue
        for valid_time, value in zip(times, series):
            if value is None:
                continue
            records.append(
                {
                    "location": location_id,
                    "model": model,
                    "issued_at": issued_iso,
                    "valid_time": valid_time,  # local-naive ISO in requested tz (GMT)
                    "variable": variable,
                    "value": float(value),
                    "source": source,
                    "fetched_at": fetched_iso,
                }
            )
    return records


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
