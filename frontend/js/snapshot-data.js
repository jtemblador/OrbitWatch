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
 *   • snapshotConjunctions — events adapted to the field names the UX code uses
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

// Populated by loadSnapshot(); consumed as globals by the other modules
// (matches the project's existing plain-globals style).
const satelliteMetadata = new Map(); // norad_id -> derived metadata (see below)
let snapshotMeta = null;             // snapshot.json "meta" block
let snapshotSatellites = [];         // raw OMM records (worker init payload)
let snapshotConjunctions = [];       // adapted events, closest-first (capped)

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
  // Cap to the closest CAP events for display (see CONJ_DISPLAY_CAP above).
  // Events are closest-first in the snapshot (run_screen sorts by miss).
  snapshotConjunctions =
    snap.conjunctions.slice(0, CONJ_DISPLAY_CAP).map(_adaptConjunction);

  console.log(
    `snapshot: ${snap.meta.n_satellites} satellites, ` +
    `${snap.meta.n_conjunctions} conjunctions ` +
    `(displaying closest ${snapshotConjunctions.length}), ` +
    `generated ${snap.meta.generated_at}`
  );
  return snap;
})();

/** Satellite name for UI labels/panels (independent of whether a Cesium label
 *  exists — labels are created lazily at scale). */
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
