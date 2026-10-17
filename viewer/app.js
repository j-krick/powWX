"use strict";

// Stable colour per model so a model looks the same across every chart.
const MODEL_COLORS = {
  gem_hrdps_continental: "#e6194B",
  gem_regional: "#f58231",
  gem_global: "#e6c200",
  ecmwf_ifs025: "#4363d8",
  ecmwf_aifs025_single: "#42d4f4",
  gfs_seamless: "#3cb44b",
  icon_global: "#b05ce6",
  powwx_blend: "#ffcc33",   // bias-corrected consensus — distinct gold
};
const OBS_COLOR = "#ffffff";

// Which variables get a chart/table panel, in display order, with the daily
// table aggregation that makes sense for each.
const CHART_VARS = [
  { key: "temperature_2m", title: "Temperature", agg: "minmax" },
  { key: "freezing_level_height", title: "Freezing level", agg: "minmax", refElevation: true },
  { key: "wind_speed_10m", title: "Wind speed", agg: "max" },
  { key: "wind_direction_10m", title: "Wind direction", agg: "dir", scatter: true, degrees: true },
  { key: "precipitation", title: "Precipitation", agg: "sum" },
  { key: "snowfall", title: "Snowfall", agg: "sum" },
  { key: "snow_depth", title: "Snow depth", agg: "max" },
  { key: "relative_humidity_2m", title: "Humidity", agg: "mean" },
];

async function loadJSON(name) {
  const r = await fetch(`data/${name}.json`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${name}.json: ${r.status}`);
  return r.json();
}

// ---- shared time-axis range (pan/zoom across all charts) -----------------
const DAY = 86400000;
const allCharts = [];
const VIEW = { min: null, max: null }; // current x-window in epoch ms; null = fit data
let syncing = false;

// Apply an x-range to every chart so panning/zooming one moves them all together.
// min/max are epoch ms, or null to fit the data extent.
function applyRange(min, max) {
  VIEW.min = min; VIEW.max = max;
  syncing = true;
  for (const ch of allCharts) {
    ch.options.scales.x.min = min == null ? undefined : min;
    ch.options.scales.x.max = max == null ? undefined : max;
    ch.update("none");
  }
  syncing = false;
  reflectRange();
}

// Fired by the zoom plugin after a user pan/zoom gesture on one chart.
function onGestureRange({ chart }) {
  if (syncing) return;
  const s = chart.scales.x;
  applyRange(s.min, s.max);
}

function toDateInput(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

// Update the range label + date inputs to match what's currently shown.
function reflectRange() {
  const label = document.getElementById("range-label");
  if (!label) return;
  let min = VIEW.min, max = VIEW.max;
  if ((min == null || max == null) && allCharts.length) {
    const s = allCharts[0].scales.x; // when fitting data, read the rendered extent
    if (min == null) min = s.min;
    if (max == null) max = s.max;
  }
  if (min != null && max != null) {
    const o = { month: "short", day: "numeric", hour: "numeric" };
    label.textContent = `${fmtLocal(min, o)} → ${fmtLocal(max, o)}`;
    const from = document.getElementById("range-from");
    const to = document.getElementById("range-to");
    if (from) from.value = toDateInput(min);
    if (to) to.value = toDateInput(max);
  }
}

function buildRangeControls() {
  const root = document.getElementById("range-controls");
  if (!root) return;
  root.innerHTML = "";
  const now = Date.now();
  const mkBtn = (text, fn) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = text; b.onclick = fn;
    return b;
  };

  const presets = document.createElement("div");
  presets.className = "range-presets";
  presets.append(
    mkBtn("Next 3 days", () => applyRange(now - 0.5 * DAY, now + 3 * DAY)),
    mkBtn("Next 7 days", () => applyRange(now - 0.5 * DAY, now + 7 * DAY)),
    mkBtn("Last 3 days", () => applyRange(now - 3 * DAY, now + 0.5 * DAY)),
    mkBtn("All", () => applyRange(null, null)),
  );

  const custom = document.createElement("div");
  custom.className = "range-custom";
  const from = document.createElement("input"); from.type = "date"; from.id = "range-from";
  const to = document.createElement("input"); to.type = "date"; to.id = "range-to";
  const apply = mkBtn("Apply", () => {
    const f = from.value ? new Date(from.value + "T00:00").getTime() : null;
    const t = to.value ? new Date(to.value + "T23:59").getTime() : null;
    applyRange(f, t);
  });
  custom.append(document.createTextNode("From "), from,
    document.createTextNode(" to "), to, apply);

  const label = document.createElement("span");
  label.id = "range-label"; label.className = "range-label";
  const hint = document.createElement("span");
  hint.className = "range-hint"; hint.textContent = "scroll to zoom · drag to pan";

  root.append(presets, custom, label, hint);
  reflectRange();
}

// Optional deep link: #2026-04-01..2026-05-25 sets the initial time window
// (shareable range links; also how the past-only obs/FL overlays are reached).
function applyHashRange() {
  const m = (location.hash || "").match(/(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})/);
  if (m) applyRange(new Date(m[1] + "T00:00").getTime(), new Date(m[2] + "T23:59").getTime());
}

// Hourly UTC timestamp string in the data's format ("YYYY-MM-DDTHH:00Z"), used to
// densify a sparse series' time grid so real gaps render as breaks, not a line.
function isoHour(ms) {
  const d = new Date(ms), p = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth() + 1)}-${p(d.getUTCDate())}T${p(d.getUTCHours())}:00Z`;
}

function fmtLocal(iso, opts) {
  return new Date(iso).toLocaleString(undefined,
    opts || { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}
function dayKey(iso) {
  return new Date(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

function aggregate(values, type) {
  const v = values.filter((x) => x !== null && x !== undefined);
  if (!v.length) return "—";
  if (type === "minmax") return `${Math.round(Math.min(...v))} / ${Math.round(Math.max(...v))}`;
  if (type === "max") return Math.round(Math.max(...v));
  if (type === "mean") return Math.round(v.reduce((a, b) => a + b, 0) / v.length);
  if (type === "sum") return (v.reduce((a, b) => a + b, 0)).toFixed(1);
  if (type === "dir") {
    // Circular (vector) mean — a plain average of degrees wraps wrong at 0/360.
    const rad = v.map((d) => (d * Math.PI) / 180);
    const s = rad.reduce((a, r) => a + Math.sin(r), 0);
    const c = rad.reduce((a, r) => a + Math.cos(r), 0);
    let deg = Math.round((Math.atan2(s, c) * 180) / Math.PI);
    if (deg < 0) deg += 360;
    const pts = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
    return `${deg}° ${pts[Math.round(deg / 45) % 8]}`;
  }
  return "—";
}

// ---- forecast section ----------------------------------------------------

const COMPASS = { 0: "N", 90: "E", 180: "S", 270: "W", 360: "N" };

function makeChart(canvas, loc, vcfg, fc, obs, blendLoc, flEst) {
  const node = fc.forecast[loc][vcfg.key];
  const scatter = !!vcfg.scatter; // wind direction: points, not a wrapping line
  const ov = obs[loc] && obs[loc][vcfg.key];
  // The powWX blend is a temperature-only product (for now).
  const blend = vcfg.key === "temperature_2m" ? blendLoc : null;
  // Observed freezing-level estimate (from the two stations) overlays the FL panel.
  const fle = (vcfg.key === "freezing_level_height" && flEst && flEst.times
               && flEst.times.length) ? flEst : null;

  // Unified time axis: model series (-12h..+7d) and observations (past only) sit
  // on different grids, so build one sorted set of all timestamps and align every
  // dataset to it by time. This lets the "index" hover mode line up model vs obs
  // correctly — both sources report on the exact hour, so slots merge cleanly.
  const tset = new Set(node.times);
  if (ov) ov.times.forEach((t) => tset.add(t));
  if (blend) blend.times.forEach((t) => tset.add(t));
  if (fle) {
    // Densify the FL grid to every hour across its span, so the hours it skips
    // (inversion / too-uncertain) become nulls and the line breaks there instead
    // of drawing a misleading straight segment across the gap.
    const a = new Date(fle.times[0]).getTime();
    const b = new Date(fle.times[fle.times.length - 1]).getTime();
    for (let ms = a; ms <= b; ms += 3600000) tset.add(isoHour(ms));
  }
  const times = [...tset].sort();
  const slot = new Map(times.map((t, i) => [t, i]));
  const onGrid = (srcTimes, srcVals) => {
    const arr = new Array(times.length).fill(null);
    srcTimes.forEach((t, i) => { arr[slot.get(t)] = srcVals[i]; });
    return times.map((t, i) => ({ x: t, y: arr[i] }));
  };

  // `modelId` tags each dataset so the shared legend can toggle a model across
  // every chart in the location block.
  const datasets = fc.models
    .filter((m) => node.series[m.id])
    .map((m) => ({
      label: m.label, modelId: m.id,
      data: onGrid(node.times, node.series[m.id]),
      borderColor: MODEL_COLORS[m.id] || "#888",
      backgroundColor: MODEL_COLORS[m.id] || "#888",
      borderWidth: 1.5, tension: 0.25, spanGaps: false,
      showLine: !scatter, pointRadius: scatter ? 2 : 0,
    }));
  if (ov) {
    datasets.push({
      label: "Observed", modelId: "__observed__",
      data: onGrid(ov.times, ov.values),
      borderColor: OBS_COLOR, backgroundColor: OBS_COLOR,
      borderWidth: 2, pointRadius: scatter ? 2.4 : 1.6, tension: 0.2,
      spanGaps: false, showLine: !scatter, order: -1,
    });
  }
  // powWX blend: bias-corrected, skill-weighted consensus. Translucent gold
  // uncertainty band (upper+lower, filled between) under a bold gold line.
  if (blend) {
    if (blend.upper && blend.lower) {
      datasets.push({
        label: "blend upper", modelId: "powwx_blend", isBand: true,
        data: onGrid(blend.times, blend.upper),
        borderWidth: 0, pointRadius: 0, fill: false, tension: 0.25, spanGaps: false, order: 5,
      });
      datasets.push({
        label: "blend lower", modelId: "powwx_blend", isBand: true,
        data: onGrid(blend.times, blend.lower),
        borderWidth: 0, pointRadius: 0, fill: "-1",            // fill to the upper line
        backgroundColor: "rgba(255,204,51,0.15)", tension: 0.25, spanGaps: false, order: 5,
      });
    }
    datasets.push({
      label: "powWX blend", modelId: "powwx_blend",
      data: onGrid(blend.times, blend.values),
      borderColor: MODEL_COLORS.powwx_blend, backgroundColor: MODEL_COLORS.powwx_blend,
      borderWidth: 3, tension: 0.25, pointRadius: 0, spanGaps: false, order: -2,
    });
  }
  // Observed freezing-level estimate from the two stations (solid = interpolated,
  // dashed = extrapolated above/below them):
  // a white line with a ±1σ band, shown in the past where both stations report.
  if (fle) {
    // Regime per chart slot, so extrapolated (above/below) segments draw dashed
    // and the reliable interpolation regime draws solid.
    const flReg = new Map(fle.times.map((t, i) => [t, (fle.regime || [])[i]]));
    const ptReg = times.map((t) => flReg.get(t) ?? null);
    datasets.push({
      label: "FL est upper", modelId: "__flest__", isBand: true,
      data: onGrid(fle.times, fle.upper),
      borderWidth: 0, pointRadius: 0, fill: false, tension: 0.2, spanGaps: false, order: 6,
    });
    datasets.push({
      label: "FL est lower", modelId: "__flest__", isBand: true,
      data: onGrid(fle.times, fle.lower),
      borderWidth: 0, pointRadius: 0, fill: "-1",
      backgroundColor: "rgba(255,255,255,0.12)", tension: 0.2, spanGaps: false, order: 6,
    });
    datasets.push({
      label: "Freezing level (obs. est.)", modelId: "__flest__",
      data: onGrid(fle.times, fle.h0),
      borderColor: "#ffffff", backgroundColor: "#ffffff",
      borderWidth: 2, pointRadius: 0, tension: 0.2, spanGaps: false, order: -1,
      segment: {
        borderDash: (ctx) => {
          const r = ptReg[ctx.p1DataIndex];
          return r && r !== "interp" ? [5, 4] : undefined;  // dashed = extrapolated
        },
      },
    });
  }
  // Freezing level: a dashed reference line at this station's elevation, so you
  // can read at a glance whether the freezing level is above or below you.
  if (vcfg.refElevation && times.length) {
    const elev = fc.locations[loc].elevation_m;
    datasets.push({
      label: `Station elevation (${elev} m)`, modelId: "__ref__",
      data: [{ x: times[0], y: elev }, { x: times[times.length - 1], y: elev }],
      borderColor: "#7d8ea0", backgroundColor: "#7d8ea0",
      borderWidth: 1, borderDash: [6, 4], pointRadius: 0, tension: 0, spanGaps: true,
    });
  }

  const yScale = {
    title: { display: true, text: fc.units[vcfg.key] || "", color: "#9fb0c3" },
    ticks: { color: "#9fb0c3" }, grid: { color: "#243246" },
  };
  if (vcfg.degrees) {
    yScale.min = 0; yScale.max = 360;
    yScale.ticks = { color: "#9fb0c3", stepSize: 90,
      callback: (val) => COMPASS[val] ?? val };
  }

  const dataMin = times.length ? new Date(times[0]).getTime() : undefined;
  const dataMax = times.length ? new Date(times[times.length - 1]).getTime() : undefined;

  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      // "index" mode is reliable now that every dataset shares one time grid: the
      // hovered index is the same timestamp for model and obs alike.
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          titleFont: { size: 11 }, bodyFont: { size: 11 },
          // Drop the elevation reference line, band edges, and gap-filled nulls.
          filter: (item) => item.dataset.modelId !== "__ref__"
            && !item.dataset.isBand && item.parsed.y !== null,
          callbacks: vcfg.degrees
            ? { label: (c) => `${c.dataset.label}: ${Math.round(c.parsed.y)}°` }
            : {},
        },
        // Scroll/pinch to zoom the time axis, drag to pan; gestures sync to every
        // chart via onGestureRange. Limits keep pan/zoom within the loaded data.
        zoom: {
          pan: { enabled: true, mode: "x", onPanComplete: onGestureRange },
          zoom: { wheel: { enabled: true }, pinch: { enabled: true }, mode: "x",
                  onZoomComplete: onGestureRange },
          limits: { x: { min: dataMin, max: dataMax, minRange: 6 * 3600 * 1000 } },
        },
      },
      scales: {
        x: { type: "time",
             min: VIEW.min == null ? undefined : VIEW.min,
             max: VIEW.max == null ? undefined : VIEW.max,
             time: { tooltipFormat: "EEE MMM d, HH:mm" },
             ticks: { color: "#9fb0c3", maxRotation: 0, autoSkipPadding: 20 }, grid: { color: "#243246" } },
        y: yScale,
      },
    },
  });
  allCharts.push(chart);
  return chart;
}

function makeTable(loc, vcfg, fc) {
  const node = fc.forecast[loc][vcfg.key];
  const days = [];
  const dayOf = node.times.map((t) => { const k = dayKey(t); if (!days.includes(k)) days.push(k); return k; });

  const wrap = document.createElement("div");
  wrap.className = "table-scroll as-table";
  const table = document.createElement("table");
  table.className = "fc";
  const unit = fc.units[vcfg.key] || "";
  table.innerHTML =
    `<thead><tr><th>Model</th>${days.map((d) => `<th>${d}</th>`).join("")}</tr></thead>`;
  const tbody = document.createElement("tbody");
  fc.models.filter((m) => node.series[m.id]).forEach((m) => {
    const series = node.series[m.id];
    const cells = days.map((d) => {
      const vals = series.filter((_, i) => dayOf[i] === d);
      return `<td>${aggregate(vals, vcfg.agg)}</td>`;
    });
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${m.label}</td>${cells.join("")}`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  const cap = document.createElement("div");
  cap.className = "note";
  cap.textContent = vcfg.agg === "minmax" ? `daily min / max (${unit})`
    : vcfg.agg === "sum" ? `daily total (${unit})`
    : vcfg.agg === "max" ? `daily max (${unit})` : `daily mean (${unit})`;
  wrap.appendChild(cap);
  return wrap;
}

function buildForecast(fc, obs, blend, flEst) {
  const root = document.getElementById("forecast");
  root.innerHTML = "";
  const blendLocs = (blend && blend.locations) || {};
  const fle = (flEst && flEst.estimate && flEst.estimate.times
               && flEst.estimate.times.length) ? flEst.estimate : null;
  const locIds = Object.keys(fc.locations).sort(
    (a, b) => fc.locations[b].elevation_m - fc.locations[a].elevation_m);

  for (const loc of locIds) {
    const meta = fc.locations[loc];
    const blendLoc = blendLocs[loc] && blendLocs[loc].temperature_2m;
    const block = document.createElement("div");
    block.className = "loc-block";
    block.innerHTML =
      `<div class="loc-head"><h2>${meta.label}</h2>` +
      `<span class="elev">${meta.elevation_m} m</span>` +
      `<span class="role">${meta.role}</span></div>`;

    // shared model legend (+ observed if this location has obs); click a name to
    // show/hide that model across every chart in this location block.
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = fc.models.map((m) =>
      `<span class="leg" data-model="${m.id}"><i style="background:${MODEL_COLORS[m.id] || "#888"}"></i>${m.label}</span>`).join("");
    if (obs[loc]) legend.innerHTML += `<span class="leg" data-model="__observed__"><i style="background:${OBS_COLOR}"></i>Observed <em class="tag">provisional</em></span>`;
    if (blendLoc) legend.innerHTML += `<span class="leg blend-leg" data-model="powwx_blend"><i style="background:${MODEL_COLORS.powwx_blend}"></i>powWX blend <em class="tag">experimental</em></span>`;
    if (fle && fc.forecast[loc] && fc.forecast[loc].freezing_level_height) legend.innerHTML += `<span class="leg" data-model="__flest__"><i style="background:#ffffff"></i>Freezing level (obs. est.) <em class="tag">experimental</em></span>`;
    block.appendChild(legend);

    const locCharts = [];
    const grid = document.createElement("div");
    grid.className = "panel-grid";
    for (const vcfg of CHART_VARS) {
      if (!fc.forecast[loc] || !fc.forecast[loc][vcfg.key]) continue;
      const panel = document.createElement("div");
      panel.className = "panel";
      panel.innerHTML = `<h3>${vcfg.title} <span class="unit">${fc.units[vcfg.key] || ""}</span></h3>`;
      const cw = document.createElement("div");
      cw.className = "chart-wrap";
      const canvas = document.createElement("canvas");
      cw.appendChild(canvas);
      panel.appendChild(cw);
      panel.appendChild(makeTable(loc, vcfg, fc));
      if (vcfg.key === "freezing_level_height" && fle) {
        const cap = document.createElement("div");
        cap.className = "note";
        cap.innerHTML = "<em class=\"tag\">experimental</em> White = freezing level estimated from the two stations "
          + "(solid where 0 °C is between them, <span style=\"border-bottom:1px dashed #9fb0c3\">dashed</span> where extrapolated above/below). "
          + "Gaps = inversion or too uncertain.";
        panel.appendChild(cap);
      }
      grid.appendChild(panel);
      locCharts.push(makeChart(canvas, loc, vcfg, fc, obs, blendLoc, fle));
    }
    block.appendChild(grid);

    legend.querySelectorAll(".leg").forEach((span) => {
      span.onclick = () => {
        const id = span.dataset.model;
        const visible = !span.classList.toggle("off");
        for (const ch of locCharts) {
          ch.data.datasets.forEach((ds, i) => {
            if (ds.modelId === id) ch.setDatasetVisibility(i, visible);
          });
          ch.update();
        }
      };
    });

    root.appendChild(block);
  }
}

// ---- verification (Phase 2) ----------------------------------------------
// "Which model wins, where, at which horizon." Reads verification.json: per
// location/variable, MAE / bias / RMSE per model and lead day. Charts use a
// linear lead-day x-axis (days ahead), so they stay OUT of the time-axis
// pan/zoom sync that the forecast charts share.

const METRIC_LABELS = { mae: "Mean absolute error", bias: "Bias (forecast − observed)" };

function makeMetricChart(canvas, locVar, ver, metric, unit) {
  const maxLead = locVar.max_lead_day || 1;
  const xs = [];
  for (let d = 0; d <= maxLead; d++) xs.push(d);

  const datasets = ver.models
    .filter((m) => locVar.by_lead[m.id])
    .map((m) => {
      const byDay = new Map(locVar.by_lead[m.id].map((r) => [r.lead_day, r]));
      return {
        label: m.label, modelId: m.id,
        data: xs.map((d) => ({ x: d, y: byDay.has(d) ? byDay.get(d)[metric] : null,
                               n: byDay.has(d) ? byDay.get(d).n : null })),
        borderColor: MODEL_COLORS[m.id] || "#888",
        backgroundColor: MODEL_COLORS[m.id] || "#888",
        borderWidth: m.id === "powwx_blend" ? 3 : 1.6, tension: 0.2,
        spanGaps: false, pointRadius: m.id === "powwx_blend" ? 3 : 2.4,
      };
    });
  // Zero reference for the bias chart (perfect = on the line).
  if (metric === "bias") {
    datasets.push({
      label: "zero", modelId: "__zero__",
      data: [{ x: 0, y: 0 }, { x: maxLead, y: 0 }],
      borderColor: "#7d8ea0", borderWidth: 1, borderDash: [6, 4],
      pointRadius: 0, tension: 0, order: 10,
    });
  }

  return new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          titleFont: { size: 11 }, bodyFont: { size: 11 },
          filter: (i) => i.dataset.modelId !== "__zero__" && i.parsed.y !== null,
          callbacks: {
            title: (items) => (items[0].parsed.x === 0 ? "Same day" : `Lead day ${items[0].parsed.x}`),
            label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(2)} ${unit}` +
              (c.raw.n != null ? `  (n ${c.raw.n.toLocaleString()})` : ""),
          },
        },
      },
      scales: {
        x: { type: "linear", min: 0, max: maxLead,
             title: { display: true, text: "Lead time (days ahead; 0 = same day)", color: "#9fb0c3" },
             ticks: { color: "#9fb0c3", stepSize: 1, precision: 0 }, grid: { color: "#243246" } },
        y: { title: { display: true, text: unit, color: "#9fb0c3" },
             ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
      },
    },
  });
}

// A ranking cell (one lead day or stratum) as text: the clear winner, or the tie.
function winnerText(w, labels) {
  const name = (id) => labels[id] || id;
  if (w.decisive) return `<strong>${name(w.model)}</strong>`;
  if (!w.runner_up) return `${name(w.model)} <span class="note">(only model)</span>`;
  return `Tie: ${w.tied.map(name).join(", ")}`;
}

// Per-lead-day ranking: who is most accurate at each horizon, by how much (± 1
// standard error, on shared forecasts), and how the blend compares.
function makeLeadTable(locVar, ver, unit) {
  const labels = Object.fromEntries(ver.models.map((m) => [m.id, m.label]));
  const blendByLead = new Map(((locVar.blend && locVar.blend.by_lead) || []).map((b) => [b.lead_day, b]));
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "fc verif";
  table.innerHTML =
    `<thead><tr><th>Lead</th><th>Most accurate</th><th>MAE</th><th>Ahead of next by</th>` +
    `<th>n</th><th>powWX blend</th></tr></thead>`;
  const tbody = document.createElement("tbody");
  for (const w of locVar.winners_by_lead) {
    const b = blendByLead.get(w.lead_day);
    const margin = w.margin != null ? `${w.margin.toFixed(2)} ± ${w.margin_se.toFixed(2)}` : "—";
    // Blend MAE, then its error relative to the winner on the same forecasts.
    // Gold where the blend clearly beats the best single model at this lead.
    const blendWins = b && b.decisive && b.improvement_pct > 0;
    const blend = b
      ? `${b.blend_mae.toFixed(2)} (${b.improvement_pct > 0 ? "−" : "+"}${Math.abs(b.improvement_pct)}% ± ${b.improvement_se}%` +
        (b.decisive ? ")" : ", tie)")
      : "—";
    const tr = document.createElement("tr");
    if (w.decisive) tr.className = "leader";
    tr.innerHTML =
      `<td>${w.lead_day === 0 ? "same day" : `day ${w.lead_day}`}</td>` +
      `<td>${winnerText(w, labels)}</td><td>${w.mae.toFixed(2)}</td><td>${margin}</td>` +
      `<td>${w.n.toLocaleString()}</td><td${blendWins ? ' class="blend-win"' : ""}>${blend}</td>`;
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  wrap.appendChild(table);
  const cap = document.createElement("div");
  cap.className = "note";
  cap.textContent = `MAE in ${unit}. "Most accurate" ranks the source models only. ` +
    "The powWX blend column gives its MAE and how its error compares with that lead's most " +
    "accurate model (− = lower; gold = clearly better). Margins compare forecasts made for the " +
    "same hours, ± 1 standard error; a tie means the gap is within 2 standard errors.";
  wrap.appendChild(cap);
  return wrap;
}

// Conditional verification: one line per model across a stratum's categories
// (season / observed-temp bin / time of day). Lines crossing = the ranking
// flips by condition. Computed day-ahead so models compare fairly.
function makeStratChart(canvas, sd, ver, unit) {
  const order = sd.order;
  const datasets = ver.models
    .filter((m) => sd.by_model[m.id])
    .map((m) => {
      const byS = new Map(sd.by_model[m.id].map((r) => [r.stratum, r.mae]));
      return {
        label: m.label, modelId: m.id,
        data: order.map((s) => (byS.has(s) ? byS.get(s) : null)),
        borderColor: MODEL_COLORS[m.id] || "#888",
        backgroundColor: MODEL_COLORS[m.id] || "#888",
        borderWidth: 1.6, tension: 0.2, pointRadius: 3, spanGaps: false,
      };
    });
  return new Chart(canvas, {
    type: "line",
    data: { labels: order, datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          titleFont: { size: 11 }, bodyFont: { size: 11 },
          filter: (i) => i.parsed.y !== null,
          callbacks: { label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(2)} ${unit}` },
        },
      },
      scales: {
        x: { ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
        y: { title: { display: true, text: `MAE (${unit})`, color: "#9fb0c3" },
             ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
      },
    },
  });
}

function renderStratTable(wrap, sd, ver, unit) {
  const labels = Object.fromEntries(ver.models.map((m) => [m.id, m.label]));
  // Highlight a column's winner only where it wins clearly (not a tie).
  const winner = {};
  sd.best.forEach((b) => { if (b.decisive) winner[b.stratum] = b.model; });
  wrap.innerHTML = "";
  const table = document.createElement("table");
  table.className = "fc verif strat";
  table.innerHTML =
    `<thead><tr><th>Model</th>${sd.order.map((s) => `<th>${s}</th>`).join("")}</tr></thead>`;
  const tbody = document.createElement("tbody");
  ver.models.filter((m) => sd.by_model[m.id]).forEach((m) => {
    const byS = new Map(sd.by_model[m.id].map((r) => [r.stratum, r.mae]));
    const cells = sd.order.map((s) => {
      const v = byS.has(s) ? byS.get(s).toFixed(2) : "—";
      const win = winner[s] === m.id ? ' class="win"' : "";
      return `<td${win}>${v}</td>`;
    }).join("");
    const dot = `<i class="mdot" style="background:${MODEL_COLORS[m.id] || "#888"}"></i>`;
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${dot}${labels[m.id] || m.id}</td>${cells}`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  const cap = document.createElement("div");
  cap.className = "note";
  cap.textContent = `MAE (${unit}); a column's winner is highlighted only where it leads by more ` +
    "than 2 standard errors · day-ahead forecasts";
  wrap.appendChild(cap);
}

function renderStrata(block, lv, ver, unit, legend, stratHolder) {
  const strata = lv.strata;
  if (!strata || !Object.keys(strata).length) return;
  const dims = Object.keys(strata);

  const sect = document.createElement("div");
  sect.className = "strata-section";
  sect.innerHTML =
    `<h3>Performance by condition <span class="unit">day-ahead</span></h3>` +
    `<p class="note">Does the best model change with the weather? Day-ahead error, so models with different horizons compare fairly.</p>`;

  const tabs = document.createElement("div");
  tabs.className = "strata-tabs";
  dims.forEach((d, i) => {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = strata[d].label; b.dataset.dim = d;
    if (i === 0) b.classList.add("active");
    tabs.appendChild(b);
  });
  sect.appendChild(tabs);

  const noteEl = document.createElement("div");
  noteEl.className = "strata-callout";
  const grid = document.createElement("div");
  grid.className = "strata-grid";
  const cwrap = document.createElement("div");
  cwrap.className = "chart-wrap strata-chart";
  const canvas = document.createElement("canvas");
  cwrap.appendChild(canvas);
  const tableWrap = document.createElement("div");
  tableWrap.className = "table-scroll";
  grid.append(cwrap, tableWrap);
  sect.append(noteEl, grid);
  block.appendChild(sect);

  const labels = Object.fromEntries(ver.models.map((m) => [m.id, m.label]));
  const offSet = () =>
    new Set([...legend.querySelectorAll(".leg.off")].map((s) => s.dataset.model));

  function draw(dim) {
    const sd = strata[dim];
    if (stratHolder.chart) stratHolder.chart.destroy();
    const chart = makeStratChart(canvas, sd, ver, unit);
    const off = offSet();
    chart.data.datasets.forEach((ds, i) => {
      if (off.has(ds.modelId)) chart.setDatasetVisibility(i, false);
    });
    chart.update();
    stratHolder.chart = chart;
    renderStratTable(tableWrap, sd, ver, unit);
    const wins = sd.best.map((b) => `${b.stratum}: ${winnerText(b, labels)}`).join(" · ");
    const clear = new Set(sd.best.filter((b) => b.decisive).map((b) => b.model));
    noteEl.innerHTML = (sd.flips
      ? `🔀 The most accurate model <strong>changes</strong> by ${sd.label.toLowerCase()}. `
      : clear.size === 1 && sd.best.every((b) => b.decisive)
        ? `One model leads clearly in every ${sd.label.toLowerCase()} bin. `
        : `No clear change of leader by ${sd.label.toLowerCase()}. `) + `Most accurate → ${wins}`;
  }
  tabs.querySelectorAll("button").forEach((b) => {
    b.onclick = () => {
      tabs.querySelectorAll("button").forEach((x) => x.classList.remove("active"));
      b.classList.add("active");
      draw(b.dataset.dim);
    };
  });
  draw(dims[0]);
}

function buildVerification(ver) {
  const intro = document.getElementById("verification-intro");
  const body = document.getElementById("verification-body");
  body.innerHTML = "";
  const locs = ver.locations || {};
  const ids = Object.keys(locs).sort(
    (a, b) => locs[b].elevation_m - locs[a].elevation_m);

  if (!ids.length) {
    intro.textContent = "No verifiable forecast/observation pairs yet — the record is still accruing.";
    return;
  }
  intro.textContent = ver.notes || "";

  const labels = Object.fromEntries(ver.models.map((m) => [m.id, m.label]));

  for (const lid of ids) {
    const loc = locs[lid];
    const block = document.createElement("div");
    block.className = "loc-block";

    for (const vkey of Object.keys(loc.variables)) {
      const lv = loc.variables[vkey];
      const unit = (ver.units && ver.units[vkey]) || "";
      const vlabel = (ver.var_labels && ver.var_labels[vkey]) || vkey;
      // Headline: the day-ahead ranking of the *source* models (the blend has its
      // own callout). Never a pooled all-lead ranking (review F2).
      const dayAhead = lv.winners_by_lead.find((w) => w.lead_day === 1);

      const head = document.createElement("div");
      head.className = "loc-head";
      head.innerHTML =
        `<h2>${loc.label}</h2><span class="elev">${loc.elevation_m} m</span>` +
        `<span class="role">${vlabel}</span>`;
      block.appendChild(head);

      // Winner callout + period/coverage.
      const callout = document.createElement("div");
      callout.className = "verif-callout";
      const dayWins = lv.winners_by_lead
        .map((w) => `${w.lead_day === 0 ? "same day" : `d${w.lead_day}`}: ` +
          (w.decisive ? (labels[w.model] || w.model) : w.runner_up ? "tie" : `${labels[w.model] || w.model} only`))
        .join(" · ");
      let headline = "";
      if (dayAhead && dayAhead.decisive) {
        headline = `Day-ahead, <strong>${labels[dayAhead.model] || dayAhead.model}</strong> is the most accurate here: ` +
          `MAE <strong>${dayAhead.mae.toFixed(2)} ${unit}</strong>, ` +
          `${dayAhead.margin.toFixed(2)} ± ${dayAhead.margin_se.toFixed(2)} ${unit} ahead of ` +
          `${labels[dayAhead.runner_up] || dayAhead.runner_up} (n ${dayAhead.n.toLocaleString()}).`;
      } else if (dayAhead) {
        headline = `Day-ahead, it's <strong>too close to call</strong>: ` +
          `${dayAhead.tied.map((id) => labels[id] || id).join(", ")} are within 2 standard errors ` +
          `of each other (best MAE ${dayAhead.mae.toFixed(2)} ${unit}, n ${dayAhead.n.toLocaleString()}).`;
      }
      callout.innerHTML =
        (headline ? `<p>${headline}</p>` : "") +
        `<p class="note">Most accurate by lead day → ${dayWins}</p>` +
        `<p class="note">${lv.n_pairs.toLocaleString()} forecast/observation pairs · ` +
        `${lv.period.start.slice(0, 10)} → ${lv.period.end.slice(0, 10)} · ` +
        `actuals: ${loc.obs_source}</p>`;
      block.appendChild(callout);

      // powWX blend callout — our composed forecast, judged out-of-sample.
      if (lv.blend) {
        const bd = lv.blend;
        const vs = bd.vs_best_by_lead;
        const verdict = !vs.decisive
          ? `is <strong>about as accurate as</strong> the best single model at each lead (difference ${vs.improvement_pct}% ± ${vs.improvement_se}%)`
          : vs.improvement_pct > 0
            ? `has <strong>${vs.improvement_pct}% ± ${vs.improvement_se}% lower error</strong> than the best single model at each lead`
            : `has <strong>${Math.abs(vs.improvement_pct)}% ± ${vs.improvement_se}% higher error</strong> than the best single model at each lead`;
        const bc = document.createElement("div");
        bc.className = "blend-callout";
        const band = (bd.band_coverage_oos != null)
          ? ` Its ${Math.round(bd.band_level * 100)}% range covers <strong>${Math.round(bd.band_coverage_oos * 100)}%</strong> of outcomes out-of-sample.`
          : "";
        bc.innerHTML =
          `<p>⚙️ <strong style="color:${MODEL_COLORS.powwx_blend}">powWX blend</strong> <em class="tag">experimental</em> — bias-corrected, skill-weighted consensus, judged <em>out-of-sample</em> — ${verdict}, ` +
          `compared on the same forecasts (n ${vs.n.toLocaleString()}). ` +
          `Its bias is ${bd.overall.bias >= 0 ? "+" : ""}${bd.overall.bias} ${unit}.${band}</p>`;
        block.appendChild(bc);
      }

      // Shared per-model legend (toggles both charts in this block).
      const legend = document.createElement("div");
      legend.className = "legend";
      legend.innerHTML = ver.models
        .filter((m) => lv.by_lead[m.id])
        .map((m) => `<span class="leg" data-model="${m.id}"><i style="background:${MODEL_COLORS[m.id] || "#888"}"></i>${m.label}</span>`)
        .join("");
      block.appendChild(legend);

      const grid = document.createElement("div");
      grid.className = "panel-grid";
      const charts = [];
      const stratHolder = { chart: null };
      for (const metric of ["mae", "bias"]) {
        const panel = document.createElement("div");
        panel.className = "panel";
        panel.innerHTML = `<h3>${METRIC_LABELS[metric]} <span class="unit">${unit}</span></h3>`;
        const cw = document.createElement("div");
        cw.className = "chart-wrap";
        const canvas = document.createElement("canvas");
        cw.appendChild(canvas);
        panel.appendChild(cw);
        grid.appendChild(panel);
        charts.push(makeMetricChart(canvas, lv, ver, metric, unit));
      }
      // Leaderboard table panel.
      const tpanel = document.createElement("div");
      tpanel.className = "panel";
      tpanel.innerHTML = `<h3>Most accurate by lead day</h3>`;
      tpanel.appendChild(makeLeadTable(lv, ver, unit));
      grid.appendChild(tpanel);
      block.appendChild(grid);

      renderStrata(block, lv, ver, unit, legend, stratHolder);

      legend.querySelectorAll(".leg").forEach((span) => {
        span.onclick = () => {
          const id = span.dataset.model;
          const visible = !span.classList.toggle("off");
          const all = stratHolder.chart ? [...charts, stratHolder.chart] : charts;
          for (const ch of all) {
            ch.data.datasets.forEach((ds, i) => {
              if (ds.modelId === id) ch.setDatasetVisibility(i, visible);
            });
            ch.update();
          }
        };
      });
    }
    body.appendChild(block);
  }
}

// ---- season in context (history) -------------------------------------------
// "Is this season unusual?" Reads history.json (scripts/build_history.py): per
// station, season charts on a fixed day-of-season axis (so, like verification,
// they stay OUT of the time-axis pan/zoom sync), the how-unusual chips, and
// snow milestones. Loaded only when the section scrolls near the screen.

const HIST_COLORS = {
  band90: "rgba(94,200,255,0.10)", band50: "rgba(94,200,255,0.22)",
  median: "#7d8ea0", past: "rgba(159,176,195,0.22)", prev: "#5ec8ff", avg: "#c9d4e0",
};
// Percentile classes for daily temperatures (upper bound exclusive), as in build_history.py.
const PCT_CLASSES = [[10, "very cold", "pc-vcold"], [33, "cooler", "pc-cool"],
  [67, "near normal", "pc-norm"], [90, "warmer", "pc-warm"], [101, "very warm", "pc-vwarm"]];
const histCharts = [];

function pctClass(p) {
  const hit = PCT_CLASSES.find(([below]) => p < below) || PCT_CLASSES[PCT_CLASSES.length - 1];
  return { label: hit[1], cls: hit[2] };
}
function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
// Day `pos` of a season axis starting on ISO date `start`, e.g. "Nov 14".
function axisDay(start, pos, opts) {
  const d = new Date(start + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + pos);
  return d.toLocaleDateString("en-CA", { timeZone: "UTC", month: "short", day: "numeric", ...(opts || {}) });
}
function fmtDay(iso, opts) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-CA", { timeZone: "UTC", ...opts });
}
function daysBetween(a, b) {
  return Math.round((new Date(b + "T12:00:00Z") - new Date(a + "T12:00:00Z")) / DAY);
}
const signed = (x, nd = 1) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(nd)}`;

// Dashed vertical line at today's position on a season chart.
function todayLinePlugin(pos) {
  return {
    id: "todayLine",
    afterDatasetsDraw(chart) {
      if (pos == null) return;
      const { ctx, chartArea: { top, bottom }, scales: { x } } = chart;
      const px = x.getPixelForValue(pos);
      ctx.save();
      ctx.strokeStyle = "#9fb0c3"; ctx.setLineDash([3, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(px, top); ctx.lineTo(px, bottom); ctx.stroke();
      ctx.fillStyle = "#9fb0c3"; ctx.font = "10px system-ui"; ctx.fillText("today", px + 3, top + 10);
      ctx.restore();
    },
  };
}

// One season chart: normal band (10-90 % light, 25-75 % dark, median dashed),
// past seasons (faint; last season highlighted), station average, this season
// (bold; dashed if modelled) and the forecast extension (gold, with its band).
// Every dataset spans the whole axis so the "index" tooltip lines up.
function makeSeasonChart(canvas, c, unit, todayPos) {
  const n = c.n_days, nd = unit === "°C" ? 1 : 0;
  const full = (vals) => vals.map((y, i) => ({ x: i, y }));
  const ds = [];
  const band = (key, label, extra) => ({ label, data: full(c.band[key]), histId: "band", isBand: true,
    borderWidth: 0, pointRadius: 0, tension: 0.2, spanGaps: false, ...extra });
  if (c.band) {
    ds.push(band("p90", "p90", { fill: false }),
      band("p10", "p10", { fill: "-1", backgroundColor: HIST_COLORS.band90 }),
      band("p75", "p75", { fill: false }),
      band("p25", "p25", { fill: "-1", backgroundColor: HIST_COLORS.band50 }));
    ds.push({ label: "Normal (median)", data: full(c.band.p50), histId: "band",
      borderColor: HIST_COLORS.median, borderWidth: 1, borderDash: [4, 3], pointRadius: 0, tension: 0.2 });
  }
  const past = [...(c.past || [])].sort((a, b) => a.year - b.year);
  const prev = past.length ? past[past.length - 1] : null;
  for (const p of past) {
    const isPrev = p === prev;
    ds.push({ label: isPrev ? `Last season (${p.label})` : p.label, data: full(p.values),
      histId: isPrev ? "prev" : "past", borderColor: isPrev ? HIST_COLORS.prev : HIST_COLORS.past,
      borderWidth: isPrev ? 1.5 : 1, pointRadius: 0, tension: 0.2, spanGaps: false, order: isPrev ? 2 : 4 });
  }
  if (c.average) {
    ds.push({ label: `Station average (${c.average.n_seasons} seasons)`, data: full(c.average.values),
      histId: "avg", borderColor: HIST_COLORS.avg, borderWidth: 1.5, borderDash: [1, 3],
      pointRadius: 0, tension: 0.2, spanGaps: false, order: 3 });
  }
  if (c.forecast) {
    const arr = (key) => {
      const a = new Array(n).fill(null);
      c.forecast.positions.forEach((p, i) => { a[p] = c.forecast[key][i]; });
      return full(a);
    };
    if (c.forecast.upper && c.forecast.lower) {
      ds.push({ label: "fc upper", data: arr("upper"), histId: "fc", isBand: true, borderWidth: 0,
        pointRadius: 0, fill: false, order: 5 });
      ds.push({ label: "fc lower", data: arr("lower"), histId: "fc", isBand: true, borderWidth: 0,
        pointRadius: 0, fill: "-1", backgroundColor: "rgba(255,204,51,0.15)", order: 5 });
    }
    ds.push({ label: "Next days (powWX blend)", data: arr("values"), histId: "fc",
      borderColor: MODEL_COLORS.powwx_blend, borderWidth: 2.5, borderDash: [2, 3],
      pointRadius: 0, tension: 0.2, order: -2 });
  }
  if (c.current) {
    const modelled = c.current.source === "modelled";
    ds.push({ label: `${c.complete ? "Latest" : "This"} season (${c.season})${modelled ? ", modelled" : ""}`, data: full(c.current.values),
      histId: "cur", borderColor: OBS_COLOR, borderWidth: 2.5, borderDash: modelled ? [6, 4] : undefined,
      pointRadius: 0, tension: 0.2, spanGaps: false, order: -1 });
  }

  // Ticks on the first of each month.
  const monthStarts = [];
  for (let i = 0; i < n; i++) if (axisDay(c.axis_start, i, { day: "numeric", month: undefined }) === "1") monthStarts.push(i);

  const chart = new Chart(canvas, {
    type: "line",
    data: { datasets: ds },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          titleFont: { size: 11 }, bodyFont: { size: 11 }, footerFont: { size: 10, weight: "normal" },
          filter: (i) => !i.dataset.isBand && i.dataset.histId !== "past" && i.parsed.y !== null,
          callbacks: {
            title: (items) => axisDay(c.axis_start, items[0].parsed.x),
            label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(nd)} ${unit}`,
            footer: (items) => {
              if (!c.band || !items.length) return "";
              const x = items[0].parsed.x, lo = c.band.p10[x], hi = c.band.p90[x];
              if (lo == null || hi == null) return "";
              const ns = c.band.n_seasons ? ` · ${c.band.n_seasons[x]} seasons` : "";
              return `Normal range (10–90 %): ${lo.toFixed(nd)} to ${hi.toFixed(nd)} ${unit}${ns}`;
            },
          },
        },
      },
      scales: {
        x: { type: "linear", min: 0, max: n - 1,
             afterBuildTicks: (axis) => { axis.ticks = monthStarts.map((v) => ({ value: v })); },
             ticks: { color: "#9fb0c3", maxRotation: 0, autoSkip: false,
                      callback: (v) => axisDay(c.axis_start, v, { day: undefined }) },
             grid: { color: "#243246" } },
        // Snow: at least 0-50 cm, so a few cm don't fill the chart.
        y: { title: { display: true, text: unit, color: "#9fb0c3" },
             ...(unit === "cm" ? { min: 0, suggestedMax: 50 } : {}),
             ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
      },
    },
    plugins: [todayLinePlugin(todayPos)],
  });
  histCharts.push(chart);
  return chart;
}

// Clickable legend for one season chart (toggles datasets by histId).
function seasonLegend(chart, c) {
  const items = [];
  if (c.current) items.push(["cur", OBS_COLOR, `${c.complete ? "Latest" : "This"} season (${c.season})${c.current.source === "modelled" ? " <em class=\"tag\">modelled</em>" : ""}`]);
  if (c.forecast) items.push(["fc", MODEL_COLORS.powwx_blend, "Next days (powWX blend)"]);
  const past = c.past || [];
  if (past.length) {
    const prev = [...past].sort((a, b) => a.year - b.year)[past.length - 1];
    items.push(["prev", HIST_COLORS.prev, `Last season (${prev.label})`]);
    if (past.length > 1) items.push(["past", "#7d8ea0", `Older seasons (${past.length - 1})`]);
  }
  if (c.average) items.push(["avg", HIST_COLORS.avg, "Station average"]);
  if (c.band) items.push(["band", "rgba(94,200,255,0.6)", "Normal range"]);
  const legend = document.createElement("div");
  legend.className = "legend";
  legend.innerHTML = items.map(([id, color, text]) =>
    `<span class="leg" data-hist="${id}"><i style="background:${color}"></i>${text}</span>`).join("");
  legend.querySelectorAll(".leg").forEach((span) => {
    span.onclick = () => {
      const visible = !span.classList.toggle("off");
      chart.data.datasets.forEach((d, i) => { if (d.histId === span.dataset.hist) chart.setDatasetVisibility(i, visible); });
      chart.update();
    };
  });
  return legend;
}

function todayPosition(h, c) {
  const p = daysBetween(c.season_start, h.today);
  return p >= 0 && p < c.n_days ? p : null;
}

function temperatureSummary(h, loc, c) {
  const s = c.summary, years = h.reference.years.join("–");
  const parts = [];
  if (s && s.percentile != null) {
    const cls = pctClass(s.percentile);
    parts.push(`<p>${c.complete ? "Over the whole season" : "So far this season"} (${s.n_days} days): mean <strong>${s.mean.toFixed(1)} °C</strong>, ` +
      `<strong>${signed(s.anomaly)} °C</strong> vs the ${years} normal: ${cls.label} ` +
      `(${ordinal(s.percentile)} percentile of ${s.n_ref} normal seasons on the same days).</p>`);
  } else if (s) {
    parts.push(`<p>${c.complete ? "Over the whole season" : "So far this season"} (${s.n_days} days): mean ${s.mean.toFixed(1)} °C.</p>`);
  } else {
    parts.push(`<p class="note">Not enough of this season yet for a summary (needs 7 days).</p>`);
  }
  if (s && s.rank != null) {
    const step = loc.adjustment && loc.adjustment.step;
    const caveat = step && step.lo < 0 === step.hi < 0
      ? ` Since ${fmtDay(step.cut, { month: "short", year: "numeric" })} this station reads about ` +
        `${Math.abs(step.step).toFixed(1)} °C (${Math.abs(step.hi).toFixed(1)}–${Math.abs(step.lo).toFixed(1)}) ` +
        `${step.step < 0 ? "cooler" : "warmer"} relative to the model than before, perhaps an equipment change, ` +
        "so ranks against older seasons are uncertain by about that much."
      : "";
    parts.push(`<p class="note">Station's own record: ${ordinal(s.rank)} warmest of ${s.n_seasons} seasons ` +
      `since ${s.station_since} on the same days.${caveat}</p>`);
  }
  const a = loc.adjustment;
  if (c.band) {
    const cov = a && a.coverage_daily;
    parts.push(`<p class="note">Band: ${years} normal, <em>modelled</em> (ERA5-Land, adjusted to this station; ` +
      `${c.band.n_years} seasons)${c.band.provisional ? ' <em class="tag">provisional</em>' : ""}.` +
      (cov ? ` Check on recent station days: ${Math.round(cov.below * 100)} % fell below and ` +
        `${Math.round(cov.above * 100)} % above its 10–90 % range (a fair band: 10 % each).` : "") +
      (c.band.provisional ? " Only a short station record to adjust with, so it may be off by about 1 °C." : "") + "</p>");
  }
  if (c.current && c.current.source === "modelled") {
    parts.push(`<p class="note">This station's own values are switched off here; its line is ERA5-Land, adjusted.</p>`);
  }
  return parts.join("");
}

function snowSummary(loc, c) {
  const s = c.summary, parts = [];
  if (s && s.depth_cm != null) {
    let txt = `Latest (${axisDay(c.axis_start, s.position)}): <strong>${s.depth_cm} cm</strong>`;
    if (s.rank != null) txt += `, ${ordinal(s.rank)} deepest of ${s.n_seasons} seasons for the date`;
    if (s.pct_of_median != null) txt += ` (${s.pct_of_median} % of the median)`;
    parts.push(`<p>${txt}.</p>`);
  } else if (s && s.rank != null) {
    parts.push(`<p>Latest: ${ordinal(s.rank)} deepest of ${s.n_seasons} seasons for the date.</p>`);
  } else if (!c.current) {
    parts.push(`<p class="note">No snow-depth readings yet this season.</p>`);
  }
  if (c.band) {
    parts.push(`<p class="note">Band: this station's measured seasons since ${c.band.since}, drawn where at ` +
      "least 8 seasons have data near the date (the tooltip gives the count).</p>");
  }
  if (c.note) parts.push(`<p class="note">${c.note}</p>`);
  return parts.join("");
}

function milestonesTable(m) {
  const fmt = (key, v) => (v == null ? "—" : key === "peak_cm" ? `${v} cm` : axisDay(m.axis_start, v));
  const rows = [["First 10 cm", "onset"], ["Peak depth", "peak_cm"], ["Peak date", "peak"],
    ["Melt-out (below 10 cm)", "meltout"]];
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const cur = m.current || {};
  wrap.innerHTML = `<table class="fc milestones"><thead><tr><th></th><th>This season</th><th>Median</th>` +
    `<th>Range</th><th>n</th></tr></thead><tbody>` +
    rows.map(([label, key]) => {
      const st = m.stats[key];
      const now = key === "onset" ? (cur.onset != null ? fmt(key, cur.onset) : "not yet") : "—";
      return `<tr><td>${label}</td><td>${now}</td><td>${st ? fmt(key, st.median) : "—"}</td>` +
        `<td>${st ? `${fmt(key, st.min)} – ${fmt(key, st.max)}` : "—"}</td><td>${st ? st.n : "—"}</td></tr>`;
    }).join("") + `</tbody></table>`;
  const cap = document.createElement("div");
  cap.className = "note";
  cap.textContent = "Seasons count only where the record can pin a date down (no gap over 14 days " +
    "before it; for the peak, December–April mostly covered), so n differs by row.";
  wrap.appendChild(cap);
  return wrap;
}

function unusualBlock(h, ids) {
  const box = document.createElement("div");
  box.className = "unusual";
  box.innerHTML = `<h3>How unusual are recent and coming days? <span class="unit note">daily mean temperature</span></h3>`;
  for (const lid of ids) {
    const u = h.locations[lid].unusual;
    if (!u) continue;
    const row = document.createElement("div");
    row.className = "row";
    row.innerHTML = `<div class="row-label">${h.locations[lid].label} <span class="note">${h.locations[lid].elevation_m} m</span></div>`;
    const chips = document.createElement("div");
    chips.className = "chips";
    let sepDone = false;
    for (const d of u.days) {
      if (d.kind === "forecast" && !sepDone) {
        const sep = document.createElement("span");
        sep.className = "chip-sep"; sep.textContent = "forecast ▸";
        chips.appendChild(sep); sepDone = true;
      }
      if (d.pct == null) continue;
      const cls = pctClass(d.pct);
      const fc = d.kind === "forecast";
      const range = fc && d.pct_lo != null ? `${Math.min(d.pct_lo, d.pct_hi)}–${ordinal(Math.max(d.pct_lo, d.pct_hi))}` : ordinal(d.pct);
      const chip = document.createElement("div");
      chip.className = `chip ${cls.cls}${fc ? " fc" : ""}`;
      chip.title = `${fmtDay(d.date, { weekday: "long", month: "short", day: "numeric" })}: ` +
        (d.value != null ? `${d.value.toFixed(1)} °C, ` : "") +
        `${cls.label} (${ordinal(d.pct)} percentile${fc && d.pct_lo != null ? `; 80 % range ${range} percentile` : ""}) ` +
        `${fc ? "forecast by the powWX blend" : "observed"}`;
      chip.innerHTML = `<span>${fmtDay(d.date, { weekday: "short", day: "numeric" })}</span>` +
        (d.value != null ? `<span class="chip-val">${Math.round(d.value) || 0}°</span>` : "") +
        `<span>${range}</span>`;
      chips.appendChild(chip);
    }
    row.appendChild(chips);
    box.appendChild(row);
  }
  const key = document.createElement("div");
  key.className = "chip-key";
  key.innerHTML = PCT_CLASSES.map(([, label, cls]) => `<span><i class="${cls}"></i>${label}</span>`).join("") +
    `<span>dashed = forecast</span>`;
  box.appendChild(key);
  const cap = document.createElement("p");
  cap.className = "note";
  cap.textContent = `Percentile of each day's mean temperature among the same dates (±${h.reference.window_days} ` +
    `days) in ${h.reference.years.join("–")}, from the modelled normal adjusted to each station. Forecast days use ` +
    "the powWX blend, with its 80 % range as a percentile range. Recent years run warmer than that normal, so " +
    "'warmer' turns up more than one day in three.";
  box.appendChild(cap);
  return box;
}

function chartPanel(title, unitLabel) {
  const panel = document.createElement("div");
  panel.className = "panel";
  panel.innerHTML = `<h3>${title} <span class="unit">${unitLabel}</span></h3>`;
  const cw = document.createElement("div");
  cw.className = "chart-wrap";
  const canvas = document.createElement("canvas");
  cw.appendChild(canvas);
  panel.appendChild(cw);
  return { panel, canvas, cw };
}

function renderHistoryTab(h, ids, tab, root) {
  histCharts.splice(0).forEach((ch) => ch.destroy());
  root.innerHTML = "";
  for (const lid of ids) {
    const loc = h.locations[lid];
    const key = tab === "snow" ? "snow_depth" : `temperature_${tab}`;
    const c = loc.charts[key];
    if (!c) continue;
    const head = document.createElement("div");
    head.className = "loc-head";
    head.innerHTML = `<h2>${loc.label}</h2><span class="elev">${loc.elevation_m} m</span><span class="role">${loc.role}</span>`;
    root.appendChild(head);
    const grid = document.createElement("div");
    grid.className = "hist-grid";
    const title = tab === "snow" ? `Snow depth, ${c.season}` : `${h.seasons[tab].label} ${c.season}`;
    const { panel, canvas, cw } = chartPanel(title, tab === "snow" ? "cm, daily median" : `°C, ${c.statistic}`);
    grid.appendChild(panel);
    const chart = makeSeasonChart(canvas, c, c.unit, todayPosition(h, c));
    panel.insertBefore(seasonLegend(chart, c), cw);
    const sum = document.createElement("div");
    sum.className = "hist-summary";
    sum.innerHTML = tab === "snow" ? snowSummary(loc, c) : temperatureSummary(h, loc, c);
    panel.appendChild(sum);
    if (tab === "snow" && loc.milestones && Object.keys(loc.milestones.stats).length) {
      const mp = document.createElement("div");
      mp.className = "panel";
      mp.innerHTML = "<h3>Snow milestones at this station</h3>";
      mp.appendChild(milestonesTable(loc.milestones));
      grid.appendChild(mp);
    }
    root.appendChild(grid);
  }
  const ms = h.modelled_snowpack;
  if (tab === "snow" && ms) {
    const head = document.createElement("div");
    head.className = "loc-head";
    head.innerHTML = `<h2>Modelled snowpack</h2><span class="role">ERA5-Land, both stations</span>`;
    root.appendChild(head);
    // Lines are this snow season (if it has data from 1 Dec yet) and last season.
    const lines = Object.entries(ms.lines).map(([y, l]) => ({ year: Number(y), ...l }));
    const cur = lines.find((l) => l.label === ms.season);
    const c = { n_days: ms.n_days, axis_start: ms.axis_start, season: ms.season,
      band: { ...ms.band, n_years: ms.n_years },
      current: cur ? { values: cur.values, source: "modelled-ratio" } : null,
      past: lines.filter((l) => l !== cur) };
    const grid = document.createElement("div");
    grid.className = "hist-grid";
    const { panel, canvas, cw } = chartPanel(`Snowpack vs normal, ${c.season}`, "% of the 1991–2020 median for the date");
    grid.appendChild(panel);
    const chart = makeSeasonChart(canvas, c, "%", null);
    panel.insertBefore(seasonLegend(chart, c), cw);
    const sum = document.createElement("div");
    sum.className = "hist-summary";
    sum.innerHTML = (ms.summary
      ? `<p>As of ${fmtDay(ms.summary.date, { month: "short", day: "numeric" })}: <strong>${ms.summary.pct_of_median} %</strong> of the normal median ` +
        `(${ordinal(ms.summary.percentile)} percentile).</p>`
      : `<p class="note">Shown from 1 December, when the snowpack is deep enough for a percentage to mean something.</p>`) +
      `<p class="note"><em class="tag">modelled</em> ERA5-Land, one ~10 km cell for both stations, data to ` +
      `${fmtDay(ms.data_to, { month: "short", day: "numeric" })} (about 5 days behind). It runs about 2.4× deeper ` +
      "than #58 measures, so only the comparison with its own normal is shown, never depths. It ranks seasons " +
      "reasonably well (correlation 0.8 with #58 on 1 April, 16 seasons). Late in spring, % of median exaggerates: " +
      "the median melts out first.</p>";
    panel.appendChild(sum);
    root.appendChild(grid);
  }
}

function buildHistory(h) {
  const intro = document.getElementById("history-intro");
  const body = document.getElementById("history-body");
  const ids = Object.keys(h.locations || {}).sort(
    (a, b) => h.locations[b].elevation_m - h.locations[a].elevation_m);
  if (!ids.length) { body.textContent = "Season history is unavailable in this build of the page."; return; }
  intro.textContent = h.notes || "";
  body.innerHTML = "";
  body.appendChild(unusualBlock(h, ids));

  const month = Number(h.today.slice(5, 7));
  const sample = h.locations[ids[0]].charts;
  const tabs = [["winter", `Winter ${sample.temperature_winter.season}`],
    ["summer", `Summer ${sample.temperature_summer.season}`], ["snow", `Snow depth ${sample.snow_depth.season}`]];
  let current = month >= 11 || month <= 4 ? "winter" : "summer";
  const bar = document.createElement("div");
  bar.className = "hist-tabs";
  const root = document.createElement("div");
  for (const [id, label] of tabs) {
    const b = document.createElement("button");
    b.type = "button"; b.textContent = label; b.dataset.tab = id;
    if (id === current) b.classList.add("active");
    b.onclick = () => {
      current = id;
      bar.querySelectorAll("button").forEach((x) => x.classList.toggle("active", x.dataset.tab === id));
      renderHistoryTab(h, ids, id, root);
    };
    bar.appendChild(b);
  }
  body.append(bar, root);
  renderHistoryTab(h, ids, current, root);

  const a = (url, text) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
  const credits = document.createElement("ul");
  credits.className = "credits note";
  credits.innerHTML = (h.credits || []).map((c) => `<li>${a(c.url, c.text)}</li>`).join("");
  body.appendChild(credits);
}

// Fetch history.json only when the section comes near the screen, so it never
// slows the first paint of the forecasts.
function lazyHistory() {
  const section = document.getElementById("history");
  const load = async () => {
    try {
      buildHistory(await loadJSON("history"));
    } catch (e) {
      console.warn("history skipped:", e.message);
      document.getElementById("history-body").textContent = "Season history is unavailable in this build of the page.";
    }
  };
  if (!("IntersectionObserver" in window)) { load(); return; }
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { io.disconnect(); load(); }
  }, { rootMargin: "600px" });
  io.observe(section);
}

// ---- webcams -------------------------------------------------------------

function buildWebcams(webcams) {
  const root = document.getElementById("webcam-cams");
  root.innerHTML = "";
  const base = webcams.public_base_url;
  const cams = webcams.cameras || {};
  for (const camId of Object.keys(cams)) {
    const cam = cams[camId];
    const frames = cam.frames || [];
    const el = document.createElement("div");
    el.className = "cam";
    el.innerHTML = `<h3>${cam.label}</h3>`;
    const img = document.createElement("img");
    img.alt = cam.label;
    el.appendChild(img);

    const controls = document.createElement("div");
    controls.className = "cam-controls";
    const older = document.createElement("button"); older.textContent = "◀ older";
    const newer = document.createElement("button"); newer.textContent = "newer ▶";
    const live = document.createElement("button"); live.textContent = "⟳ Live"; live.className = "cam-live";
    const slider = document.createElement("input");
    slider.type = "range"; slider.min = "0"; slider.max = String(Math.max(0, frames.length - 1)); slider.value = "0";
    controls.append(older, newer, slider, live);
    el.appendChild(controls);
    const caption = document.createElement("div");
    caption.className = "cam-caption";
    el.appendChild(caption);

    const hasHistory = frames.length > 0 && base;
    // `frames` is newest-first (idx 0 = newest). The slider runs oldest→newest
    // left→right (the intuitive timeline), so slider position = reversed idx.
    let idx = 0; // 0 = newest captured frame

    function showFrame() {
      const f = frames[idx];
      const n = frames.length;
      img.src = `${base}/${f.key}`;
      caption.textContent = `${fmtLocal(f.time)}  ·  ${n - idx} of ${n} (drag right → newer)`;
      slider.value = String((n - 1) - idx);
      older.disabled = idx >= n - 1;
      newer.disabled = idx <= 0;
    }
    function showLive() {
      img.src = `${cam.live_url}?t=${Date.now()}`;
      caption.innerHTML = `<span class="cam-live">● Live</span>`;
    }

    if (hasHistory) {
      older.onclick = () => { if (idx < frames.length - 1) { idx++; showFrame(); } };
      newer.onclick = () => { if (idx > 0) { idx--; showFrame(); } };
      slider.oninput = () => { idx = (frames.length - 1) - Number(slider.value); showFrame(); };
      live.onclick = showLive;
      showFrame();
    } else {
      older.disabled = newer.disabled = slider.disabled = true;
      live.onclick = showLive;
      showLive();
      const note = document.createElement("div");
      note.className = "note";
      note.textContent = "Frame history appears here once the webcam archive is public.";
      el.appendChild(note);
    }
    root.appendChild(el);
  }
}

// ---- chrome --------------------------------------------------------------

const AVALANCHE_FORECAST_URL = "https://avalanche.ca/map?lat=54.504308&lng=-128.966096";

function buildFooter(meta) {
  const f = document.getElementById("footer");
  const a = (url, text) => `<a href="${url}" target="_blank" rel="noopener">${text}</a>`;
  const links = (meta.links || []).map((l) => a(l.url, l.label)).join("");
  const credits = (meta.credits || []).map((c) => `<li>${a(c.url, c.text)}</li>`).join("");
  f.innerHTML =
    `<p class="disclaimer"><strong>Not a forecast.</strong> powWX is an experimental, volunteer-run ` +
    `tool; its data may be wrong, late or missing. Do not rely on it alone for decisions in ` +
    `avalanche terrain. Check the ${a(AVALANCHE_FORECAST_URL, "avalanche forecast at avalanche.ca")}. ` +
    `BC MoTI is not liable for the accuracy of its station data.</p>` +
    `<ul class="credits">${credits}</ul>` +
    `<p class="note">${meta.station_note || ""}</p>` +
    `<div class="links">Cross-check: ${links}</div>`;
}

// Status line: every time in BC time (labelled), with its age computed here in the
// browser so a page that stopped rebuilding shows as stale instead of looking current.
const STALE_HOURS = 12;   // same rule as the POW-O-METER station page
const HOUR = 3600000;

function fmtBC(iso) {
  return new Date(iso).toLocaleString("en-CA", {
    timeZone: "America/Vancouver", weekday: "short", month: "short", day: "numeric",
    hour: "numeric", minute: "2-digit" });
}
function fmtAge(ms) {
  const h = ms / HOUR;
  if (h < 1) return `${Math.max(0, Math.round(h * 60))} min ago`;
  if (h < 48) return `${Math.round(h)} h ago`;
  return `${Math.round(h / 24)} days ago`;
}
function lastObsTime(obs, lid) {
  const latest = Object.values(obs[lid] || {})
    .map((s) => s.times[s.times.length - 1]).filter(Boolean).sort();
  return latest.length ? latest[latest.length - 1] : null;
}

function setStatus(fc, obs, meta, locLabels) {
  const now = Date.now();
  const items = [];
  const add = (label, iso) => {
    if (!iso) { items.push({ text: `${label}: no recent data`, stale: true }); return; }
    const age = now - new Date(iso).getTime();
    items.push({ text: `${label} ${fmtBC(iso)} (${fmtAge(age)})`, stale: age > STALE_HOURS * HOUR });
  };
  add("forecast issued", meta.latest_run_issued_at);
  for (const lid of ["pow_o_meter", "station_58"]) {
    add(`${(locLabels[lid] && locLabels[lid].label) || lid} obs to`, lastObsTime(obs, lid));
  }
  add("page built", meta.generated_at);

  const el = document.getElementById("status");
  el.replaceChildren();
  items.forEach((it, i) => {
    if (i) el.append("  ·  ");
    const span = document.createElement("span");
    span.textContent = it.text;
    if (it.stale) span.className = "stale";
    el.append(span);
  });
  el.append("  ·  BC time");
  const stale = items.filter((it) => it.stale).length;
  document.querySelector("header").classList.toggle("is-stale", stale > 0);
  if (stale) {
    const warn = document.createElement("strong");
    warn.className = "stale-warning";
    warn.textContent = `⚠ Some data are more than ${STALE_HOURS} h old. Check the times before relying on this page.`;
    el.prepend(warn, document.createElement("br"));
  }
}

function wireToggle() {
  const bc = document.getElementById("btn-charts");
  const bt = document.getElementById("btn-tables");
  bc.onclick = () => { document.body.className = "view-charts"; bc.classList.add("active"); bt.classList.remove("active"); };
  bt.onclick = () => { document.body.className = "view-tables"; bt.classList.add("active"); bc.classList.remove("active"); };
}

async function main() {
  try {
    const [fc, obs, webcams, meta] = await Promise.all(
      ["forecast", "observations", "webcams", "meta"].map(loadJSON));
    // Blend and freezing-level estimate are optional — tolerate their absence.
    let blend = null, flEst = null;
    try { blend = await loadJSON("blend"); } catch (e) { console.warn("blend skipped:", e.message); }
    try { flEst = await loadJSON("freezing_level"); } catch (e) { console.warn("freezing_level skipped:", e.message); }
    buildForecast(fc, obs, blend, flEst);
    lazyHistory();
    // Verification is optional: tolerate its absence so the rest of the viewer
    // still renders if verification.json hasn't been generated yet.
    try {
      buildVerification(await loadJSON("verification"));
    } catch (e) {
      console.warn("verification panel skipped:", e.message);
      const intro = document.getElementById("verification-intro");
      if (intro) intro.textContent = "Verification is unavailable in this build of the page.";
    }
    buildWebcams(webcams);
    buildFooter(meta);
    setStatus(fc, obs, meta, fc.locations || {});
    // Keep the ages honest for a page left open.
    setInterval(() => setStatus(fc, obs, meta, fc.locations || {}), 60000);
    wireToggle();
    buildRangeControls();
    applyHashRange();
    window.addEventListener("hashchange", applyHashRange);
  } catch (e) {
    document.getElementById("status").textContent = `Failed to load data: ${e.message}`;
    console.error(e);
  }
}

main();
