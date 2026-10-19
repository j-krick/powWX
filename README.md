# powWX — Shames backcountry weather & forecast-verification tool

A free, community-facing tool for Shames Mountain (near Terrace, BC) backcountry
trip planning. It shows recent observed weather, current multi-model forecasts,
and — the novel part — **forecast verification**: how each model's predictions
compare to what was actually observed, so we learn which models perform best here
and under which conditions.

Two observation points at two elevations:

| Station | Role | Elevation | Coords |
|---|---|---|---|
| POW-O-METER | top of hill | 1150 m | 54.49819, -128.96317 (moved ~70 m from 54.49837, -128.96421 on 2026-09-26) |
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
  model's **bias is removed** and the models are combined **weighted by skill**
  (`w ∝ 1/MAE²`), with coefficients learned per `(model, lead_day, time-of-day)`
  — a bias-corrected consensus à la NWS National Blend of Models / Krishnamurti
  superensemble. Time-of-day is the regime to condition on (it's known at forecast
  time and well sampled in any window; *season* isn't — a 90-day window is almost
  one season). The weighting scheme was picked by a walk-forward sweep
  (`scripts/exp_blend_weighting.py`); notably "just use the best model" was far
  worse (−18 % at the bottom) — error cancellation matters.
- Coefficients use a trailing 90-day window; the blend is evaluated **walk-forward
  (out-of-sample)** — retrained monthly on only prior data — and added to the
  leaderboard as `powwx_blend`. It **beats the best single model at both stations**
  (top ≈ +7.5 %, bottom ≈ +1.5 % MAE).
- **Uncertainty band** — the live blend carries an 80 % range from the empirical
  per-lead distribution of its own out-of-sample errors (shown as a gold ribbon
  that widens with lead time). It's calibration-checked: `band_coverage_oos`
  learns the quantiles on early data and verifies coverage on later data (~76–78 %
  vs the 80 % target). The live series + band are written to `blend.json`.
- **Strictly causal** — no forecast is informed by its own outcome (see the tests).
- **Freezing-level estimate** (`powwx/freezing_level.py`) — the 0 °C isotherm height
  from the two stations' temperatures by lapse-rate extrapolation
  (`H₀ = z_b + T_b·Δz/(T_b−T_t)`), with a propagated ±1σ band and a **regime flag**
  (interp / above / below / inversion). The reliable *interpolation* regime
  (0 °C between 740–1150 m) is drawn solid; **above/below extrapolation** is drawn
  dashed and bounded (band ≤ ±500 m *and* a hard distance cap of +700 m / −500 m
  beyond the stations, since the band models only measurement noise, not the lapse
  rate changing with height). Inversions stay gaps. Shipped in
  `freezing_level.json` (with a per-point regime). It's a winter/spring product — the top station is
  off in summer. A model-vs-estimate **consistency cross-check** (interp regime,
  day-ahead) is wired into `verification.json` but stays dormant until model
  `freezing_level_height` (live-only; not in the Previous-Runs backfill) overlaps
  the estimate next winter. It's framed as corroboration, not ground-truth scoring:
  the model value is a free-atmosphere column diagnostic, the estimate a surface
  one.
- **Actuals by station:**
  - POW-O-METER (top) comes from powOmeter's published v1 files
    (`best_hourly.csv`: true UTC, on the hour, history back to 2025-01-26), copied
    into the observation log below, so the backfill is verifiable without a live
    fetch. Until 2026-10-16 it was read from the station's Google Sheet, whose
    pre-2026-09-22 times were an hour off in daylight-saving time; top-station
    numbers from before the switch are superseded.
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
−8 offset would be an hour wrong all summer. Re-importing is safe: when an hour
is logged more than once, the most recently fetched value wins on read.

**Observation logger** — `powwx/obs_logger.py` + `scripts/run_obs_logger.py`,
append-only to `data/observations/obs_date=YYYY-MM-DD/*.parquet`, committed by
`obs-logger.yml` (every 6 h). Station #58, plus POW-O-METER temperature and
humidity (new hours only, `source=powometer_v1`). POW-O-METER snow depth is not
logged: it must stay split by site across the 2026-09-26 move.

GEPS (Canadian ensemble) is logged to `data/ensembles/` but not yet verified.
Verifying it and fanning out to wind / precip / snow depth are the remaining
Phase 2 work.

## Season in context (historical comparison)

A page section answering "is this season unusual?" for temperature (winter
1 Nov–30 Apr, summer 1 May–31 Oct) and snow depth (1 Oct–30 Jun axis) at both
stations: this season against past seasons and a 1991–2020 normal band, how
unusual the last 7 and next 7 days are (percentiles), and snow milestones at #58.

- **Normals come from ERA5-Land reanalysis** (modelled, via Open-Meteo's archive
  API), adjusted to each station by its monthly mean difference, because the
  station records are far shorter than 30 years. Checked 2026-10-16: daily
  anomalies correlate 0.94 with #58; ERA5-Land snow depth runs ~2.4× deep, so it
  is only shown as % of its own median. Never use `best_match` for history (it
  switches to ECMWF IFS from 2017).
- **#58 reads ~0.3 °C cooler relative to ERA5-Land since its 2021 outage**
  (maybe an equipment change); the adjustment uses data from 2021-10-15 and the
  page states the step as a caveat on ranks.
- Code: `powwx/history.py` (pure statistics, no powWX imports, so it can be
  copied into powOmeter), `powwx/reanalysis.py`, `scripts/fetch_reanalysis.py`,
  `scripts/build_history.py` → `viewer/data/history.json` (built in `viewer.yml`,
  ~90 KB, loaded only when the section scrolls into view). Settings, including
  the **station #58 kill switch**, are in `config/history.yaml`.
- Cache: `data/reanalysis/<model>/<location>/*.parquet` (append-only, latest
  fetch wins, ~1 MB). The full backfill runs **locally**:
  `python scripts/fetch_reanalysis.py --start 1940-01-01`; `reanalysis.yml`
  tops up the last 90 days weekly.
- **Long view** (is it warming?): winter, summer and annual means from ERA5-Land,
  ERA5 and ECCC's homogenized Terrace record (AHCCD, measured, 1913–2020), with
  Sen's-slope trends and 90 % block-bootstrap ranges. The page says "warming"
  only when every record's range is above zero. As of 2026-10: all three agree
  over their full lengths (+0.14 to +0.51 °C/decade), but not since 1980 (the
  reanalyses say +0.25 to +0.62, Terrace measured +0.04, range across zero).
- **El Niño card**: past winters grouped by NOAA's RONI (Nov–Jan), with ERA5-Land
  winter temperature (trend removed) and 1 April modelled snowpack, a
  permutation test against all other winters, and #58's 1 April ranks.
  RONI and AHCCD are refreshed by the same weekly job (`powwx/climate_sources.py`,
  saved to `data/reanalysis/roni/` and `data/reanalysis/ahccd/` only when changed).

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
- `issued_at` — for the **live** logger this is the request time (`fetched_at`),
  and lead time = `valid_time − issued_at`: how far ahead the forecast was when
  someone could read it.
  For **backfill** rows (`source = previous_runs`) `issued_at` is
  `valid_time − N days` for the `_previous_dayN` offset. The value itself comes
  from the newest run *initialised* at least N days before `valid_time`, so its
  true lead is 24N to 24N+5 h (checked against the Single Runs API, 2026-10-19,
  GFS and ICON alike). Live runs are 5–12 h old when fetched, so a live "day N"
  is a somewhat longer true lead than a backfill "day N".
- `run_init` — live rows since 2026-10-20: the model run's initialisation time,
  from Open-Meteo's `meta.json` for the domain in `config/models.yaml`
  (`run_meta`). Null when it couldn't be read or a new run landed mid-fetch.
- **Never overwrite.** Each run writes a new file. Keeping the same `valid_time`
  across many `issued_at` (the 3-days-out vs 1-day-out forecast) is the entire
  basis of verification.
- **Duplicates on read: the last file wins.** If the same `(model, issued_at,
  valid_time)` appears in more than one file (e.g. a re-run backfill), the
  verification keeps the one from the latest file in name order; files are never
  edited. Observations work the same way: the latest fetch of an hour wins.

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
python scripts/build_history.py            # season history -> viewer/data/history.json
python scripts/fetch_reanalysis.py --recent 90   # top up the ERA5 cache
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

Climate normals: ERA5-Land and ERA5 reanalysis (Copernicus Climate Change
Service), via Open-Meteo. Contains modified Copernicus Climate Change Service
information; neither the European Commission nor ECMWF is responsible for any
use that may be made of the information it contains.

Terrace homogenized temperature: Environment and Climate Change Canada, Adjusted
and Homogenized Canadian Climate Data (AHCCD), Open Government Licence – Canada.
El Niño index (RONI): NOAA Climate Prediction Center.

POW-O-METER data: Mount Remo Backcountry Society, CC BY 4.0.

Station #58 data: BC Ministry of Transportation and Infrastructure (MoTI), via
Avalanche Canada and PCIC. MoTI's station data are "Access Only"; written
permission to use them in powWX and powOmeter was granted on 2026-10-16 and is
on file with MRBS (see powOmeter's `docs/permissions/`). Conditions: credit BC
MoTI wherever the data are shown, and state that MoTI is not liable for their
accuracy.
