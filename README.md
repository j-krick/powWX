# powWX — Shames backcountry weather & forecast-verification tool

A free, community-facing tool for Shames Mountain (near Terrace, BC) backcountry
trip planning. It shows recent observed weather, current multi-model forecasts,
and — the novel part — **forecast verification**: how each model's predictions
compare to what was actually observed, so we learn which models perform best here
and under which conditions.

Two observation points at two elevations:

| Station | Role | Elevation | Coords |
|---|---|---|---|
| POW-O-METER | top of hill | 1150 m | 54.49837, -128.96421 |
| Avalanche Canada / DriveBC #58 | bottom of hill | 740 m | 54.48497, -128.95586 |

## Status — Phase 0 (the time-critical logger)

Forecast runs and webcam frames **cannot be recovered retroactively**, so the
logger and webcam grabber are built first and run on a schedule from day one.

- **Forecast logger** — pulls 7 deterministic models from Open-Meteo for both
  points and appends to a Parquet log. `.github/workflows/forecast-logger.yml`
  runs every 6 h.
- **Webcam grabber** — captures both Shames frames to Cloudflare R2.
  `.github/workflows/webcam-grabber.yml` runs every 30 min during daylight.
- **Backfill** — `scripts/backfill_previous_runs.py` bootstraps verification
  history from Open-Meteo's Previous Runs API (GFS 2m-temp from Mar 2021; other
  models from ~Jan 2024).

Phase 1 (viewer) and Phase 2 (verification metrics + GEPS ensemble) follow once
data accrues. See `shames-weather-tool-brief.md` for the full vision.

## Models logged

`gem_hrdps_continental`, `gem_regional`, `gem_global`, `ecmwf_ifs025`,
`ecmwf_aifs025_single`, `gfs_seamless`, `icon_global` (all verified resolving on
`/v1/forecast` for Shames on 2026-05-29). Defined in `config/models.yaml`.

> Note: HRRR is CONUS-only and does **not** cover Shames (54.5 °N); `gfs_seamless`
> runs alone here. GEPS (ensemble) is deferred to Phase 2.

## The forecast data model (append-only — do not collapse)

Every record is keyed on **`(location, model, issued_at, valid_time, variable, value)`**
plus `source` and `fetched_at` provenance.

- `valid_time` — the time the value forecasts for.
- `issued_at` — for the **live** logger this is the request time (`fetched_at`):
  Open-Meteo's forecast endpoint does not expose per-model run/init times, so the
  request time is the honest reference, and lead time = `valid_time − issued_at`.
  For **backfill** rows (`source = previous_runs`) `issued_at` is exact:
  `valid_time − N days` for the `_previous_dayN` offset.
- **Never overwrite.** Each run writes a new file. Keeping the same `valid_time`
  across many `issued_at` (the 3-days-out vs 1-day-out forecast) is the entire
  basis of verification.

Layout: `data/forecasts/issued_date=YYYY-MM-DD/<run>.parquet` (committed to git).

## Local development

```sh
python -m venv .venv && .venv\Scripts\activate    # Windows
pip install -e ".[dev]"

# Behind a corporate TLS proxy? `pip install -e ".[dev]"` pulls in `truststore`,
# which the scripts auto-enable to use the Windows cert store. No-op otherwise.

python scripts/verify_models.py            # check model ids still resolve
python scripts/run_forecast_logger.py      # one logging run -> data/forecasts/
python scripts/backfill_previous_runs.py --past-days 7
python scripts/run_webcam_grabber.py       # needs R2_* env vars
```

## GitHub Actions secrets (webcam grabber)

Set these repo secrets for Cloudflare R2 (free tier: 10 GB + zero egress):

| Secret | Meaning |
|---|---|
| `R2_ACCOUNT_ID` | Cloudflare account id (R2 endpoint host) |
| `R2_ACCESS_KEY_ID` | R2 API token access key |
| `R2_SECRET_ACCESS_KEY` | R2 API token secret |
| `R2_BUCKET` | Bucket name, e.g. `powwx-webcam` |

## Attribution & license

Weather data by [Open-Meteo](https://open-meteo.com/) under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) — attribution required;
keep this notice on any public viewer. Code is MIT licensed.
