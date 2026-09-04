/* BicycleLane opportunity explorer.
   - Client-side weighting & ranking: composite = weight[detector] * score_norm.
   - i18n (FR/EN/ES): the UI language is chosen by the user.
   - Area of interest: draw a rectangle; the analysis runs only inside its bbox. */

// ------------------------------------------------------------------ i18n
const I18N = {
  en: {
    subtitle: "opportunity explorer", analyze: "Analyze", cityPh: "City, Country",
    criteria: "Criteria & weights",
    hint: "Toggle a criterion and drag its weight (0–1). The ranking and the map update live. Draw an area of interest to restrict the analysis, and click a demand–supply zone to reveal the proposed lanes inside it.",
    weight: "weight", showTop: "Show top", ranked: "Ranked opportunities",
    drawArea: "◻ Draw area of interest", drawing: "Draw a rectangle on the map…",
    clearArea: "✕ Clear area", areaSet: "Area of interest set — Analyze to explore it",
    extract: (c) => `Analyzing “${c}” — extracting OSM networks…`,
    run: (c) => `Analyzing “${c}” — running detectors…`,
    shownOf: (s, a) => `(${s} shown of ${a} active)`,
    proposedLanes: (n) => `<b>${n} proposed lane(s)</b> in this zone — click the zone to draw them`,
    proposedLane: "Proposed lane", composite: "composite", normScore: "normalised score", raw: "raw",
    showPriority: "Priority layer (composite)", priority: "priority", contributions: "contributions",
    detectors: { D1: "Missing links", D2: "Route continuity", D3: "Network resilience",
      D4: "Coverage / density",
      D5: "Demand–supply mismatch", D6: "Accessibility & equity", D7: "Safety / crashes", D8: "Comfort / experience", D9: "Intermodality / stations", D10: "Relief & green corridors", D11: "Generators / schools", D12: "Tourism & leisure", D13: "Directness / detour", D14: "Modal shift (short car trips)" },
  },
  fr: {
    subtitle: "explorateur d'opportunités", analyze: "Analyser", cityPh: "Ville, Pays",
    criteria: "Critères & poids",
    hint: "Activez un critère et réglez son poids (0–1). Le classement et la carte se mettent à jour en direct. Dessinez une zone d'intérêt pour y limiter l'analyse, et cliquez une zone demande–offre pour tracer les pistes proposées.",
    weight: "poids", showTop: "Afficher top", ranked: "Opportunités classées",
    drawArea: "◻ Dessiner une zone d'intérêt", drawing: "Dessinez un rectangle sur la carte…",
    clearArea: "✕ Effacer la zone", areaSet: "Zone d'intérêt définie — cliquez Analyser",
    extract: (c) => `Analyse de « ${c} » — extraction des réseaux OSM…`,
    run: (c) => `Analyse de « ${c} » — détecteurs en cours…`,
    shownOf: (s, a) => `(${s} affichées sur ${a} actives)`,
    proposedLanes: (n) => `<b>${n} piste(s) proposée(s)</b> dans cette zone — cliquez la zone pour les tracer`,
    proposedLane: "Piste proposée", composite: "composite", normScore: "score normalisé", raw: "brut",
    showPriority: "Couche priorité (composite)", priority: "priorité", contributions: "contributions",
    detectors: { D1: "Chaînons manquants", D2: "Continuité d'itinéraire", D3: "Résilience du réseau",
      D4: "Couverture / densité",
      D5: "Décalage demande–offre", D6: "Accessibilité & équité", D7: "Sécurité / accidents", D8: "Confort / expérience", D9: "Intermodalité / gares", D10: "Relief & corridors verts", D11: "Générateurs / écoles", D12: "Tourisme & loisir", D13: "Directness / détour", D14: "Report modal (trajets voiture courts)" },
  },
  es: {
    subtitle: "explorador de oportunidades", analyze: "Analizar", cityPh: "Ciudad, País",
    criteria: "Criterios y pesos",
    hint: "Active un criterio y ajuste su peso (0–1). La clasificación y el mapa se actualizan al instante. Dibuje una zona de interés para limitar el análisis y haga clic en una zona demanda–oferta para ver los carriles propuestos.",
    weight: "peso", showTop: "Mostrar top", ranked: "Oportunidades clasificadas",
    drawArea: "◻ Dibujar zona de interés", drawing: "Dibuje un rectángulo en el mapa…",
    clearArea: "✕ Borrar zona", areaSet: "Zona de interés definida — pulse Analizar",
    extract: (c) => `Analizando «${c}» — extrayendo redes OSM…`,
    run: (c) => `Analizando «${c}» — ejecutando detectores…`,
    shownOf: (s, a) => `(${s} mostradas de ${a} activas)`,
    proposedLanes: (n) => `<b>${n} carril(es) propuesto(s)</b> en esta zona — haga clic para dibujarlos`,
    proposedLane: "Carril propuesto", composite: "compuesto", normScore: "puntuación normalizada", raw: "bruto",
    showPriority: "Capa de prioridad (compuesto)", priority: "prioridad", contributions: "contribuciones",
    detectors: { D1: "Enlaces faltantes", D2: "Continuidad de ruta", D3: "Resiliencia de la red",
      D4: "Cobertura / densidad",
      D5: "Desajuste demanda–oferta", D6: "Accesibilidad y equidad", D7: "Seguridad / accidentes", D8: "Confort / experiencia", D9: "Intermodalidad / estaciones", D10: "Relieve y corredores verdes", D11: "Generadores / escuelas", D12: "Turismo y ocio", D13: "Franqueza / rodeo", D14: "Cambio modal (trayectos cortos en coche)" },
  },
};

const state = { data: null, active: {}, weights: {}, topK: 50, colorOf: {},
                current: [], lang: "fr", bbox: null,
                showPriority: false, compositeCriteria: [] };

const $ = (id) => document.getElementById(id);
const statusEl = () => $("status");

function t(key, ...args) {
  const dict = I18N[state.lang] || I18N.en;
  const v = key in dict ? dict[key] : I18N.en[key];
  return typeof v === "function" ? v(...args) : (v == null ? key : v);
}
function detLabel(key) {
  const d = (I18N[state.lang] || I18N.en).detectors;
  return (d && d[key]) || key;
}

function applyI18n() {
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => {
    el.placeholder = t(el.dataset.i18nPh);
  });
  // dynamic bits
  document.querySelectorAll("#criteria .criterion").forEach((el) => {
    const k = el.querySelector(".chk")?.dataset.k;
    if (k) el.querySelector(".name").textContent = `${k} · ${detLabel(k)}`;
    const wl = el.querySelector(".wlabel span");
    if (wl) wl.textContent = t("weight");
  });
  if ($("clear-area") && !$("clear-area").hidden) statusEl().textContent = t("areaSet");
}

// ------------------------------------------------------------------ map
const map = L.map("map", { zoomControl: true }).setView([43.53, 5.45], 13);
L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
  attribution: "&copy; OpenStreetMap &copy; CARTO", maxZoom: 20,
}).addTo(map);

const netLayer = L.layerGroup().addTo(map);
const priorityLayer = L.layerGroup().addTo(map);  // composite "Priority" cells (below opportunities)
const oppLayer = L.layerGroup().addTo(map);
const segLayer = L.layerGroup().addTo(map);   // D5 drill-down
const aoiLayer = L.layerGroup().addTo(map);    // area-of-interest rectangle

// -------------------- area of interest (two-click rectangle) --------------
// Two clicks (opposite corners) is used rather than click-drag: dragging fights
// Leaflet's map panning and is unreliable, whereas plain clicks always work.
let drawMode = false, corner1 = null, rect = null;

$("draw-area").addEventListener("click", () => {
  drawMode ? exitDraw() : enterDraw();
});
$("clear-area").addEventListener("click", clearArea);

function enterDraw() {
  drawMode = true;
  corner1 = null;
  $("map").style.cursor = "crosshair";
  $("draw-area").classList.add("active");
  setBusy(false, t("drawing"));
}
function exitDraw() {
  drawMode = false;
  corner1 = null;
  $("map").style.cursor = "";
  $("draw-area").classList.remove("active");
}
function clearArea() {
  state.bbox = null;
  aoiLayer.clearLayers();
  rect = null;
  $("clear-area").hidden = true;
  setBusy(false, "");
}

map.on("click", (e) => {
  if (!drawMode) return;
  if (!corner1) {
    // first corner
    corner1 = e.latlng;
    aoiLayer.clearLayers();
    rect = L.rectangle([corner1, corner1],
      { color: "#12324f", weight: 2, dashArray: "5", fillOpacity: 0.06 }).addTo(aoiLayer);
    return;
  }
  // second corner -> finalise
  const b = L.latLngBounds(corner1, e.latlng);
  exitDraw();
  if (b.getNorth() - b.getSouth() < 1e-4 || b.getEast() - b.getWest() < 1e-4) {
    aoiLayer.clearLayers();
    return;
  }
  rect.setBounds(b);
  state.bbox = [b.getWest(), b.getSouth(), b.getEast(), b.getNorth()];
  $("clear-area").hidden = false;
  setBusy(false, t("areaSet"));
});
map.on("mousemove", (e) => {
  if (drawMode && corner1 && rect) rect.setBounds(L.latLngBounds(corner1, e.latlng));
});

// ------------------------------------------------------------- analyze
$("city-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const city = $("city").value.trim();
  if (!city) return;
  let q = `city=${encodeURIComponent(city)}`;
  if (state.bbox) q += `&bbox=${state.bbox.map((n) => n.toFixed(6)).join(",")}`;
  setBusy(true, t("extract", city));
  try {
    const started = await fetch(`/api/analyze?${q}`, { method: "POST" });
    if (!started.ok) throw new Error((await started.json()).detail || started.statusText);
    const { job_id } = await started.json();

    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      const st = await (await fetch(`/api/jobs/${job_id}`)).json();
      if (st.status === "done") break;
      if (st.status === "error") throw new Error(st.error || "analysis failed");
      setBusy(true, t("run", city));
    }

    const r = await fetch(`/api/opportunities?${q}`);
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    state.data = await r.json();
    onData();
    setBusy(false, "");
  } catch (err) {
    setBusy(false, err.message, true);
  }
});

function setBusy(busy, msg, isErr) {
  $("go").disabled = busy;
  statusEl().textContent = msg || "";
  statusEl().classList.toggle("error", !!isErr);
}

// language selector
$("lang").value = state.lang;
$("lang").addEventListener("change", (e) => {
  state.lang = e.target.value;
  applyI18n();
  if (state.data) recompute();   // rebuild popups/list in the new language
});

// ----------------------------------------------------------------- on data
function onData() {
  const d = state.data;
  state.colorOf = {};
  d.detectors.forEach((det) => (state.colorOf[det.key] = det.color));

  segLayer.clearLayers();
  netLayer.clearLayers();
  L.geoJSON(d.network, { style: { color: "#3186cc", weight: 2, opacity: 0.5 } }).addTo(netLayer);

  priorityLayer.clearLayers();
  state.compositeCriteria = (d.composite && d.composite.meta && d.composite.meta.criteria) || [];

  buildCriteria(d.detectors);
  $("controls").hidden = false;
  $("results").hidden = false;

  const all = L.geoJSON(d.features).getBounds();
  if (all.isValid()) map.fitBounds(all.pad(0.05));
  else if (state.bbox) map.fitBounds([[state.bbox[1], state.bbox[0]], [state.bbox[3], state.bbox[2]]]);

  recompute();
}

function buildCriteria(detectors) {
  const box = $("criteria");
  box.innerHTML = "";
  detectors.forEach((det) => {
    state.active[det.key] = det.count > 0;
    state.weights[det.key] = 1.0;
    const el = document.createElement("div");
    el.className = "criterion";
    el.innerHTML = `
      <div class="row">
        <input type="checkbox" ${det.count ? "checked" : "disabled"} data-k="${det.key}" class="chk" />
        <span class="dot" style="background:${det.color}"></span>
        <span class="name">${det.key} · ${detLabel(det.key)}</span>
        <span class="count">${det.shown}/${det.count}</span>
      </div>
      <input type="range" class="w" data-k="${det.key}" min="0" max="1" step="0.05" value="1" ${det.count ? "" : "disabled"} />
      <div class="wlabel"><span>${t("weight")}</span><span class="wval" data-k="${det.key}">1.00</span></div>`;
    box.appendChild(el);
    if (!det.count) el.classList.add("off");
  });

  box.querySelectorAll(".chk").forEach((c) =>
    c.addEventListener("change", (e) => {
      state.active[e.target.dataset.k] = e.target.checked;
      e.target.closest(".criterion").classList.toggle("off", !e.target.checked);
      recompute();
    }));
  box.querySelectorAll(".w").forEach((s) =>
    s.addEventListener("input", (e) => {
      const k = e.target.dataset.k;
      state.weights[k] = parseFloat(e.target.value);
      box.querySelector(`.wval[data-k="${k}"]`).textContent = state.weights[k].toFixed(2);
      recompute();
    }));
  $("topk").addEventListener("input", (e) => {
    state.topK = parseInt(e.target.value, 10);
    $("topk-out").textContent = state.topK;
    recompute();
  });
  $("show-priority").checked = state.showPriority;
  $("show-priority").onchange = (e) => {
    state.showPriority = e.target.checked;
    renderPriority();
  };
}

// ------------------------------------------------------------- recompute
function recompute() {
  if (!state.data) return;
  const shown = [];
  for (const f of state.data.features.features) {
    const dkey = f.properties.detector;
    if (!state.active[dkey]) continue;
    shown.push({ f, dkey, composite: state.weights[dkey] * f.properties.score_norm });
  }
  shown.sort((a, b) => b.composite - a.composite);
  const top = shown.slice(0, state.topK);
  top.forEach((e, i) => (e.rank = i + 1));
  state.current = top;

  oppLayer.clearLayers();
  for (const e of top) {
    const col = state.colorOf[e.dkey];
    const layer = L.geoJSON(e.f, {
      style: { color: col, weight: 2 + 6 * e.composite, opacity: 0.25 + 0.7 * e.composite,
               fillColor: col, fillOpacity: 0.12 + 0.5 * e.composite },
    });
    layer.bindPopup(popupHtml(e));
    if (e.dkey === "D5") {
      const cid = String(e.f.properties.cell_id);
      layer.on("click", () => showZoneSegments(cid));
    }
    layer.addTo(oppLayer);
    e.layer = layer;
  }
  renderList(top);
  $("result-count").textContent = t("shownOf", top.length, shown.length);
  renderPriority();  // the composite cell layer follows the same weights
}

// ------------------------------------------------- composite "Priority" layer
// A cell's composite = Σ_active weight[det] * cell[det]  (cell[det] = the
// per-detector normalised value the server aggregated onto the shared grid).
function cellComposite(props) {
  let s = 0;
  for (const det of state.compositeCriteria) {
    if (!state.active[det]) continue;
    s += (state.weights[det] || 0) * (props[det] || 0);
  }
  return s;
}

function renderPriority() {
  priorityLayer.clearLayers();
  const comp = state.data && state.data.composite;
  if (!state.showPriority || !comp || !comp.features || !comp.features.length) return;
  const vals = comp.features.map((f) => cellComposite(f.properties));
  const maxv = Math.max(0, ...vals);
  if (maxv <= 0) return;
  comp.features.forEach((f, i) => {
    const v = vals[i] / maxv;
    if (v <= 0) return;
    const lyr = L.geoJSON(f, {
      style: { stroke: false, fillColor: "#6d28d9", fillOpacity: 0.12 + 0.55 * v },
    });
    lyr.bindPopup(priorityPopup(f.properties, vals[i]));
    lyr.addTo(priorityLayer);
  });
}

function priorityPopup(p, comp) {
  const parts = [];
  for (const det of state.compositeCriteria) {
    if (!state.active[det]) continue;
    const c = (state.weights[det] || 0) * (p[det] || 0);
    if (c > 0) parts.push({ det, c });
  }
  parts.sort((a, b) => b.c - a.c);
  const rows = [`<b>${t("priority")}</b>: ${comp.toFixed(3)}`,
                `<hr style="margin:4px 0">${t("contributions")}:`];
  for (const x of parts) {
    rows.push(`<span class="dot" style="background:${state.colorOf[x.det]}"></span> ` +
              `${x.det} · ${detLabel(x.det)}: ${x.c.toFixed(3)}`);
  }
  return `<div style="max-width:260px">${rows.join("<br>")}</div>`;
}

function popupHtml(e) {
  const p = e.f.properties;
  const rows = [
    `<b>#${e.rank}</b> — ${p.detector} · ${detLabel(p.detector)} · ${t("composite")} ${e.composite.toFixed(3)}`,
    `${t("normScore")}: ${p.score_norm.toFixed(3)} (${t("raw")} ${p.score})`,
  ];
  if (p.gap_m != null) rows.push(`gap: ${p.gap_m} m · road: ${p.road_len_m} m`);
  if (p.gap_len_m != null) rows.push(`gap length: ${p.gap_len_m} m`);
  if (p.residual != null) rows.push(`demand ${p.demand} · supply ${p.supply} · residual ${p.residual}`);
  if (e.dkey === "D5") {
    const n = ((state.data.zone_segments || {})[String(p.cell_id)] || []).length;
    rows.push(`<hr style="margin:4px 0">${t("proposedLanes", n)}`);
  }
  if (p.explanation) rows.push(`<hr style="margin:4px 0">${p.explanation}`);
  return `<div style="max-width:260px">${rows.join("<br>")}</div>`;
}

function renderList(top) {
  const ol = $("ranklist");
  ol.innerHTML = "";
  top.slice(0, 30).forEach((e) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="rk">${e.rank}</span>
      <span class="chip" style="background:${state.colorOf[e.dkey]}">${e.dkey}</span>
      <span class="sc">${e.composite.toFixed(3)}</span>`;
    li.addEventListener("click", () => {
      map.fitBounds(e.layer.getBounds().pad(2));
      e.layer.openPopup();
    });
    ol.appendChild(li);
  });
}

// ------------------------------------------------------- D5 zone drill-down
function showZoneSegments(cellId) {
  segLayer.clearLayers();
  const segs = ((state.data && state.data.zone_segments) || {})[String(cellId)] || [];
  if (!segs.length) return;
  const lyr = L.geoJSON({ type: "FeatureCollection", features: segs }, {
    style: { color: "#4c1d95", weight: 5, opacity: 0.95 },
    onEachFeature: (f, l) => {
      const p = f.properties;
      l.bindPopup(`<div style="max-width:260px"><b>${t("proposedLane")}</b><br>` +
        `${p.street} (${p.highway}, ${Math.round(p.length_m)} m)<br>score ${p.score}` +
        `<hr style="margin:4px 0">${p.explanation || ""}</div>`);
    },
  }).addTo(segLayer);
  map.fitBounds(lyr.getBounds().pad(0.4));
}

// ------------------------------------------------------------------ export
function _citySlug() {
  return ((state.data && state.data.city) || "city")
    .split(",")[0].trim().toLowerCase().replace(/\s+/g, "_");
}
function _download(name, mime, text) {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
function _wkt(g) {
  if (!g) return "";
  const pts = (cs) => cs.map((c) => c.join(" ")).join(", ");
  const ring = (r) => "(" + pts(r) + ")";
  switch (g.type) {
    case "Point": return `POINT(${g.coordinates.join(" ")})`;
    case "LineString": return `LINESTRING(${pts(g.coordinates)})`;
    case "Polygon": return `POLYGON(${g.coordinates.map(ring).join(", ")})`;
    case "MultiLineString": return `MULTILINESTRING(${g.coordinates.map((l) => "(" + pts(l) + ")").join(", ")})`;
    case "MultiPolygon": return `MULTIPOLYGON(${g.coordinates.map((p) => "(" + p.map(ring).join(", ") + ")").join(", ")})`;
    default: return "";
  }
}
function exportGeoJSON() {
  if (!state.current.length) return;
  const fc = { type: "FeatureCollection", features: state.current.map((e) => ({
    type: "Feature", geometry: e.f.geometry,
    properties: Object.assign({ rank: e.rank, detector: e.dkey, composite: Number(e.composite.toFixed(6)) }, e.f.properties),
  })) };
  _download(`bicyclelane_${_citySlug()}_opportunities.geojson`, "application/geo+json", JSON.stringify(fc));
}
function exportCSV() {
  if (!state.current.length) return;
  const cols = ["rank", "detector", "composite", "score", "score_norm", "id", "explanation", "geometry_wkt"];
  const esc = (s) => '"' + String(s == null ? "" : s).replace(/"/g, '""') + '"';
  const lines = [cols.join(",")];
  for (const e of state.current) {
    const p = e.f.properties;
    lines.push([e.rank, e.dkey, e.composite.toFixed(6), p.score, p.score_norm, p.id || "",
                p.explanation || "", _wkt(e.f.geometry)].map(esc).join(","));
  }
  _download(`bicyclelane_${_citySlug()}_opportunities.csv`, "text/csv", lines.join("\n"));
}
$("dl-geojson").addEventListener("click", exportGeoJSON);
$("dl-csv").addEventListener("click", exportCSV);

// initial language
applyI18n();
