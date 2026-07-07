/**
 * OrbitWatch — Satellite point rendering with smooth interpolation (Phase 9.3:
 * snapshot-driven, no backend).
 *
 * Positions come from js/propagation-worker.js (in-browser satellite.js SGP4
 * over the snapshot's OMM elements) at a speed-adaptive cadence, and the
 * preRender loop smoothly interpolates between batches — same lerp
 * architecture as the old /api/positions poller, with the fetch swapped for a
 * worker round-trip. Points + labels rendered via GPU-batched collections.
 *
 * Labels are LAZY at scale: PointPrimitiveCollection handles ~11k points in
 * one draw call, but LabelCollection rasterizes glyphs per label and does not.
 * At ≤ LABELS_ALL_MAX satellites every point gets a label (the cozy demo
 * look); above it, labels exist only for the selected satellite and
 * conjunction pairs (created on demand via ensureLabel).
 *
 * Depends on: viewer (app.js), simClock (clock.js),
 *   snapshotReady + snapshotSatellites + satelliteMetadata (snapshot-data.js)
 */

const BASE_REFRESH_MS = 5000;
const MIN_REFRESH_MS = 500;  // floor — keeps the sim-time gap between batches small at speed
const LERP_FRAME_MS = 50;    // ~20fps interpolation — saves CPU at scale
const LABELS_ALL_MAX = 400;  // ≤ this many sats → label everything up front

/**
 * Effective refresh interval — scales inversely with clock speed so the
 * simulated time gap between position batches stays small enough for accurate
 * lerp. At 1x: 5000ms (5s sim gap). At 10x: 500ms (5s sim gap).
 */
function getRefreshInterval() {
  return Math.max(Math.floor(BASE_REFRESH_MS / simClock.getSpeed()), MIN_REFRESH_MS);
}

// GPU-batched collections — single draw call each; the 11k-point path.
const pointCollection = viewer.scene.primitives.add(
  new Cesium.PointPrimitiveCollection()
);
const labelCollection = viewer.scene.primitives.add(
  new Cesium.LabelCollection()
);

// Per-satellite render state.
// Map<norad_id, { point, label|null, start, target, ok }>
//   point:  PointPrimitive reference
//   label:  Label reference, or null until ensureLabel() (lazy at scale)
//   start:  Cartesian3 at previous batch
//   target: Cartesian3 at current batch
//   ok:     last batch propagated successfully (false → point hidden)
const satellites = new Map();
// Same entries in worker-index order — the hot lerp loop iterates this array.
let satOrder = [];

// Interpolation progress: 0 (at start position) → 1 (at target position).
let lerpFactor = 0;
let lastFetchTime = 0;
let lastLerpTime = 0;
let lastBatchSimMs = null; // sim time of the previous batch (time-jump detection)

// Worker plumbing — one in-flight batch at a time (the old fetchInFlight).
let propWorker = null;
let workerBusy = false;

const LABEL_STYLE = {
  font: "13px monospace",
  fillColor: Cesium.Color.WHITE,
  style: Cesium.LabelStyle.FILL,
  pixelOffset: new Cesium.Cartesian2(10, -4),
  showBackground: true,
  backgroundColor: new Cesium.Color(0, 0, 0, 0.6),
  backgroundPadding: new Cesium.Cartesian2(4, 2),
  // Fade out distant labels instead of shrinking them
  translucencyByDistance: new Cesium.NearFarScalar(5e6, 1.0, 1.5e7, 0.0),
};

/** Create the label for one satellite if it doesn't exist yet (lazy at scale).
 *  Called by info-panel.js (selection) and conjunctions.js (pair labels). */
function ensureLabel(noradId) {
  const entry = satellites.get(noradId);
  if (!entry) return null;
  if (!entry.label) {
    entry.label = labelCollection.add({
      position: entry.point.position,
      text: getSatName(noradId),
      ...LABEL_STYLE,
    });
    // Respect current visibility state (type filters + label toggle)
    if (typeof applyVisibilityState === "function") applyVisibilityState();
  }
  return entry.label;
}

// Scratch Cartesian3 — reused each frame to avoid GC pressure.
// Safe: Cesium's position setter copies the value, not the reference.
const scratchCartesian = new Cesium.Cartesian3();

/**
 * Process a fresh position batch from the worker.
 *
 * First batch: creates points (+ labels when the catalog is small).
 * Later batches: shifts target positions for interpolation. A large sim-time
 * jump (conjunction fly-to, LIVE button) snaps instead of sweeping dots
 * across the globe for one refresh cycle.
 */
function updatePositions(batch) {
  const { positions, ok, timeMs } = batch;
  const firstBatch = satOrder.length === 0;
  const labelAll = snapshotSatellites.length <= LABELS_ALL_MAX;

  // Time jump = sim time moved far more than the expected batch gap.
  const expectedGapMs = getRefreshInterval() * simClock.getSpeed();
  const snap = lastBatchSimMs !== null &&
    Math.abs(timeMs - lastBatchSimMs) > 3 * Math.max(expectedGapMs, 5000);
  lastBatchSimMs = timeMs;

  for (let i = 0; i < snapshotSatellites.length; i++) {
    scratchCartesian.x = positions[3 * i];
    scratchCartesian.y = positions[3 * i + 1];
    scratchCartesian.z = positions[3 * i + 2];

    let entry = satOrder[i];
    if (firstBatch) {
      const noradId = snapshotSatellites[i].NORAD_CAT_ID;
      entry = {
        point: pointCollection.add({
          position: scratchCartesian,
          pixelSize: 6,
          color: Cesium.Color.RED,
          id: noradId,
        }),
        label: null,
        start: Cesium.Cartesian3.clone(scratchCartesian),
        target: Cesium.Cartesian3.clone(scratchCartesian),
        ok: ok[i] === 1,
      };
      satellites.set(noradId, entry);
      satOrder.push(entry);
      if (labelAll) {
        entry.label = labelCollection.add({
          position: scratchCartesian,
          text: getSatName(noradId),
          ...LABEL_STYLE,
        });
      }
    } else if (ok[i] === 1) {
      // Shift: current target becomes start, fresh batch becomes target.
      // Snap (start = fresh) on a big time jump, or when recovering from a
      // failed batch — the stale start would sweep the dot across the globe.
      if (snap || !entry.ok) {
        Cesium.Cartesian3.clone(scratchCartesian, entry.start);
      } else {
        Cesium.Cartesian3.clone(entry.target, entry.start);
      }
      Cesium.Cartesian3.clone(scratchCartesian, entry.target);
      entry.ok = true;
    } else {
      // Propagation failed this batch (worker zero-fills the slot): keep the
      // last good start/target — the point is hidden via the ok mask, and
      // must not lerp toward the (0,0,0) placeholder.
      entry.ok = false;
    }
  }

  // Reset interpolation for this refresh cycle
  lerpFactor = 0;
  lastFetchTime = performance.now();

  // Re-apply display toggles (controls.js) — also folds in the ok mask
  if (typeof applyVisibilityState === "function") applyVisibilityState();
}

/**
 * preRender callback — interpolates satellite positions at ~20fps.
 * Throttled to avoid unnecessary work; bump LERP_FRAME_MS to increase.
 */
function onPreRender() {
  if (satOrder.length === 0) return;
  if (simClock.isPaused()) return; // freeze positions while paused

  // Throttle: skip frame if less than LERP_FRAME_MS since last update
  const now = performance.now();
  if (now - lastLerpTime < LERP_FRAME_MS) return;
  lastLerpTime = now;

  // Advance lerp factor based on elapsed time since last batch.
  // Denominator must match the refresh interval so lerp reaches 1.0 just as
  // the next batch arrives.
  const elapsed = now - lastFetchTime;
  lerpFactor = Math.min(elapsed / getRefreshInterval(), 1.0);

  for (let i = 0; i < satOrder.length; i++) {
    const entry = satOrder[i];
    Cesium.Cartesian3.lerp(entry.start, entry.target, lerpFactor, scratchCartesian);
    entry.point.position = scratchCartesian;
    if (entry.label) entry.label.position = scratchCartesian;
  }
}

viewer.scene.preRender.addEventListener(onPreRender);

/**
 * Request a fresh position batch from the worker at the current sim time.
 * Skips if the previous batch is still computing (worker answers in ~13 ms at
 * 11k, so this almost never trips).
 */
function refreshSatellites() {
  if (!propWorker || workerBusy) return;
  if (simClock.isPaused()) return; // no work while paused — positions frozen
  workerBusy = true;
  propWorker.postMessage({ type: "compute", timeMs: simClock.getTimeMs() });
}

// --- Startup: load snapshot → spin up the worker → start the refresh loop ---

snapshotReady
  .then(() => {
    propWorker = new Worker("js/propagation-worker.js");
    propWorker.onmessage = (e) => {
      const msg = e.data;
      if (msg.type === "ready") {
        workerBusy = false;
        refreshSatellites(); // first batch
      } else if (msg.type === "positions") {
        updatePositions(msg);
        workerBusy = false;
      }
    };
    propWorker.onerror = (err) => {
      console.error("propagation worker error:", err.message || err);
      workerBusy = false;
    };
    workerBusy = true; // until 'ready'
    propWorker.postMessage({ type: "init", satellites: snapshotSatellites });

    // Self-scheduling loop — re-evaluates interval each cycle so it adapts
    // to speed changes.
    (function scheduleRefresh() {
      setTimeout(() => {
        refreshSatellites();
        scheduleRefresh();
      }, getRefreshInterval());
    })();
  })
  .catch((err) => {
    console.error("Failed to load snapshot:", err);
    document.getElementById("cesiumContainer").insertAdjacentHTML(
      "beforeend",
      '<p style="position:absolute;top:40%;width:100%;text-align:center;' +
      'color:white;font-family:monospace;z-index:10;">' +
      "Could not load snapshot.json — build it with " +
      "<code>python scripts/build_snapshot.py</code></p>"
    );
  });
