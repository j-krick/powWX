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
  // Observed freezing-level estimate from the two stations (interp regime only):
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
    if (obs[loc]) legend.innerHTML += `<span class="leg" data-model="__observed__"><i style="background:${OBS_COLOR}"></i>Observed</span>`;
    if (blendLoc) legend.innerHTML += `<span class="leg blend-leg" data-model="powwx_blend"><i style="background:${MODEL_COLORS.powwx_blend}"></i>powWX blend</span>`;
    if (fle && fc.forecast[loc] && fc.forecast[loc].freezing_level_height) legend.innerHTML += `<span class="leg" data-model="__flest__"><i style="background:#ffffff"></i>Freezing level (obs. est.)</span>`;
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
        cap.innerHTML = "White = freezing level estimated from the two stations "
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
  for (let d = 1; d <= maxLead; d++) xs.push(d);

  const datasets = ver.models
    .filter((m) => locVar.by_lead[m.id])
    .map((m) => {
      const byDay = new Map(locVar.by_lead[m.id].map((r) => [r.lead_day, r[metric]]));
      return {
        label: m.label, modelId: m.id,
        data: xs.map((d) => ({ x: d, y: byDay.has(d) ? byDay.get(d) : null })),
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
      data: [{ x: 1, y: 0 }, { x: maxLead, y: 0 }],
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
            title: (items) => `Lead day ${items[0].parsed.x}`,
            label: (c) => `${c.dataset.label}: ${c.parsed.y.toFixed(2)} ${unit}`,
          },
        },
      },
      scales: {
        x: { type: "linear", min: 1, max: maxLead,
             title: { display: true, text: "Lead time (days ahead)", color: "#9fb0c3" },
             ticks: { color: "#9fb0c3", stepSize: 1, precision: 0 }, grid: { color: "#243246" } },
        y: { title: { display: true, text: unit, color: "#9fb0c3" },
             ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
      },
    },
  });
}

function makeOverallTable(locVar, ver, unit) {
  const labels = Object.fromEntries(ver.models.map((m) => [m.id, m.label]));
  const wrap = document.createElement("div");
  wrap.className = "table-scroll";
  const table = document.createElement("table");
  table.className = "fc verif";
  table.innerHTML =
    `<thead><tr><th>Model</th><th>MAE</th><th>Bias</th><th>RMSE</th><th>n</th></tr></thead>`;
  const tbody = document.createElement("tbody");
  locVar.overall.forEach((r, i) => {
    const tr = document.createElement("tr");
    const cls = [];
    if (i === 0) cls.push("leader"); // lowest MAE
    if (r.model === "powwx_blend") cls.push("blend-row");
    tr.className = cls.join(" ");
    const dot = `<i class="mdot" style="background:${MODEL_COLORS[r.model] || "#888"}"></i>`;
    tr.innerHTML =
      `<td>${dot}${labels[r.model] || r.model}${i === 0 ? " 🏆" : ""}</td>` +
      `<td>${r.mae.toFixed(2)}</td><td>${r.bias.toFixed(2)}</td>` +
      `<td>${r.rmse.toFixed(2)}</td><td>${r.n.toLocaleString()}</td>`;
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  wrap.appendChild(table);
  const cap = document.createElement("div");
  cap.className = "note";
  cap.textContent = `pooled across all lead days · values in ${unit} · ranked by MAE`;
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
  const winner = {};
  sd.best.forEach((b) => { winner[b.stratum] = b.model; });
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
  cap.textContent = `MAE (${unit}); lowest per column highlighted · day-ahead forecasts`;
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
    const wins = sd.best.map((b) => `${b.stratum}: <strong>${labels[b.model] || b.model}</strong>`).join(" · ");
    noteEl.innerHTML = (sd.flips
      ? `🔀 The most accurate model <strong>changes</strong> by ${sd.label.toLowerCase()}. `
      : `One model leads across every ${sd.label.toLowerCase()} bin. `) + `Winner → ${wins}`;
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
      // Headline names the best *source* model (the blend has its own callout).
      const best = lv.overall.find((r) => r.model !== "powwx_blend") || lv.overall[0];

      const head = document.createElement("div");
      head.className = "loc-head";
      head.innerHTML =
        `<h2>${loc.label}</h2><span class="elev">${loc.elevation_m} m</span>` +
        `<span class="role">${vlabel}</span>`;
      block.appendChild(head);

      // Winner callout + period/coverage.
      const callout = document.createElement("div");
      callout.className = "verif-callout";
      const dayWins = lv.best_by_lead
        .map((b) => `d${b.lead_day}: ${labels[b.model] || b.model}`).join(" · ");
      callout.innerHTML =
        `<p>🏆 <strong>${labels[best.model] || best.model}</strong> has the lowest error here — ` +
        `MAE <strong>${best.mae.toFixed(2)} ${unit}</strong> over ${best.n.toLocaleString()} matched hours.</p>` +
        `<p class="note">Best by lead day → ${dayWins}</p>` +
        `<p class="note">${lv.n_pairs.toLocaleString()} forecast/observation pairs · ` +
        `${lv.period.start.slice(0, 10)} → ${lv.period.end.slice(0, 10)} · ` +
        `actuals: ${loc.obs_source}</p>`;
      block.appendChild(callout);

      // powWX blend callout — our composed forecast, judged out-of-sample.
      if (lv.blend) {
        const bd = lv.blend;
        const rawLabel = labels[bd.best_raw_model] || bd.best_raw_model;
        const verdict = bd.beats_best_raw
          ? `<strong>beats</strong> the best single model (${rawLabel}) by <strong>${bd.improvement_pct}%</strong> overall`
          : `is within <strong>${Math.abs(bd.improvement_pct)}%</strong> of the best single model (${rawLabel}) overall — and leads through the mid-range (see the chart)`;
        const bc = document.createElement("div");
        bc.className = "blend-callout";
        const band = (bd.band_coverage_oos != null)
          ? ` Its ${Math.round(bd.band_level * 100)}% range covers <strong>${Math.round(bd.band_coverage_oos * 100)}%</strong> of outcomes out-of-sample.`
          : "";
        bc.innerHTML =
          `<p>⚙️ <strong style="color:${MODEL_COLORS.powwx_blend}">powWX blend</strong> — bias-corrected, skill-weighted consensus, judged <em>out-of-sample</em> — ${verdict}. ` +
          `MAE <strong>${bd.overall.mae} ${unit}</strong> (bias ${bd.overall.bias >= 0 ? "+" : ""}${bd.overall.bias}).${band}</p>`;
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
      tpanel.innerHTML = `<h3>Leaderboard <span class="unit">all lead days</span></h3>`;
      tpanel.appendChild(makeOverallTable(lv, ver, unit));
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

function buildFooter(meta) {
  const f = document.getElementById("footer");
  const links = (meta.links || []).map((l) => `<a href="${l.url}" target="_blank" rel="noopener">${l.label}</a>`).join("");
  f.innerHTML =
    `<div class="links">${links}</div>` +
    `<p>${meta.attribution || ""}</p>` +
    `<p class="note">${meta.station_note || ""}</p>`;
}

function setStatus(fc, obs, meta) {
  const parts = [];
  if (meta.latest_run_issued_at) parts.push(`forecast issued ${fmtLocal(meta.latest_run_issued_at)}`);
  const o58 = obs.station_58 && obs.station_58.temperature_2m;
  if (o58 && o58.times.length) parts.push(`obs to ${fmtLocal(o58.times[o58.times.length - 1])}`);
  if (meta.generated_at) parts.push(`page built ${fmtLocal(meta.generated_at)}`);
  document.getElementById("status").textContent = parts.join("  ·  ");
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
    // Verification is optional: tolerate its absence so the rest of the viewer
    // still renders if verification.json hasn't been generated yet.
    try {
      buildVerification(await loadJSON("verification"));
    } catch (e) {
      console.warn("verification panel skipped:", e.message);
      const sec = document.getElementById("verification");
      if (sec) sec.style.display = "none";
    }
    buildWebcams(webcams);
    buildFooter(meta);
    setStatus(fc, obs, meta);
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
