# Project Brief: powWX — Shames Backcountry Weather & Forecast-Verification Tool

> **Tool name:** **powWX**. Use this as the project/repo name and throughout.

> **Project location:** Create the project folder under `C:\Users\Julian.Krick\src\`
> (e.g. `C:\Users\Julian.Krick\src\powWX\`). This is a Windows environment.

> **Purpose of this document:** This is a planning brief, not a spec. Read it, ask me
> clarifying questions where you need to, then produce a phased implementation plan and a
> proposed repo structure. Do **not** start writing application code until we've agreed on
> the plan. The one exception is noted under "What has a deadline" below.

---

## 1. Context and goal

I'm a splitboarder near Terrace, BC. Reliable weather data for backcountry trip planning
in this region is sparse. I want to build a free, community-facing tool that helps with
trip planning by showing:

1. **Recent observed weather** (last several days) at the mountain.
2. **Current forecasts** for the next several days, from multiple named models, shown
   side by side.
3. **Forecast verification** — compare what each model *predicted* against what was
   *observed*, so we can learn which models perform best in this region and under which
   conditions.

The verification piece (#3) is the novel, valuable part. Items #1 and #2 overlap with
existing tools (SpotWX, snow-forecast, meteoblue, AlpineFX), so they only need to be good
enough and tuned to our specific stations — not reinvented.

Reference for look/feel and scope (a solo passion project that aggregates cams, multi-model
forecasts at multiple elevations, temps, and history): https://whistlerpeak.com/

End goal: host it cheaply or free, make it available to the local backcountry community,
and improve trip-planning safety.

---

## 2. What has a deadline (do this conceptually first)

**Forecast data and webcam images cannot be recovered retroactively** beyond what archives
already hold. Every day a logger is not running is data we can never get back. Therefore the
**forecast logger** and **webcam grabber** are the time-critical components and should be
designed and stood up before any UI work.

Caveat that softens this: Open-Meteo has a **Previous Runs API** (archived model runs at
fixed lead-time offsets, 1–7 days ahead; data from ~Jan 2024, GFS from Mar 2021). For some
of our models we may be able to **backfill** a verification dataset from this archive rather
than waiting months. Please check archive coverage for each model in our set when planning
phase 2. The live logger is still needed (archives lag and don't cover everything), but
phase 2 may have data to work with much sooner than otherwise.

---

## 3. Locations and observation sources (the "actuals")

Two real observation points at two elevations:

| Source | Role | Elevation | Coordinates | Variables observed | Access |
|---|---|---|---|---|---|
| **POW-O-METER** (mine) | Top of hill | 1150 m | 54.49837°N, 128.96421°W | air temp, snow depth, relative humidity (**wind sensor may be added later**) | Data flows satellite → Gmail → **Google Sheet** (kept as the data hub for now) |
| **DriveBC / Avalanche Canada station #58** | Bottom of hill | 740 m | 54.484970°N, 128.955860°W | air temp, precipitation, snow depth, wind | DriveBC road-weather network; Avalanche Canada has a queryable weather-station API (station id 58). **Note: the MoTI station and Avi Canada #58 are the same physical station.** |

POW-O-METER notes:
- I own and built this station; I have all the historical data and the ingestion scripts.
- Current public display (Looker Studio): https://lookerstudio.google.com/u/0/reporting/802c4e76-7a23-4d8f-8c19-9962da7765e6/page/tEjeE
- Keep the Google Sheet as the database for v1 (free, already works, readable from R/Python
  via published CSV or the Sheets API). Migration to a real DB is a possible later step, not now.

**Verification quality by variable, be realistic about this:**
- Temperature — strongest signal, verifiable at **both** elevations.
- Wind — verifiable at the **bottom** only until I add a top wind sensor (when I do, that
  directly unlocks top-of-hill wind verification, which is arguably the most
  decision-relevant variable for avalanche/wind-slab concerns).
- Precipitation — verifiable at the bottom.
- Snow depth — verifiable at both, but **noisy**: modelled snowfall vs. measured depth
  involves settlement/compaction, so treat depth-based precip verification as approximate.
- Relative humidity — loggable and comparable, but rarely the decision driver.

---

## 4. Forecast sources (the models to log)

**Primary data layer: Open-Meteo** (free, no API key, CC BY 4.0, attribution required;
free for non-commercial use up to 10,000 calls/day). It serves raw, named, individually
selectable model output — the correct substrate for clean per-model verification.

**v1 logging set — all deterministic, two endpoints, one consistent pipeline:**

| Model | Open-Meteo identifier | Endpoint | Notes |
|---|---|---|---|
| GEM HRDPS | `cmc_gem_hrdps` | `/v1/gem` | 2.5 km, ~2-day horizon |
| GEM RDPS | `cmc_gem_rdps` | `/v1/gem` | 10 km, ~3.5-day |
| GEM GDPS | `cmc_gem_gdps` | `/v1/gem` | 15 km, ~10-day |
| ECMWF IFS | `ecmwf_ifs` | `/v1/ecmwf` | 9 km; 1-hourly to 90 h then coarsens |
| ECMWF AIFS Single | `ecmwf_aifs025_single` | `/v1/ecmwf` | AI model; currently 6-hourly only |
| GFS | `gfs_global` (HRRR rides along for short lead times) | forecast API | |
| **ICON Global** (my add) | `icon_global` | forecast API | strong, independent global; worth including |

Please confirm exact identifier strings against current Open-Meteo docs when you build —
they occasionally change.

**Deferred to phase 2:** **GEPS** (Canadian ensemble) — available only via the **Ensemble
API** (`/v1/ensemble`), returns up to 51 members per run, needs different handling than
deterministic models. Add once the deterministic pipeline is solid.

**Explicitly dropped, with reasons:**
- **HRDPS 1 km West** — not on Open-Meteo (only the 2.5 km national HRDPS is). Would require
  a separate, messier GRIB2 pipeline from ECCC MSC GeoMet/Datamart. Possible future add, not v1.
- **RAP, NAM, SREF** — not exposed by Open-Meteo; would need direct NOAA GRIB pipelines, and
  their coverage/quality is poor at our latitude (54°N, north of CONUS). Not worth the effort.

**meteoblue — link-out only, do NOT ingest.** Reasons: (a) its public value is proprietary
post-processing (their MOS and "Learning MultiModel" blend), so it is *not* raw named-model
output and is the wrong unit for "which raw model wins"; (b) meteoMail delivers a forecast
*image* (a 5-day meteogram PNG), not structured data — parsing it would mean OCR on a daily
picture, fragile and low-value; (c) scraping their site is against their terms and a poor
foundation for a public safety project. meteoblue stays as a human-facing cross-check link
alongside SpotWX and snow-forecast. (Their public short-term verification meteogram and
forecast-accuracy pages are worth a look as *design references* for phase 2 — reference only.)

---

## 5. Webcams

Two Shames webcams publish the **current** frame at stable public URLs (Google Cloud Storage):
- Chairlift: `https://storage.googleapis.com/shames_webcam_images/camera1-latest-timestamp.png`
- Parking lot: `https://storage.googleapis.com/shames_webcam_images/camera2-latest-timestamp.png`

Requirements:
- Embed the live frames in the viewer.
- **Capture frames on a schedule** to build history / timelapse video (I want to be able to
  view older photos, ideally as a video). Use ffmpeg to stitch frames.
- **Important:** these URLs only ever hold the *latest* image — there is no retroactive
  archive, so historical frames only exist if we start capturing now (same deadline logic
  as forecasts).
- **Do not commit a growing pile of PNGs into the git repo** — that bloats it fast. Plan for
  cheap object storage from day one. Propose options.

The cameras run during winter and part of summer; expect gaps when they're off.

The Shames co-op snow report (https://mymountaincoop.ca/shames-mountain/our-mountain/snow-report/)
has human-entered fields that blank out of season — **link out to it, don't scrape it.**

---

## 6. Critical data-model requirement (please honour this exactly)

The forecast logger must be **append-only** and key every record on:

```
(model, issued_at, valid_time, variable, value)
```

- `issued_at` = when the forecast run was made.
- `valid_time` = the time the value is forecasting for.
- **Never overwrite.** A common mistake is logging "the current forecast" and overwriting it,
  which destroys the ability to compare lead times (what the model said 3 days out vs. 1 day
  out). Preserving both timestamps is the whole basis of verification. Do not collapse this.

Storage for logged forecasts: keep it simple — append to CSV or Parquet committed back to the
repo by the scheduled job. Free, versioned, no database. At our volume (2 points, ~7 models,
every 6–12 h) this stays manageable for years. Migrate later only if needed. (Webcam images
are the exception — those go to object storage, not git, per section 5.)

---

## 7. Scheduling / hosting

- I have a **GitHub account** and use it with Claude Code. Assume **GitHub Actions** for the
  scheduled jobs (free, cron-capable, can commit results back to the repo). The logger and
  webcam grabber run on a cron schedule (propose cadence — I'm thinking forecasts every 6–12 h,
  webcams more often during daylight).
- Hosting the viewer: keep it cheap/free. A static site (e.g. GitHub Pages) reading committed
  data files is attractive. Propose options and trade-offs.

---

## 8. Language preferences

- I program mainly in **R**, also **Python** and **Matlab**. Intermediate level.
- Reasonable split to propose: **Python for collection** (clean scheduling on Actions,
  `requests`/`pandas`, tidy Open-Meteo client) and **R for analysis/verification**
  (`arrow`, `dplyr`, stats and plotting), both reading the same Parquet files. If you think
  keeping the whole thing in R (`httr2` + `targets` + cron Action) is better for me given my
  background, make the case. I'm open.

---

## 9. Phasing (proposed — refine it)

- **Phase 0 — Forecast logger + webcam grabber.** Time-critical. Ugly is fine. No UI. Get it
  running on a schedule and confirm data is accumulating. Also evaluate Open-Meteo Previous
  Runs archive coverage for our models to bootstrap verification.
- **Phase 1 — Viewer.** Pull recent observations (POW-O-METER + station #58) and current
  multi-model forecasts; present cleanly, tuned to our two elevations; embed webcams + timelapse.
- **Phase 2 — Verification.** Once enough forecast history exists (logged + any backfill),
  join forecasts to observations, compute error metrics per model / lead-time / variable, and
  surface which model is performing best, by variable and condition.

---

## 10. What I want from you (Claude Code) first

1. Ask any clarifying questions you need.
2. Create the project folder at `C:\Users\Julian.Krick\src\powWX\` (Windows) and use
   **powWX** as the repo name.
3. Propose a **repo structure** and a **phased implementation plan** matching the phasing above.
4. Recommend the **language split** and **hosting/storage** choices with trade-offs.
5. Confirm Open-Meteo model identifiers and Previous Runs archive coverage for our set.
6. Then, with my sign-off, scaffold **Phase 0** first — the append-only forecast logger and the
   webcam grabber on GitHub Actions — because that is the only piece with a real deadline.

Please do not over-engineer v1. Simple, append-only, file-based, free.
