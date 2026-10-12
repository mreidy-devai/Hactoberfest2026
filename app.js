'use strict';

/* Run Club Route Builder
 * Static, no build step. Open pieces:
 *  - request parsing: local open-weight model via Ollama (fallback: regex rules)
 *  - geocoding: OpenStreetMap Nominatim
 *  - routing: OSM foot routing (OSRM) -> candidate loops
 *  - scoring: OpenStreetMap data via Overpass (parks, woods, water, mapped trees)
 *  - map: Leaflet
 */

const MI = 1609.344;
const PREFS = ['shade', 'foliage', 'green', 'water', 'flat', 'hilly'];
const ELEVATION = 'https://api.open-meteo.com/v1/elevation';
/* Routing server base URL. Default is the public OSM foot router; set a self-hosted OSRM
 * (see selfhost/) in Settings to keep your start point off third-party servers. */
const PUBLIC_ROUTER = 'https://routing.openstreetmap.de/routed-foot';
let routerBase = PUBLIC_ROUTER;
function normalizeRouterUrl(s) {
  const t = String(s || '').trim().replace(/\/+$/, '');
  if (!t) return PUBLIC_ROUTER;
  let u;
  try { u = new URL(t); } catch (_) { throw new Error('Routing server URL is not valid: ' + t); }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') throw new Error('Routing server URL must start with http:// or https://');
  return t;
}
const OVERPASS = 'https://overpass-api.de/api/interpreter';
const NOMINATIM = 'https://nominatim.openstreetmap.org/search';

/* ---------- geometry ---------- */
const rad = d => (d * Math.PI) / 180;
function haversine(a, b) {
  const R = 6371000;
  const dLat = rad(b.lat - a.lat), dLon = rad(b.lon - a.lon);
  const s = Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}
function destination(lat, lon, bearingDeg, distM) {
  const R = 6371000, br = rad(bearingDeg), d = distM / R;
  const la1 = rad(lat), lo1 = rad(lon);
  const la2 = Math.asin(Math.sin(la1) * Math.cos(d) + Math.cos(la1) * Math.sin(d) * Math.cos(br));
  const lo2 = lo1 + Math.atan2(Math.sin(br) * Math.sin(d) * Math.cos(la1),
    Math.cos(d) - Math.sin(la1) * Math.sin(la2));
  return { lat: (la2 * 180) / Math.PI, lon: (((lo2 * 180) / Math.PI + 540) % 360) - 180 };
}
/* coords are GeoJSON [lon, lat]; returns {lat, lon} points every ~stepM meters */
function sampleRoute(coords, stepM = 30) {
  const out = [];
  for (let i = 0; i < coords.length - 1; i++) {
    const a = { lat: coords[i][1], lon: coords[i][0] };
    const b = { lat: coords[i + 1][1], lon: coords[i + 1][0] };
    const n = Math.max(1, Math.round(haversine(a, b) / stepM));
    for (let k = 0; k < n; k++) {
      out.push({ lat: a.lat + ((b.lat - a.lat) * k) / n, lon: a.lon + ((b.lon - a.lon) * k) / n });
    }
  }
  const last = coords[coords.length - 1];
  out.push({ lat: last[1], lon: last[0] });
  return out;
}
function pointInRing(lat, lon, ring) {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const yi = ring[i].lat, xi = ring[i].lon, yj = ring[j].lat, xj = ring[j].lon;
    if ((yi > lat) !== (yj > lat) && lon < ((xj - xi) * (lat - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/* ---------- request parsing ---------- */
function parseRegex(text) {
  const t = ' ' + text.toLowerCase().replace(/\s+/g, ' ') + ' ';
  let distanceValue = null, unit = null;
  if (/half[- ]marathon/.test(t)) { distanceValue = 21.0975; unit = 'km'; }
  else if (/\bmarathon\b/.test(t)) { distanceValue = 42.195; unit = 'km'; }
  else {
    const m = t.match(/(\d+(?:\.\d+)?)\s*(km|kilometers?|kilometres?|k|miles?|mi)\b/);
    if (m) { distanceValue = parseFloat(m[1]); unit = /^k/.test(m[2]) ? 'km' : 'mi'; }
  }
  const prefs = [];
  if (/\b(shade|shaded|shady|tree|trees|canopy)\b/.test(t)) prefs.push('shade');
  if (/\b(foliage|fall colou?rs?|autumn|leaves|leaf)\b/.test(t)) prefs.push('foliage');
  if (/\b(park|parks|green|nature|trail|trails|woods|forest)\b/.test(t)) prefs.push('green');
  if (/\b(water|river|lake|creek|stream|pond|waterfront)\b/.test(t)) prefs.push('water');
  // flat wins over hilly so "no hills" / "not hilly" are not read as wanting hills
  if (/\b(flat|level|pancake|gentle|no hills?|not hilly|few hills?|easy)\b/.test(t)) prefs.push('flat');
  else if (/\b(hilly|hills?|climb|climbs|climbing|elevation|challenging)\b/.test(t)) prefs.push('hilly');

  const end = '(?:[.;]|\\s+(?:with|and|for|that|but|where)\\b|$)';
  const m1 = text.match(new RegExp('\\b(?:start(?:s|ing)?|begin(?:s|ning)?)\\s+(?:at|from|in|near|by)\\s+(.+?)\\s*' + end, 'i'));
  const m2 = text.match(new RegExp('\\b(?:from|near)\\s+(.+?)\\s*' + end, 'i'));
  let start = (m1 && m1[1]) || (m2 && m2[1]) || null;
  if (start) start = start.replace(/^the\s+/i, '').replace(/[,.;\s]+$/, '').trim() || null;
  return { distanceValue, unit, prefs, start };
}

function normalizeParsed(raw, defaultUnit) {
  const o = raw && typeof raw === 'object' ? raw : {};
  let unit = o.unit === 'km' || o.unit === 'mi' ? o.unit : null;
  let value = Number(o.distanceValue ?? o.distance_value);
  if (!isFinite(value) || value <= 0) value = null;
  if (value !== null) {
    unit = unit || defaultUnit;
    const km = unit === 'mi' ? (value * MI) / 1000 : value;
    if (km < 0.5 || km > 60) value = null;
  }
  const prefs = Array.isArray(o.prefs) ? [...new Set(o.prefs.filter(p => PREFS.includes(p)))] : [];
  let start = typeof o.start === 'string' ? o.start.trim().slice(0, 120) : null;
  if (!start) start = null;
  return { distanceValue: value, unit: value === null ? null : unit, prefs, start };
}

async function parseWithOllama(text, { url, model }) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), 45000);
  try {
    const res = await fetch(url.replace(/\/+$/, '') + '/api/chat', {
      method: 'POST',
      signal: ctl.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model,
        stream: false,
        format: 'json',
        options: { temperature: 0 },
        messages: [
          {
            role: 'system',
            content:
              'Extract a running-route request into JSON with exactly these keys: ' +
              '"distanceValue" (number or null), "unit" ("km" or "mi" or null), ' +
              '"prefs" (array using only: shade, foliage, green, water, flat, hilly), ' +
              '"start" (place name or address where the run starts, or null). ' +
              'Use "shade" for trees/shaded/canopy, "foliage" for fall colors/leaves, ' +
              '"green" for parks/trails/nature, "water" for river/lake/waterfront, ' +
              '"flat" for flat/easy/no hills, "hilly" for hills/climbing/challenging. ' +
              'Output JSON only.'
          },
          { role: 'user', content: text }
        ]
      })
    });
    if (!res.ok) throw new Error('Ollama HTTP ' + res.status);
    const j = await res.json();
    return JSON.parse(j.message.content);
  } finally {
    clearTimeout(timer);
  }
}

/* ---------- network helpers ---------- */
async function geocode(q, bias) {
  const u = new URL(NOMINATIM);
  u.searchParams.set('format', 'jsonv2');
  u.searchParams.set('q', q);
  u.searchParams.set('limit', '1');
  if (bias) {
    const d = 0.15;
    u.searchParams.set('viewbox', [bias.lon - d, bias.lat + d, bias.lon + d, bias.lat - d].join(','));
  }
  const res = await fetch(u);
  if (!res.ok) throw new Error('Geocoder HTTP ' + res.status);
  const arr = await res.json();
  if (!arr.length) return null;
  return { lat: parseFloat(arr[0].lat), lon: parseFloat(arr[0].lon), label: arr[0].display_name };
}

async function footRoute(points) {
  const coords = points.map(p => p.lon.toFixed(6) + ',' + p.lat.toFixed(6)).join(';');
  const res = await fetch(routerBase + '/route/v1/foot/' + coords + '?overview=full&geometries=geojson&continue_straight=false');
  if (!res.ok) throw new Error('Router HTTP ' + res.status);
  const j = await res.json();
  if (!j.routes || !j.routes.length) throw new Error('No route');
  return { distance: j.routes[0].distance, coords: j.routes[0].geometry.coordinates };
}

/* Loop through 3 waypoints on a circle that passes through the start. */
function loopWaypoints(start, targetM, bearing, scale) {
  const r = (targetM / (2 * Math.PI * 1.3)) * scale; // 1.3 ~ street-network detour factor
  const c = destination(start.lat, start.lon, bearing, r);
  const back = (bearing + 180) % 360;
  const pts = [start];
  for (const a of [90, 180, 270]) pts.push(destination(c.lat, c.lon, (back + a) % 360, r));
  pts.push(start);
  return pts;
}

async function buildCandidate(start, targetM, bearing) {
  let scale = 1;
  let r = await footRoute(loopWaypoints(start, targetM, bearing, scale));
  const off = Math.abs(r.distance - targetM) / targetM;
  if (off > 0.15) {
    scale = targetM / r.distance;
    try { r = await footRoute(loopWaypoints(start, targetM, bearing, scale)); } catch (_) { /* keep first */ }
  }
  return { bearing, distance: r.distance, coords: r.coords };
}

async function pool(tasks, n) {
  const out = new Array(tasks.length);
  let i = 0;
  await Promise.all(Array.from({ length: n }, async () => {
    while (i < tasks.length) {
      const k = i++;
      try { out[k] = await tasks[k](); } catch (_) { out[k] = null; }
    }
  }));
  return out;
}

/* ---------- OSM cover data & scoring ---------- */
async function fetchCover(center, radiusM) {
  const dLat = radiusM / 111320;
  const dLon = radiusM / (111320 * Math.cos(rad(center.lat)));
  const bb = [center.lat - dLat, center.lon - dLon, center.lat + dLat, center.lon + dLon]
    .map(x => x.toFixed(5)).join(',');
  const q = '[out:json][timeout:40];(' +
    `way["leisure"~"^(park|nature_reserve|garden)$"](${bb});` +
    `way["landuse"~"^(forest|grass|meadow|recreation_ground|village_green)$"](${bb});` +
    `way["natural"~"^(wood|scrub|grassland)$"](${bb});` +
    `way["natural"="water"](${bb});` +
    ');out geom;' +
    `node["natural"="tree"](${bb});out skel;`;
  const res = await fetch(OVERPASS, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: 'data=' + encodeURIComponent(q)
  });
  if (!res.ok) throw new Error('Overpass HTTP ' + res.status);
  const j = await res.json();
  return indexCover(j.elements || []);
}

const CELL_LAT = 0.0002, CELL_LON = 0.00026; // roughly 22m x 22m at mid-latitudes
const cellKey = (lat, lon) => Math.floor(lat / CELL_LAT) + ',' + Math.floor(lon / CELL_LON);

function indexCover(elements) {
  const green = [], water = [];
  const trees = new Map(), waterPts = new Map();
  const add = (map, p) => {
    const k = cellKey(p.lat, p.lon);
    (map.get(k) || map.set(k, []).get(k)).push(p);
  };
  for (const e of elements) {
    if (e.type === 'node' && typeof e.lat === 'number') { add(trees, e); continue; }
    if (e.type !== 'way' || !e.geometry || e.geometry.length < 4) continue;
    const g = e.geometry;
    const closed = g[0].lat === g[g.length - 1].lat && g[0].lon === g[g.length - 1].lon;
    if (!closed) continue;
    let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
    for (const p of g) {
      if (p.lat < minLat) minLat = p.lat; if (p.lat > maxLat) maxLat = p.lat;
      if (p.lon < minLon) minLon = p.lon; if (p.lon > maxLon) maxLon = p.lon;
    }
    const poly = { ring: g, minLat, maxLat, minLon, maxLon };
    if (e.tags && e.tags.natural === 'water') { water.push(poly); for (const p of g) add(waterPts, p); }
    else green.push(poly);
  }
  return { green, water, trees, waterPts, treeCount: [...trees.values()].reduce((s, a) => s + a.length, 0) };
}

function nearIndexed(map, p, maxM) {
  const ci = Math.floor(p.lat / CELL_LAT), cj = Math.floor(p.lon / CELL_LON);
  for (let di = -1; di <= 1; di++) for (let dj = -1; dj <= 1; dj++) {
    const arr = map.get((ci + di) + ',' + (cj + dj));
    if (arr) for (const q of arr) if (haversine(p, q) <= maxM) return true;
  }
  return false;
}
function inAny(polys, p) {
  for (const poly of polys) {
    if (p.lat < poly.minLat || p.lat > poly.maxLat || p.lon < poly.minLon || p.lon > poly.maxLon) continue;
    if (pointInRing(p.lat, p.lon, poly.ring)) return true;
  }
  return false;
}
function scoreCover(coords, cover) {
  const pts = sampleRoute(coords, 30);
  let g = 0, w = 0;
  for (const p of pts) {
    if (inAny(cover.green, p) || nearIndexed(cover.trees, p, 22)) g++;
    if (inAny(cover.water, p) || nearIndexed(cover.waterPts, p, 30)) w++;
  }
  return { green: g / pts.length, water: w / pts.length };
}

/* ---------- elevation ---------- */
/* Evenly spaced points along the route, at most maxPts (Open-Meteo allows 100 per request). */
function profilePoints(coords, maxPts = 100) {
  let len = 0;
  for (let i = 0; i < coords.length - 1; i++) {
    len += haversine({ lat: coords[i][1], lon: coords[i][0] }, { lat: coords[i + 1][1], lon: coords[i + 1][0] });
  }
  const pts = sampleRoute(coords, Math.max(30, len / (maxPts - 1)));
  if (pts.length <= maxPts) return pts;
  const out = [];
  for (let i = 0; i < maxPts; i++) out.push(pts[Math.round((i * (pts.length - 1)) / (maxPts - 1))]);
  return out;
}

async function fetchElevations(pts) {
  const u = new URL(ELEVATION);
  u.searchParams.set('latitude', pts.map(p => p.lat.toFixed(5)).join(','));
  u.searchParams.set('longitude', pts.map(p => p.lon.toFixed(5)).join(','));
  const res = await fetch(u);
  if (!res.ok) throw new Error('Elevation HTTP ' + res.status);
  const j = await res.json();
  if (!Array.isArray(j.elevation) || j.elevation.length !== pts.length) throw new Error('Bad elevation response');
  return j.elevation;
}

/* Total ascent/descent in meters. The digital elevation model is coarse (~30-90 m cells),
 * so changes smaller than minStep are ignored instead of summed as noise. */
function elevationStats(elev, minStep = 3) {
  if (!Array.isArray(elev) || elev.length < 2) return null;
  let gain = 0, loss = 0, ref = elev[0], min = Infinity, max = -Infinity;
  for (const e of elev) {
    if (e < min) min = e;
    if (e > max) max = e;
    const d = e - ref;
    if (d >= minStep) { gain += d; ref = e; }
    else if (d <= -minStep) { loss -= d; ref = e; }
  }
  return { gain, loss, min, max };
}

function rankRoutes(routes, targetM, prefs) {
  const wantGreen = prefs.some(p => p === 'shade' || p === 'foliage' || p === 'green');
  const wGreen = wantGreen ? 1 : 0.25;
  const wWater = prefs.includes('water') ? 0.9 : 0;
  const flat = prefs.includes('flat') && !prefs.includes('hilly');
  const hilly = prefs.includes('hilly') && !prefs.includes('flat');
  for (const r of routes) {
    const acc = Math.abs(r.distance - targetM) / targetM;
    let s = (r.green == null ? 0 : wGreen * r.green + wWater * r.water) - 1.5 * acc;
    if (r.gain != null && (flat || hilly)) {
      const perKm = r.gain / (r.distance / 1000);
      const norm = Math.min(perKm / 25, 1.5); // ~25 m of climb per km counts as hilly
      s += flat ? -0.8 * norm : 0.5 * norm;
    }
    r.score = s;
  }
  return routes.slice().sort((a, b) => b.score - a.score);
}

function toGPX(name, coords) {
  const esc = s => s.replace(/[<>&"]/g, c => ({ '<': '&lt;', '>': '&gt;', '&': '&amp;', '"': '&quot;' }[c]));
  const pts = coords.map(c => `<trkpt lat="${c[1]}" lon="${c[0]}"/>`).join('');
  return '<?xml version="1.0" encoding="UTF-8"?>' +
    '<gpx version="1.1" creator="Run Club Route Builder" xmlns="http://www.topografix.com/GPX/1/1">' +
    `<trk><name>${esc(name)}</name><trkseg>${pts}</trkseg></trk></gpx>`;
}

/* ---------- UI ---------- */
function initUI() {
  const $ = s => document.querySelector(s);
  const store = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (_) { /* ignore */ } }
  };
  const state = { here: null, layers: [], routes: [], selected: 0 };
  const COLORS = ['--a', '--b', '--c'];
  const cssVar = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  const ask = $('#ask'), units = $('#units'), ollamaUrl = $('#ollamaUrl'), model = $('#model');
  const routerUrl = $('#routerUrl');
  const statusEl = $('#status'), go = $('#go');
  units.value = store.get('units', 'mi');
  ollamaUrl.value = store.get('ollamaUrl', ollamaUrl.value);
  model.value = store.get('model', model.value);
  routerUrl.value = store.get('routerUrl', '');
  for (const el of [units, ollamaUrl, model, routerUrl]) el.addEventListener('change', () => store.set(el.id, el.value));

  const setStatus = (msg, err = false) => { statusEl.textContent = msg; statusEl.className = err ? 'err' : ''; };

  let map = null;
  if (typeof L !== 'undefined') {
    map = L.map('map', { zoomControl: true }).setView([40.82, -74.16], 12);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 19, attribution: '&copy; OpenStreetMap contributors'
    }).addTo(map);
  } else {
    $('#map').textContent = 'Map library could not load (offline?). Routes will still be listed.';
  }

  document.querySelectorAll('#chips button').forEach(b =>
    b.addEventListener('click', () => { ask.value = b.dataset.ex; ask.focus(); }));

  $('#locate').addEventListener('click', () => {
    if (!navigator.geolocation) return setStatus('Geolocation is not available in this browser.', true);
    setStatus('Finding you...');
    navigator.geolocation.getCurrentPosition(
      pos => {
        state.here = { lat: pos.coords.latitude, lon: pos.coords.longitude };
        if (map) map.setView([state.here.lat, state.here.lon], 14);
        setStatus('Got your location. It will be used when your request does not name a start.');
      },
      err => setStatus('Could not get location: ' + err.message, true),
      { enableHighAccuracy: true, timeout: 15000 }
    );
  });

  function draw() {
    state.layers.forEach(l => map && map.removeLayer(l));
    state.layers = [];
    document.querySelectorAll('.card').forEach((c, i) => c.classList.toggle('sel', i === state.selected));
    if (!map) return;
    state.routes.forEach((r, i) => {
      const sel = i === state.selected;
      const line = L.polyline(r.coords.map(c => [c[1], c[0]]), {
        color: cssVar(COLORS[i]), weight: sel ? 6 : 3, opacity: sel ? 0.95 : 0.5
      }).addTo(map);
      state.layers.push(line);
      if (sel) { line.bringToFront(); map.fitBounds(line.getBounds(), { padding: [20, 20] }); }
    });
  }

  function render(unit) {
    const list = $('#results');
    list.textContent = '';
    state.routes.forEach((r, i) => {
      const li = document.createElement('li');
      li.className = 'card';
      li.style.borderLeftColor = cssVar(COLORS[i]);
      const h = document.createElement('h2');
      h.textContent = 'Route ' + 'ABC'[i];
      const dist = unit === 'mi' ? (r.distance / MI).toFixed(2) + ' mi' : (r.distance / 1000).toFixed(2) + ' km';
      const bits = [dist];
      if (r.green != null) bits.push(Math.round(r.green * 100) + '% trees/parks');
      if (r.water != null && r.water > 0.03) bits.push(Math.round(r.water * 100) + '% near water');
      if (r.gain != null) {
        bits.push('+' + (unit === 'mi' ? Math.round(r.gain * 3.28084) + ' ft' : Math.round(r.gain) + ' m') + ' climb');
      }
      const stats = document.createElement('div');
      stats.className = 'stats';
      stats.textContent = bits.join('  |  ');
      const prof = r.elev ? profileSvg(r.elev, cssVar(COLORS[i])) : null;
      const show = document.createElement('button');
      show.type = 'button'; show.textContent = 'Show on map';
      show.addEventListener('click', () => { state.selected = i; draw(); });
      const gpx = document.createElement('button');
      gpx.type = 'button'; gpx.textContent = 'Download GPX';
      gpx.addEventListener('click', () => {
        const blob = new Blob([toGPX('Run Club Route ' + 'ABC'[i], r.coords)], { type: 'application/gpx+xml' });
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = 'run-route-' + 'abc'[i] + '.gpx';
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 1000);
      });
      li.append(h, stats);
      if (prof) li.appendChild(prof);
      li.append(show, gpx);
      list.appendChild(li);
    });
  }

  /* tiny elevation profile; y range has a 10 m floor so flat routes look flat */
  function profileSvg(elev, color) {
    const W = 300, H = 44, NS = 'http://www.w3.org/2000/svg';
    const min = Math.min(...elev), max = Math.max(...elev, min + 10);
    const pts = elev.map((e, k) =>
      ((k / (elev.length - 1)) * W).toFixed(1) + ',' + (H - 2 - ((e - min) / (max - min)) * (H - 4)).toFixed(1)).join(' ');
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    svg.setAttribute('class', 'profile');
    svg.setAttribute('role', 'img');
    svg.setAttribute('aria-label', 'Elevation profile, low ' + Math.round(min) + ' m, high ' + Math.round(max) + ' m');
    const fill = document.createElementNS(NS, 'polygon');
    fill.setAttribute('points', '0,' + H + ' ' + pts + ' ' + W + ',' + H);
    fill.setAttribute('fill', color);
    fill.setAttribute('opacity', '0.18');
    const line = document.createElementNS(NS, 'polyline');
    line.setAttribute('points', pts);
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', color);
    line.setAttribute('stroke-width', '2');
    svg.append(fill, line);
    return svg;
  }

  $('#form').addEventListener('submit', async ev => {
    ev.preventDefault();
    const text = ask.value.trim();
    if (!text) return setStatus('Describe the run you want first.', true);
    go.disabled = true;
    try {
      routerBase = normalizeRouterUrl(routerUrl.value);
      const selfHosted = routerBase !== PUBLIC_ROUTER;
      setStatus('Reading your request...');
      let parsed, parser;
      try {
        parsed = normalizeParsed(await parseWithOllama(text, { url: ollamaUrl.value, model: model.value }), units.value);
        parser = 'local model (' + model.value + ')';
      } catch (_) {
        parsed = normalizeParsed(parseRegex(text), units.value);
        parser = 'built-in rules (local model not reachable)';
      }
      const unit = parsed.unit || units.value;
      const targetM = parsed.distanceValue
        ? (unit === 'mi' ? parsed.distanceValue * MI : parsed.distanceValue * 1000)
        : (units.value === 'mi' ? 3 * MI : 5000);

      let start = null;
      if (parsed.start) {
        setStatus('Finding "' + parsed.start + '"...');
        start = await geocode(parsed.start, state.here);
        if (!start) throw new Error('Could not find "' + parsed.start + '". Try a more specific place name.');
      } else if (state.here) {
        start = state.here;
      } else {
        throw new Error('Name a starting place, or tap "Use my location".');
      }

      setStatus('Building loops and checking tree and park cover...');
      const radius = Math.max(900, targetM * 0.4);
      const coverP = fetchCover(start, radius).catch(() => null);
      const bearings = [0, 60, 120, 180, 240, 300];
      const cands = (await pool(bearings.map(b => () => buildCandidate(start, targetM, b)), 3)).filter(Boolean);
      if (!cands.length) {
        throw new Error(selfHosted
          ? 'Your routing server at ' + routerBase + ' did not return any loops. Is it running, and does its map extract cover this start point? ' +
            '(Not falling back to the public server, so your start point stays private.)'
          : 'The routing service did not return any loops. Try again in a moment.');
      }

      const cover = await coverP;
      for (const c of cands) {
        if (cover) Object.assign(c, scoreCover(c.coords, cover));
        else { c.green = null; c.water = null; }
      }
      setStatus('Checking hills...');
      await pool(cands.map(c => async () => {
        const elev = await fetchElevations(profilePoints(c.coords));
        const st = elevationStats(elev);
        if (st) { c.gain = st.gain; c.elev = elev; }
      }), 3);
      const noElev = cands.every(c => c.gain == null);
      state.routes = rankRoutes(cands, targetM, parsed.prefs).slice(0, 3);
      state.selected = 0;
      render(unit);
      draw();
      let note = cover ? '' : ' Tree/park data was unavailable, so tree preferences were not applied.';
      if (noElev) note += ' Elevation data was unavailable, so hills were not considered.';
      setStatus('Parsed with ' + parser + '. Routing: ' + (selfHosted ? 'your server' : 'public OSM') + '. Start: ' +
        (start.label ? start.label.split(',').slice(0, 2).join(',') : 'your location') + '.' + note);
    } catch (e) {
      setStatus(e.message || String(e), true);
    } finally {
      go.disabled = false;
    }
  });
}

if (typeof document !== 'undefined') initUI();
if (typeof module !== 'undefined') {
  module.exports = { parseRegex, normalizeParsed, haversine, destination, sampleRoute, pointInRing,
    loopWaypoints, indexCover, scoreCover, rankRoutes, toGPX, profilePoints, elevationStats, normalizeRouterUrl };
}
