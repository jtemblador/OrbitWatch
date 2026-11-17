/**
 * OrbitWatch — snapshot data layer (Phase 9.3).
 *
 * The deployed site is static: this module loads ONE file (snapshot.json,
 * built offline by scripts/build_snapshot.py) and everything downstream —
 * positions, trails, panel data, the conjunction list — is computed in the
 * browser from it. Zero calls to a backend or CelesTrak per visit.
 *
 * Owns:
 *   • snapshotReady        — Promise the render modules await
 *   • snapshotMeta         — freshness + screen params (drives the header line)
 *   • satelliteMetadata    — Map<norad_id, {name, object_type, epoch, …}>
 *   • conjunctionView()    — events adapted to the field names the UX code uses,
 *                            filtered (same-constellation / persistent) + capped
 *   • single-satellite compute helpers (satrec cache, geodetic position, TEME
 *     track) — cheap enough on the main thread; the all-catalog batch runs in
 *     js/propagation-worker.js instead.
 *
 * Depends on: satellite.js global (pinned CDN script in index.html).
 */

// WGS-72 values — SGP4's own constants; must match backend/core/tle_fetcher.py
// (GM_EARTH / R_EARTH) so the derived apoapsis/periapsis agree with the old API.
const GM_EARTH = 398600.8;  // km³/s²
const R_EARTH = 6378.135;   // km equatorial

// Display groups — each satellite belongs to EXACTLY ONE (mutually exclusive),
// assigned by _classifyGroup at load. Order = render/legend order. `color` is a
// CSS hex used for both the globe point and the filter swatch. Toggling a group
// off stops its per-frame animation work (satellites.js) — the heat lever.
const SAT_GROUPS = [
  { key: "starlink",   label: "Starlink",         color: "#ff8c3a" },
  { key: "stations",   label: "Space Stations",   color: "#ffe14a" },
  { key: "navigation", label: "Navigation / MEO", color: "#4ade80" },
  { key: "leo",        label: "Other LEO",        color: "#4fc3f7" },
  { key: "geo",        label: "GEO / high orbit", color: "#c084fc" },
];
// Name-prefix matchers (word-boundary so "COSMOS" isn't caught by a stray token).
const _STATION_RE = /\b(ISS|ZARYA|TIANHE|TIANGONG|MENGTIAN|WENTIAN|CSS)\b/;
const _GNSS_RE = /\b(GPS|NAVSTAR|GLONASS|GALILEO|BEIDOU)\b/;

// Display cap on conjunctions (Phase 10.6 full-catalog perf): the snapshot now
// carries the ENTIRE screened catalog's events (~7.7k pairs → ~6.9k unique
// participant sats), and animating that many dots made the whole machine lag.
// The UI works with only the closest CAP events (list, arcs, participant dots,
// worker propagation all follow from this one slice — events arrive
// closest-first). The full set still exists in snapshot.json + the archive;
// the header shows "closest N of TOTAL" so the display stays honest.
// 500 events ≈ ~800 participant sats — comparable per-frame work to the old
// 5k-catalog view, with 15 fps + occlusion-skip (satellites.js) as headroom.
const CONJ_DISPLAY_CAP = 500;

// 10.10 cross-constellation filter. 80% of events are Starlink × Starlink —
// pairs the operator's own fleet coordination handles. Pairs that cross
// operators are the ones that need two parties talking, so the list can hide
// same-constellation pairs. Matched on the catalog name family (curated, not
// exhaustive — an unlisted constellation's internal pairs stay visible).
const _CONSTELLATION_RE =
  /^(STARLINK|ONEWEB|KUIPER|QIANFAN|HULIANWANG|IRIDIUM|GLOBALSTAR|ORBCOMM|FLOCK|SKYSAT|LEMUR|HAWK|ICEYE|GEESAT|JILIN)\b/;

function constellationOf(name) {
  const m = _CONSTELLATION_RE.exec(name || "");
  if (!m) return null;
  return m[1] === "SKYSAT" ? "FLOCK" : m[1]; // both Planet
}

/** True if both participants of a raw snapshot conjunction share a constellation. */
function isSameConstellation(c) {
  const fam = constellationOf(c.a_name);
  return fam !== null && fam === constellationOf(c.b_name);
}

// 11.0c persistent formations. The snapshot builder marks a pair `persistent`
// when it has appeared in >= 9 consecutive runs (~3 days) at < 10 m/s relative
// speed — TerraSAR-X / TanDEM-X flying in formation, GEO co-located slots. They
// are real geometry (the screen keeps them; nothing is suppressed) but not
// crossings, so the list hides them by default behind a toggle. Absent from
// every event in a pre-11.0 snapshot → the filter is then a no-op.
function isPersistentFormation(c) {
  return c.persistent === true;
}

/** The filtered view of raw (closest-first) snapshot conjunctions: FILTER, then
 *  cap — so the view holds the closest `cap` events that pass the filters, not
 *  just the few that made the overall top `cap`. Returns the raw records.
 *  opts: { hideSame: same-constellation pairs, hidePersistent: formations }. */
function filterConjunctions(conjunctions, opts, cap) {
  const o = opts || {};
  const kept = conjunctions.filter((c) =>
    !(o.hideSame && isSameConstellation(c)) &&
    !(o.hidePersistent && isPersistentFormation(c)));
  return { total: kept.length, shown: kept.slice(0, cap) };
}

/** The cross-constellation view (10.10) — filterConjunctions with only that filter. */
function crossConstellationSet(conjunctions, cap) {
  return filterConjunctions(conjunctions, { hideSame: true }, cap);
}

// 11.0a: element age (days at generated_at) beyond which an event's prediction
// is tagged "low confidence (stale elements)". Mirrors the 3-day threshold of
// build_snapshot.py's median-age warning; normal event ages are ~1 d (p95
// 1.3-1.7 d across the archive), so this tags the genuinely stale tail only.
const STALE_ELEMENT_AGE_D = 3.0;

/** True if either participant's elements were older than STALE_ELEMENT_AGE_D
 *  when the snapshot was built. Unknown ages (pre-11.0 snapshot) are not stale. */
function isStaleEvent(e) {
  return [e.sat1_age_d, e.sat2_age_d].some(
    (a) => Number.isFinite(a) && a > STALE_ELEMENT_AGE_D);
}

// Populated by loadSnapshot(); consumed as globals by the other modules
// (matches the project's existing plain-globals style).
const satelliteMetadata = new Map(); // norad_id -> derived metadata (see below)
let snapshotMeta = null;             // snapshot.json "meta" block
let snapshotSatellites = [];         // raw OMM records (worker init payload)
let snapshotRawConjunctions = [];    // every screened event, raw, closest-first
let snapshotPersistentTotal = 0;     // persistent formations in the FULL snapshot

/** The adapted, capped conjunction set for a filter combination (see
 *  filterConjunctions). `total` counts every passing event in the full
 *  snapshot, so the header can say "closest N of TOTAL"; `hidden` is how many
 *  the filters removed. */
function conjunctionView(opts) {
  const v = filterConjunctions(snapshotRawConjunctions, opts, CONJ_DISPLAY_CAP);
  return {
    events: v.shown.map(_adaptConjunction),
    total: v.total,
    hidden: snapshotRawConjunctions.length - v.total,
  };
}

// Per-satellite satrec cache for main-thread single-sat computations (info
// panel, trails, TCA orb). json2satrec is the expensive call (~15 µs) — cache
// it; propagation from a cached satrec is nearly free.
const _ommById = new Map();
const _satrecById = new Map();

/**
 * Normalize an ISO timestamp to the strict ECMAScript form (exactly 3
 * fractional digits + explicit UTC designator). The snapshot carries 6-digit
 * microseconds (strict-OMM, required by python-sgp4's loader), but Safari's
 * native Date parser rejects anything but 3 digits — and satellite.js's
 * json2satrec parses EPOCH with `new Date` internally, so an un-normalized
 * snapshot would silently produce NaN epochs on all of WebKit/iOS.
 * Truncation costs < 1 ms of epoch (≤ ~8 m along-track at LEO speed) —
 * far below a screen pixel.
 */
function _isoToEcma(iso) {
  const m = iso.match(
    /^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$/);
  if (!m) return iso; // unexpected shape — let Date try as-is
  const frac = (m[2] || "").padEnd(3, "0").slice(0, 3);
  return `${m[1]}.${frac}${m[3] || "Z"}`; // naive (OMM EPOCH) → UTC
}

/** EPOCH ms since Unix epoch (EPOCH is normalized at load — see loadSnapshot). */
function _epochMs(omm) {
  return Date.parse(omm.EPOCH);
}

/** Orbit regime from period / perigee / eccentricity — mirrors the backend's
 *  screening_volumes classification (ecc gate, GEO period band, LEO ≤ 2000 km). */
function _regime(periapsis_km, period_min, ecc) {
  if (ecc >= 0.25) return "HEO";
  if (period_min > 1300 && period_min < 1800) return "GEO";
  if (periapsis_km > 2000) return "MEO";
  return "LEO";
}

/** Assign one mutually-exclusive display group. Priority: constellation by name
 *  first (Starlink / stations / GNSS), then fall back to orbit regime. */
function _classifyGroup(name, regime) {
  const n = (typeof name === "string" ? name : "").toUpperCase();
  if (n.includes("STARLINK")) return "starlink";
  if (_STATION_RE.test(n)) return "stations";
  if (_GNSS_RE.test(n) || regime === "MEO") return "navigation";
  if (regime === "GEO" || regime === "HEO") return "geo";
  return "leo";
}

/** Derive the display metadata the old /api/satellites endpoint provided.
 *  Same math as GPFetcher._derive_orbit_params (Kepler's 3rd law, WGS-72). */
function _deriveMetadata(omm) {
  const n_rad_s = (omm.MEAN_MOTION * 2.0 * Math.PI) / 86400.0;
  const a = Math.cbrt(GM_EARTH / (n_rad_s * n_rad_s)); // semimajor axis, km
  const period_min = 1440.0 / omm.MEAN_MOTION;
  const periapsis_km = a * (1.0 - omm.ECCENTRICITY) - R_EARTH;
  const regime = _regime(periapsis_km, period_min, omm.ECCENTRICITY);
  return {
    name: omm.OBJECT_NAME,
    object_type: omm.OBJECT_TYPE || "UNKNOWN",
    epoch: omm.EPOCH,
    epochMs: _epochMs(omm),
    period_min,
    inclination_deg: omm.INCLINATION,
    apoapsis_km: a * (1.0 + omm.ECCENTRICITY) - R_EARTH,
    periapsis_km,
    regime,
    group: _classifyGroup(omm.OBJECT_NAME, regime),
  };
}

/** Snapshot conjunction record → the field names the 9.1 UX code renders.
 *  (The snapshot schema is compact: a/b/miss_km/rtn_km — see snapshot.py.) */
function _adaptConjunction(c) {
  return {
    sat1_norad_id: c.a,
    sat2_norad_id: c.b,
    sat1_name: c.a_name,
    sat2_name: c.b_name,
    tca: _isoToEcma(c.tca), // Safari-safe form for every downstream Date parse
    miss_distance_km: c.miss_km,
    relative_speed_km_s: c.rel_speed_km_s,
    r_km: c.rtn_km[0],
    t_km: c.rtn_km[1],
    n_km: c.rtn_km[2],
    screening_regime: c.regime,
    // 9.10: what THIS encounter was predicted to be on earlier runs, as
    // [[epochSeconds, missKm], ...] oldest-first. Absent on ~two thirds of
    // events (a pair must survive with a stable TCA across runs), and absent
    // from EVERY event in a pre-9.10 snapshot — which a frontend-only redeploy
    // still serves, since that path reuses the live snapshot rather than
    // re-screening. Downstream must treat null as the ordinary case.
    history: c.hist || null,
    // 11.0 trust fields — all optional (absent in a pre-11.0 snapshot, which
    // the reuse deploy path still serves to new code). Strictly typed here so
    // the renderers can trust them: a non-number age is "unknown", not 0.
    sat1_age_d: Number.isFinite(c.age_a_d) ? c.age_a_d : null,
    sat2_age_d: Number.isFinite(c.age_b_d) ? c.age_b_d : null,
    beyond_horizon: c.beyond_horizon === true,
    persistent: c.persistent === true,
    runs: Number.isInteger(c.runs) ? c.runs : null,
  };
}

/** Load + index snapshot.json (relative path — works under a Pages subpath). */
const snapshotReady = (async () => {
  const resp = await fetch("snapshot.json");
  if (!resp.ok) {
    throw new Error(`snapshot.json: HTTP ${resp.status} — run scripts/build_snapshot.py`);
  }
  const snap = await resp.json();

  snapshotMeta = snap.meta;
  snapshotMeta.generated_at = _isoToEcma(snapshotMeta.generated_at);
  snapshotSatellites = snap.satellites;
  for (const omm of snap.satellites) {
    // Normalize BEFORE anything parses it — this record goes both to the
    // worker (satellite.js json2satrec → new Date) and the local satrec cache.
    omm.EPOCH = _isoToEcma(omm.EPOCH);
    _ommById.set(omm.NORAD_CAT_ID, omm);
    satelliteMetadata.set(omm.NORAD_CAT_ID, _deriveMetadata(omm));
  }
  // Events are closest-first in the snapshot (run_screen sorts by miss). The
  // display set is built per filter combination by conjunctionView(): filtered
  // BEFORE the CONJ_DISPLAY_CAP cap, so a filter shows the closest CAP events
  // that pass it — not just the few that made the overall top CAP.
  snapshotRawConjunctions = Array.isArray(snap.conjunctions) ? snap.conjunctions : [];
  snapshotPersistentTotal = snapshotRawConjunctions.filter(isPersistentFormation).length;

  console.log(
    `snapshot: ${snap.meta.n_satellites} satellites, ` +
    `${snap.meta.n_conjunctions} conjunctions ` +
    `(${snapshotPersistentTotal} persistent formations), ` +
    `generated ${snap.meta.generated_at}`
  );
  return snap;
})();

/** Satellite name for UI labels/panels (independent of whether a Cesium label
 *  exists — labels are created lazily at scale). */
/** Escape a string for an innerHTML template. Names and types come from the
 *  upstream catalogs (CelesTrak / Space-Track), so they are data, never markup —
 *  every template that interpolates one must pass it through here. (textContent
 *  sinks must NOT — they'd show a literal "&amp;".) */
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (ch) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
}

function getSatName(noradId) {
  const meta = satelliteMetadata.get(noradId);
  return meta ? meta.name : `NORAD ${noradId}`;
}

/** Cached satellite.js satrec for one object (main-thread single-sat ops).
 *  Returns null for unknown ids or records SGP4 rejects (callers null-check —
 *  same per-object tolerance as the worker's init). */
function getSatrec(noradId) {
  let satrec = _satrecById.get(noradId);
  if (satrec === undefined) {
    const omm = _ommById.get(noradId);
    if (!omm) return null;
    try {
      satrec = satellite.json2satrec(omm);
      if (satrec.error !== 0) satrec = null;
    } catch (err) {
      satrec = null;
    }
    _satrecById.set(noradId, satrec); // cache nulls too — don't re-throw per call
  }
  return satrec;
}

/**
 * Geodetic position + speed at a sim time — what /api/positions/{id} returned.
 * Returns {lat, lon, alt_km, speed_km_s, epoch_age_days} or null on
 * propagation failure (decayed / out-of-range elements).
 */
function computePositionGd(noradId, timeMs) {
  const satrec = getSatrec(noradId);
  if (!satrec) return null;
  const date = new Date(timeMs);
  const pv = satellite.propagate(satrec, date);
  if (!pv || !pv.position) return null;
  const gmst = satellite.gstime(date);
  const gd = satellite.eciToGeodetic(pv.position, gmst);
  const v = pv.velocity;
  const meta = satelliteMetadata.get(noradId);
  return {
    lat: satellite.degreesLat(gd.latitude),
    lon: satellite.degreesLong(gd.longitude),
    alt_km: gd.height,
    speed_km_s: Math.hypot(v.x, v.y, v.z),
    epoch_age_days: meta ? (timeMs - meta.epochMs) / 86400000 : 0,
  };
}

/** ECEF position (Cesium Cartesian3, meters) at a sim time — for placing the
 *  conjunction orb at TCA. Returns null on propagation failure. */
function computeEcefAt(noradId, timeMs) {
  const satrec = getSatrec(noradId);
  if (!satrec) return null;
  const date = new Date(timeMs);
  const pv = satellite.propagate(satrec, date);
  if (!pv || !pv.position) return null;
  const e = satellite.eciToEcf(pv.position, satellite.gstime(date));
  return new Cesium.Cartesian3(e.x * 1000, e.y * 1000, e.z * 1000);
}

/**
 * TEME orbit track (Cesium Cartesian3, meters) — replaces the old
 * /api/positions/{id}/track fetch. TEME is inertial, so the trail is a clean
 * near-ellipse; the callers rotate it to ECEF with ONE GMST angle
 * (computeGmst in info-panel.js), exactly as before.
 */
function computeTrackTEME(noradId, startMs, durationMin, steps) {
  const satrec = getSatrec(noradId);
  if (!satrec) return [];
  const stepMs = (durationMin * 60000) / steps;
  const pts = [];
  for (let i = 0; i <= steps; i++) {
    const pv = satellite.propagate(satrec, new Date(startMs + i * stepMs));
    if (pv && pv.position) {
      pts.push(new Cesium.Cartesian3(
        pv.position.x * 1000, pv.position.y * 1000, pv.position.z * 1000));
    }
  }
  return pts;
}
