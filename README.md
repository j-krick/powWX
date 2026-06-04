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

## Status

- **Phase 0 — loggers (done, live).** Forecast runs and webcam frames *cannot be
  recovered retroactively*, so these run on a schedule from day one.
  - **Forecast logger** — pulls 7 deterministic models from Open-Meteo for both
    points and appends to a Parquet log. `forecast-logger.yml`, every 6 h.
  - **Webcam grabber** — captures both Shames frames to Cloudflare R2.
    `webcam-grabber.yml`, every 30 min during daylight.
  - **Backfill** — `scripts/backfill_previous_runs.py` bootstraps verification
    history from Open-Meteo's Previous Runs API (GFS 2m-temp from Mar 2021;
    other models from ~Jan 2024). 8.7 M rows logged.
- **Phase 1 — viewer (done).** Multi-model forecasts at both elevations,
  POW-O-METER (top) + station #58 (bottom) observations, webcams with timelapse,
  and pan/zoom/date-range controls. Static site on GitHub Pages (`viewer.yml`).
- **Phase 2 — verification (in progress, temperature-first).** Join the forecast
  log to observed actuals and compute per-model error by lead time — *which
  model wins, where, at which horizon.* See below.

See `shames-weather-tool-brief.md` for the full vision.

## Phase 2 — forecast verification

The novel part: for each `(location, model, lead day)` we compute **MAE**, **bias**
(forecast − observed) and **RMSE** against the actuals, and surface the
best-performing model. The viewer's *Forecast verification* section charts error
vs. lead time and ranks the models on a leaderboard.

- **`powwx/verification.py`** — pure join/metrics core. `load_forecast_log` reads
  the committed Parquet, `align_to_observations` matches each forecast to the
  nearest observation (`merge_asof`, ±30 min) and computes signed error + lead
  day, and `metrics_by_lead` / `overall_metrics` aggregate.
- **Conditional ("by condition") verification** — `add_strata` +
  `stratified_metrics` break performance down by **season**, **observed-temperature
  bin**, and **time of day**, computed *day-ahead* (lead day 1) so models with
  different horizons compare fairly. This surfaces regime-dependent skill: e.g.
  GEM RDPS leads in the well-mixed afternoon while ECMWF AIFS wins cold, stable
  overnight conditions. The viewer has a selector to switch dimensions. (Standard
  practice — see Murphy & Winkler's joint-distribution framework / WWRP guidance.)
- **`scripts/build_verification.py`** → `viewer/data/verification.json`. Runs in
  the viewer workflow on every deploy.
- **powWX blend** (`powwx/blend.py`) — our own composed temperature forecast: each
  model's per-lead **bias is removed** and the models are combined **inverse-MAE
  weighted** (a bias-corrected consensus, à la NWS National Blend of Models /
  Krishnamurti superensemble). Coefficients are learned from a trailing 90-day
  window; the blend is evaluated **walk-forward (out-of-sample)** — retrained each
  month on only prior data — and added to the leaderboard as `powwx_blend` so the
  "does it beat the best single model?" question is answered honestly. It does at
  the top (≈ +3 % MAE) and through the 1–5-day range at the bottom. The live
  blended series is written to `blend.json` and shown as a bold gold line in the
  temperature chart. **Strictly causal** — no forecast is informed by its own
  outcome (see the tests).
- **Actuals by station:**
  - POW-O-METER (top) is read straight from its Google Sheet (durable history
    back to 2025-01-26), so the backfill is verifiable **today** — ~243 k matched
    temperature hours.
  - Station #58 (bottom) is logged forward by the **observation logger** below
    (AvCan's API keeps only ~7 days, so un-logged obs are lost — same deadline
    logic as forecasts), **and** backfilled from the PCIC/PCDS portal (below):
    the full archive (2010→now, incl. historical wind & humidity) yields ~534 k
    matched temperature hours over the existing forecast backfill.

**PCDS historical import (bottom station).** The Pacific Climate Impacts
Consortium [PCDS portal](https://services.pacificclimate.org/met-data-portal-pcds/app)
serves station #58's full history (Network `MoTIe`, Native ID `52401`) — unlike
the 7-day AvCan API. Download the station CSV, then:

```sh
python scripts/import_pcds.py ~/Downloads/pcds_data   # appends to data/observations/
```

`powwx/observations.py:parse_pcds_csv` maps the PCDS native fields to powWX
variables. Note PCDS timestamps are **local Pacific time *with* DST**
(`America/Vancouver`), verified to the second against the AvCan feed — a fixed
−8 offset would be an hour wrong all summer. Re-importing is safe (duplicate
hours collapse on read).

**Observation logger** — `powwx/obs_logger.py` + `scripts/run_obs_logger.py`,
append-only to `data/observations/obs_date=YYYY-MM-DD/*.parquet`, committed by
`obs-logger.yml` (every 6 h). Station #58 only; POW-O-METER stays in its sheet.

GEPS (Canadian ensemble) and fanning verification out to wind / precip / snow
depth are the remaining Phase 2 work.

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
python scripts/run_obs_logger.py           # one obs run    -> data/observations/
python scripts/backfill_previous_runs.py --past-days 7
python scripts/run_webcam_grabber.py       # needs R2_* env vars
python scripts/import_pcds.py ~/Downloads/pcds_data   # backfill #58 obs from PCDS
python scripts/build_verification.py       # metrics -> viewer/data/verification.json
python scripts/build_viewer_data.py        # viewer JSON -> viewer/data/
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
