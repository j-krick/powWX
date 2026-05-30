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
};
const OBS_COLOR = "#ffffff";

// Which variables get a chart/table panel, in display order, with the daily
// table aggregation that makes sense for each.
const CHART_VARS = [
  { key: "temperature_2m", title: "Temperature", agg: "minmax" },
  { key: "wind_speed_10m", title: "Wind speed", agg: "max" },
  { key: "precipitation", title: "Precipitation", agg: "sum" },
  { key: "snowfall", title: "Snowfall", agg: "sum" },
  { key: "snow_depth", title: "Snow depth", agg: "max" },
  { key: "relative_humidity_2m", title: "Humidity", agg: "mean" },
];

const charts = [];

async function loadJSON(name) {
  const r = await fetch(`data/${name}.json`, { cache: "no-store" });
  if (!r.ok) throw new Error(`${name}.json: ${r.status}`);
  return r.json();
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
  return "—";
}

// ---- forecast section ----------------------------------------------------

function makeChart(canvas, loc, vcfg, fc, obs) {
  const node = fc.forecast[loc][vcfg.key];
  const datasets = fc.models
    .filter((m) => node.series[m.id])
    .map((m) => ({
      label: m.label,
      data: node.times.map((t, i) => ({ x: t, y: node.series[m.id][i] })),
      borderColor: MODEL_COLORS[m.id] || "#888",
      backgroundColor: MODEL_COLORS[m.id] || "#888",
      borderWidth: 1.5, pointRadius: 0, tension: 0.25, spanGaps: false,
    }));
  const ov = obs[loc] && obs[loc][vcfg.key];
  if (ov) {
    datasets.push({
      label: "Observed", data: ov.times.map((t, i) => ({ x: t, y: ov.values[i] })),
      borderColor: OBS_COLOR, backgroundColor: OBS_COLOR,
      borderWidth: 2, pointRadius: 1.6, tension: 0.2, spanGaps: false, order: -1,
    });
  }
  charts.push(new Chart(canvas, {
    type: "line",
    data: { datasets },
    options: {
      responsive: true, maintainAspectRatio: false, animation: false,
      interaction: { mode: "index", intersect: false },
      plugins: { legend: { display: false }, tooltip: { titleFont: { size: 11 }, bodyFont: { size: 11 } } },
      scales: {
        x: { type: "time", time: { tooltipFormat: "EEE MMM d, HH:mm" },
             ticks: { color: "#9fb0c3", maxRotation: 0, autoSkipPadding: 20 }, grid: { color: "#243246" } },
        y: { title: { display: true, text: fc.units[vcfg.key] || "", color: "#9fb0c3" },
             ticks: { color: "#9fb0c3" }, grid: { color: "#243246" } },
      },
    },
  }));
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

function buildForecast(fc, obs) {
  const root = document.getElementById("forecast");
  root.innerHTML = "";
  const locIds = Object.keys(fc.locations).sort(
    (a, b) => fc.locations[b].elevation_m - fc.locations[a].elevation_m);

  for (const loc of locIds) {
    const meta = fc.locations[loc];
    const block = document.createElement("div");
    block.className = "loc-block";
    block.innerHTML =
      `<div class="loc-head"><h2>${meta.label}</h2>` +
      `<span class="elev">${meta.elevation_m} m</span>` +
      `<span class="role">${meta.role}</span></div>`;

    // shared model legend (+ observed if this location has obs)
    const legend = document.createElement("div");
    legend.className = "legend";
    legend.innerHTML = fc.models.map((m) =>
      `<span><i style="background:${MODEL_COLORS[m.id] || "#888"}"></i>${m.label}</span>`).join("");
    if (obs[loc]) legend.innerHTML += `<span><i style="background:${OBS_COLOR}"></i>Observed</span>`;
    block.appendChild(legend);

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
      grid.appendChild(panel);
      makeChart(canvas, loc, vcfg, fc, obs);
    }
    block.appendChild(grid);
    root.appendChild(block);
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
    let idx = 0; // 0 = newest captured frame

    function showFrame() {
      const f = frames[idx];
      img.src = `${base}/${f.key}`;
      caption.textContent = `${fmtLocal(f.time)}  (${idx + 1} of ${frames.length}, newest first)`;
      slider.value = String(idx);
      older.disabled = idx >= frames.length - 1;
      newer.disabled = idx <= 0;
    }
    function showLive() {
      img.src = `${cam.live_url}?t=${Date.now()}`;
      caption.innerHTML = `<span class="cam-live">● Live</span>`;
    }

    if (hasHistory) {
      older.onclick = () => { if (idx < frames.length - 1) { idx++; showFrame(); } };
      newer.onclick = () => { if (idx > 0) { idx--; showFrame(); } };
      slider.oninput = () => { idx = Number(slider.value); showFrame(); };
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
    buildForecast(fc, obs);
    buildWebcams(webcams);
    buildFooter(meta);
    setStatus(fc, obs, meta);
    wireToggle();
  } catch (e) {
    document.getElementById("status").textContent = `Failed to load data: ${e.message}`;
    console.error(e);
  }
}

main();
