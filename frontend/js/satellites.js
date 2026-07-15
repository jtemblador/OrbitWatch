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
// Animation cap. Was Cesium's uncapped ~60, then 30 (the 9.4 heat fix); 15 fps
// since the 10.6 full-catalog lift — dots drift a few pixels between batches,
// so 15 fps lerp is visually indistinguishable while halving per-frame work
// (position sets + full-scene renders), the main-thread cost that scales with
// the participant count.
const TARGET_FPS = 15;
const FRAME_MS = 1000 / TARGET_FPS;
const LABELS_ALL_MAX = 400;  // ≤ this many sats → label everything up front

/**
 * Effective refresh interval — scales inversely with clock speed so the
 * simulated time gap between position batches stays small enough for accurate
 * lerp. At 1x: 5000ms (5s sim gap). At 10x: 500ms (5s sim gap). |speed|:
 * rewind (negative speeds) needs the same cadence as forward — a raw division
 * would go negative and pin the interval at the floor.
 */
function getRefreshInterval() {
  const spd = Math.abs(simClock.getSpeed()) || 1;
  return Math.max(Math.floor(BASE_REFRESH_MS / spd), MIN_REFRESH_MS);
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
let _tick = 0;             // frame counter — staggers occluded-point updates

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

// Group key -> Cesium point color (from SAT_GROUPS in snapshot-data.js), built
// once so we don't re-parse the CSS hex per point.
const GROUP_COLOR = new Map(
  (typeof SAT_GROUPS !== "undefined" ? SAT_GROUPS : []).map(
    (g) => [g.key, Cesium.Color.fromCssColorString(g.color)]
  )
);
const DEFAULT_COLOR = Cesium.Color.fromCssColorString("#ff5a4a");

function groupColor(noradId) {
  const meta = satelliteMetadata.get(noradId);
  return (meta && GROUP_COLOR.get(meta.group)) || DEFAULT_COLOR;
}

// --- Unified point emphasis (10.6 UX): ONE source of truth for a point's
// size/outline so hover, the selection ring, and the conjunction-pair
// highlight never overwrite each other. Priority: hover > selected/pair >
// plain. Reads state owned by info-panel.js (selectionIndicator) and
// conjunctions.js (focusedPair) at call time — both loaded by then.
const _HOVER_STYLE = { pixelSize: 11, outlineColor: Cesium.Color.WHITE, outlineWidth: 2 };
const _EMPH_STYLE  = { pixelSize: 10, outlineColor: Cesium.Color.CYAN,  outlineWidth: 3 };
const _PLAIN_STYLE = { pixelSize: 6,  outlineColor: Cesium.Color.TRANSPARENT, outlineWidth: 0 };
let hoveredNoradId = null;

function _isEmphasized(noradId) {
  if (typeof selectionIndicator !== "undefined" && selectionIndicator === noradId) return true;
  if (typeof focusedPair !== "undefined" && focusedPair &&
      (focusedPair[0] === noradId || focusedPair[1] === noradId)) return true;
  return false;
}

/** Re-apply the correct emphasis to one point from current state. */
function refreshPointStyle(noradId) {
  const entry = satellites.get(noradId);
  if (!entry) return;
  const s = noradId === hoveredNoradId ? _HOVER_STYLE
          : _isEmphasized(noradId)     ? _EMPH_STYLE
          :                              _PLAIN_STYLE;
  entry.point.pixelSize = s.pixelSize;
  entry.point.outlineColor = s.outlineColor;
  entry.point.outlineWidth = s.outlineWidth;
}

/** Create one satellite's label if missing (no side effects — safe to call
 *  from inside applyVisibilityState, which owns .show). */
function _createLabel(noradId) {
  const entry = satellites.get(noradId);
  if (!entry) return null;
  if (!entry.label) {
    entry.label = labelCollection.add({
      position: entry.point.position,
      text: getSatName(noradId),
      ...LABEL_STYLE,
    });
  }
  return entry.label;
}

/** Create the label for one satellite if it doesn't exist yet (lazy at scale),
 *  then re-apply visibility. Called by info-panel.js / conjunctions.js. */
function ensureLabel(noradId) {
  const label = _createLabel(noradId);
  if (label && typeof applyVisibilityState === "function") applyVisibilityState();
  return label;
}

/** Label policy (10.6): label every satellite in an isolated/focused context so
 *  the handful on screen are identifiable; keep the bulk views (startup "All",
 *  group filters) label-free — LabelCollection rasterizes per glyph and isn't
 *  as cheap as the point batch. Replaces the old manual Labels toggle. */
function _wantsLabel(noradId) {
  // Small catalog: the cozy demo look labels everything (unchanged pre-10.6).
  if (snapshotSatellites.length <= LABELS_ALL_MAX) return true;
  if (typeof isolationSet !== "undefined" && isolationSet !== null) {
    return isolationSet.has(noradId);
  }
  if (typeof selectionIndicator !== "undefined" && selectionIndicator === noradId) return true;
  if (typeof focusedPair !== "undefined" && focusedPair &&
      (focusedPair[0] === noradId || focusedPair[1] === noradId)) return true;
  return false;
}

// Scratch Cartesian3 — reused each frame to avoid GC pressure.
// Safe: Cesium's position setter copies the value, not the reference.
const scratchCartesian = new Cesium.Cartesian3();

// Earth-occlusion tester for the animation loop (10.6 full-catalog perf):
// points behind the globe are already invisible (GPU depth test), so paying
// the Cesium position-setter + label update + vertex upload for them every
// frame is pure waste — roughly half the participants at any moment. Reused
// instance; the camera position is refreshed once per frame.
const _occluder = new Cesium.EllipsoidalOccluder(
  Cesium.Ellipsoid.WGS84, new Cesium.Cartesian3(1, 0, 0));

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
  // |speed|: when rewinding, sim time legitimately moves backward by the gap.
  const expectedGapMs = getRefreshInterval() * Math.abs(simClock.getSpeed());
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
          color: groupColor(noradId), // colored by display group (9.x)
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

// --- Tab visibility: fully idle when the page isn't being looked at ---
// A hidden tab shouldn't burn CPU/GPU. The browser already throttles rAF in
// background tabs; this makes it explicit (no lerp, no render, no propagation)
// and catches positions up on return.
let tabVisible = !document.hidden;
document.addEventListener("visibilitychange", () => {
  tabVisible = !document.hidden;
  if (tabVisible) {
    lastFetchTime = performance.now(); // restart the lerp cleanly
    refreshSatellites();               // catch up to the current sim time
  }
});

/**
 * Animation driver — a self-throttled ~30fps requestAnimationFrame loop that
 * advances the lerp for VISIBLE satellites and asks Cesium to draw the frame.
 *
 * The viewer runs in requestRenderMode (app.js), so the GPU only draws when we
 * call scene.requestRender(). This loop stops requesting whenever the clock is
 * PAUSED or the tab is HIDDEN → the GPU goes idle (the fan-noise fix). Camera
 * drags still redraw on their own via Cesium. Hidden satellites (a filtered-off
 * group, or a sat that failed propagation) are skipped entirely — that's what
 * makes a filter toggle stop real per-frame work.
 */
function animationTick(now) {
  requestAnimationFrame(animationTick);
  if (satOrder.length === 0) return;
  if (simClock.isPaused() || !tabVisible) return; // idle: no work, no draw

  if (now - lastLerpTime < FRAME_MS) return; // cap at TARGET_FPS
  lastLerpTime = now;

  // Advance lerp factor based on elapsed time since the last batch, so it
  // reaches 1.0 just as the next batch arrives.
  const elapsed = now - lastFetchTime;
  lerpFactor = Math.min(elapsed / getRefreshInterval(), 1.0);

  // Refresh the occlusion tester with this frame's camera position.
  _occluder.cameraPosition = viewer.scene.camera.positionWC;
  _tick++;

  let moved = false;
  for (let i = 0; i < satOrder.length; i++) {
    const entry = satOrder[i];
    if (!entry.point.show) continue; // hidden group / failed sat — no work
    Cesium.Cartesian3.lerp(entry.start, entry.target, lerpFactor, scratchCartesian);
    // Occlusion throttle: a point behind the Earth is invisible (GPU depth
    // test), so update it at 1/4 rate instead of every tick — staggered by
    // index so the work spreads evenly. Not a full skip: its rendered position
    // then stays ≤ ~4 ticks stale, so a camera rotation can never reveal a
    // ghost dot at a long-stale spot. The test uses the FRESH lerped position
    // (never the stale rendered one), so a sat emerging from behind the limb
    // updates on its first visible frame.
    if (!_occluder.isPointVisible(scratchCartesian) && ((_tick + i) & 3) !== 0) {
      continue;
    }
    entry.point.position = scratchCartesian;
    if (entry.label) entry.label.position = scratchCartesian;
    moved = true;
  }
  // Only ask Cesium to redraw if something visible actually moved. An empty globe
  // (all filters off, nothing selected) then costs ZERO GPU while playing, rather
  // than re-rendering the whole scene at 30fps for nothing — the Chrome fan/heat
  // issue. When dots are visible they move every frame, so this always renders.
  if (moved) viewer.scene.requestRender();
}
requestAnimationFrame(animationTick);

// --- Hover emphasis: the point under the cursor gets the hover ring + a
// pointer cursor, so it's clear what a click will select. Own handler (the
// LEFT_CLICK one lives in info-panel.js); acts only on a change of target. ---
const _hoverHandler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);
_hoverHandler.setInputAction((move) => {
  const picked = viewer.scene.pick(move.endPosition);
  const id = (Cesium.defined(picked) && Cesium.defined(picked.primitive) &&
              picked.primitive.id !== undefined && satellites.has(picked.primitive.id) &&
              satellites.get(picked.primitive.id).point.show)
    ? picked.primitive.id : null;
  if (id === hoveredNoradId) return;
  const prev = hoveredNoradId;
  hoveredNoradId = id;
  if (prev !== null) refreshPointStyle(prev);
  if (id !== null) refreshPointStyle(id);
  viewer.scene.canvas.style.cursor = id !== null ? "pointer" : "";
  viewer.scene.requestRender();
}, Cesium.ScreenSpaceEventType.MOUSE_MOVE);

// Cached "which sats are conjunction participants" mask, in worker-index order.
// Built once (participants don't change) and reused to skip non-participant
// propagation in the "All" conjunction view.
let _participantMask = null;
function participantMask() {
  if (_participantMask) return _participantMask;
  const n = snapshotSatellites.length;
  const mask = new Uint8Array(n);
  if (typeof isConjunctionParticipant === "function") {
    for (let i = 0; i < n; i++) {
      if (isConjunctionParticipant(snapshotSatellites[i].NORAD_CAT_ID)) mask[i] = 1;
    }
  }
  _participantMask = mask;
  return mask;
}

// Focus-isolation worker mask (conjunctions.js sets isolationSet): propagate
// ONLY the isolated satellites — the big CPU cut behind the isolation UX.
// Cached per Set identity (each isolateSats() call makes a new Set).
let _isoMaskSrc = null;
let _isoMask = null;
function isolationMask() {
  if (_isoMaskSrc === isolationSet && _isoMask) return _isoMask;
  const n = snapshotSatellites.length;
  const mask = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (isolationSet.has(snapshotSatellites[i].NORAD_CAT_ID)) mask[i] = 1;
  }
  _isoMaskSrc = isolationSet;
  _isoMask = mask;
  return mask;
}

/**
 * Request a fresh position batch from the worker at the current sim time.
 * Skips if the previous batch is still computing (worker answers in ~25 ms at
 * 5k, so this almost never trips), or while paused / tab hidden (no point
 * propagating frozen or unseen positions).
 *
 * In the "All" conjunction view, sends a participant mask so the worker skips
 * the thousands of non-participant sats entirely — they cost nothing in the
 * background (the real fix for the All-view lag).
 */
// A forced refresh that arrives while a batch is in flight must not be lost —
// while PAUSED nothing else will re-request it (review round 2: two mode
// toggles within the worker's ~25 ms roundtrip). Remember it; the positions
// handler re-fires it as soon as the in-flight batch lands.
let pendingForcedRefresh = false;

function refreshSatellites(force) {
  if (!propWorker || workerBusy) {
    if (force) pendingForcedRefresh = true;
    return;
  }
  // While paused, positions don't change → normally skip. `force` overrides for
  // MODE changes (enter/exit the "All" conjunction view): the mask makes masked-
  // out sats ok=false, so exiting the mode while paused needs one unmasked batch
  // at the frozen sim time or re-enabled groups would stay invisible until
  // unpause (the batch itself is valid at any fixed time).
  if ((simClock.isPaused() && !force) || !tabVisible) return;
  workerBusy = true;
  const msg = { type: "compute", timeMs: simClock.getTimeMs() };
  if (typeof isolationSet !== "undefined" && isolationSet !== null) {
    // Focus isolation: propagate ONLY the involved satellites (~2–10) —
    // takes precedence over the All-view participant mask.
    msg.mask = isolationMask();
  } else if (typeof conjOnlyActive !== "undefined" && conjOnlyActive === "all") {
    msg.mask = participantMask();
  }
  propWorker.postMessage(msg);
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
        if (pendingForcedRefresh) {   // a mode change arrived mid-batch — honor it
          pendingForcedRefresh = false;
          refreshSatellites(true);
        }
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
