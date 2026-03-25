/**
 * OrbitWatch — Satellite point rendering with smooth interpolation.
 *
 * Fetches positions from /api/positions every 5 seconds and smoothly
 * interpolates between known positions using preRender callbacks.
 * Points + name labels rendered via GPU-batched collections.
 *
 * Depends on: viewer (global, from app.js)
 */

const REFRESH_INTERVAL_MS = 5000;
const LERP_FRAME_MS = 50; // ~20fps interpolation — saves CPU at scale, bump later if needed

// GPU-batched collections — single draw call each, scales to Phase 3 (6K sats).
const pointCollection = viewer.scene.primitives.add(
  new Cesium.PointPrimitiveCollection()
);
const labelCollection = viewer.scene.primitives.add(
  new Cesium.LabelCollection()
);

// Per-satellite state for rendering + interpolation.
// Map<norad_id, { point, label, start, target }>
//   point:  PointPrimitive reference
//   label:  Label reference
//   start:  Cartesian3 at last fetch
//   target: Cartesian3 at current fetch
const satellites = new Map();

// Interpolation progress: 0 (at start position) → 1 (at target position).
let lerpFactor = 0;
let lastFetchTime = 0;
let lastLerpTime = 0;

// Guard against overlapping fetches.
let fetchInFlight = false;

// Scratch Cartesian3 — reused each frame to avoid GC pressure.
// Safe: Cesium's position setter copies the value, not the reference.
const scratchCartesian = new Cesium.Cartesian3();

/**
 * Fetch all satellite positions from the API.
 */
async function fetchPositions() {
  try {
    const resp = await fetch("/api/positions");
    if (!resp.ok) {
      console.error("Failed to fetch positions:", resp.status);
      return null;
    }
    const data = await resp.json();
    return data.positions;
  } catch (err) {
    console.error("Failed to fetch positions:", err);
    return null;
  }
}

/**
 * Convert API position to Cesium Cartesian3.
 * API returns alt_km; Cesium expects meters.
 */
function toCartesian(pos) {
  return Cesium.Cartesian3.fromDegrees(pos.lon, pos.lat, pos.alt_km * 1000);
}

/**
 * Process a fresh batch of positions from the API.
 *
 * First call: creates points + labels.
 * Subsequent calls: shifts target positions for interpolation.
 */
function updatePositions(positions) {
  for (const pos of positions) {
    const cartesian = toCartesian(pos);
    const entry = satellites.get(pos.norad_id);

    if (entry) {
      // Shift: current interpolated position becomes start, new fetch becomes target
      Cesium.Cartesian3.clone(entry.target, entry.start);
      Cesium.Cartesian3.clone(cartesian, entry.target);
    } else {
      // First time seeing this satellite — create point + label
      const point = pointCollection.add({
        position: cartesian,
        pixelSize: 6,
        color: Cesium.Color.RED,
        id: pos.norad_id,
      });

      const label = labelCollection.add({
        position: cartesian,
        text: pos.name,
        font: "13px monospace",
        fillColor: Cesium.Color.WHITE,
        style: Cesium.LabelStyle.FILL,
        pixelOffset: new Cesium.Cartesian2(10, -4),
        showBackground: true,
        backgroundColor: new Cesium.Color(0, 0, 0, 0.6),
        backgroundPadding: new Cesium.Cartesian2(4, 2),
        // Fade out distant labels instead of shrinking them
        translucencyByDistance: new Cesium.NearFarScalar(5e6, 1.0, 1.5e7, 0.0),
      });

      satellites.set(pos.norad_id, {
        point,
        label,
        start: Cesium.Cartesian3.clone(cartesian),
        target: Cesium.Cartesian3.clone(cartesian),
      });
    }
  }

  // Reset interpolation for this refresh cycle
  lerpFactor = 0;
  lastFetchTime = performance.now();
}

/**
 * preRender callback — interpolates satellite positions at ~20fps.
 * Throttled to avoid unnecessary work; bump LERP_FRAME_MS to increase.
 */
function onPreRender() {
  if (satellites.size === 0) return;

  // Throttle: skip frame if less than LERP_FRAME_MS since last update
  const now = performance.now();
  if (now - lastLerpTime < LERP_FRAME_MS) return;
  lastLerpTime = now;

  // Advance lerp factor based on elapsed time since last fetch
  const elapsed = now - lastFetchTime;
  lerpFactor = Math.min(elapsed / REFRESH_INTERVAL_MS, 1.0);

  for (const entry of satellites.values()) {
    Cesium.Cartesian3.lerp(entry.start, entry.target, lerpFactor, scratchCartesian);
    entry.point.position = scratchCartesian;
    entry.label.position = scratchCartesian;
  }
}

viewer.scene.preRender.addEventListener(onPreRender);

/**
 * Fetch + update cycle. Skips if a previous fetch is still in-flight.
 */
async function refreshSatellites() {
  if (fetchInFlight) return;
  fetchInFlight = true;
  try {
    const positions = await fetchPositions();
    if (positions) {
      updatePositions(positions);
    }
  } finally {
    fetchInFlight = false;
  }
}

// --- Startup ---
refreshSatellites();
setInterval(refreshSatellites, REFRESH_INTERVAL_MS);
