/**
 * OrbitWatch — Conjunction visualization (Phase 9.1 UX pass; 9.3 snapshot-driven).
 *
 * Renders the snapshot's pre-computed conjunction list (the heavy SFS screen
 * runs offline in scripts/build_snapshot.py) and makes it interactive:
 *   • top-left list of the closest approaches (click a row → focus it)
 *   • selecting a satellite shows ITS conjunctions in a bottom-right panel
 *   • focusing a conjunction fast-travels the clock to TCA, flies the camera
 *     to the close-approach point, and drops a marker there — so the two
 *     satellites visibly converge (instead of a straight line through Earth)
 *   • header shows "<N> conjunctions · <M> satellites" + a freshness line
 *     ("updated X ago" from meta.generated_at) linking the validation report
 *
 * Depends on: viewer (app.js), simClock (clock.js), snapshotReady +
 *   snapshotConjunctions + compute helpers (snapshot-data.js), satellites +
 *   ensureLabel (satellites.js). Loaded last (after info-panel.js).
 */

// The screen already applied the SFS per-regime RTN ellipsoids, suppressed
// co-located / docked clusters, and de-duped to unique pairs — the client just
// renders its events (no client-side threshold or min-miss floor needed).
const CONJ_LIST_MAX = 20;                   // closest N shown in the top-left list
const VALIDATION_REPORT_URL =
  "https://github.com/jtemblador/OrbitWatch/blob/main/validation/socrates_report.md";
const CONJ_COLOR = Cesium.Color.ORANGE;
// Start the clock this far BEFORE TCA (at 1x) so the full approach + closest
// pass + separation play out on screen, rather than snapping to the instant.
const CONJ_LEAD_MIN = 5;
const ORB_RADIUS_M = 75000;                // yellow "conjunction area" orb
const TRAIL_BLUE = Cesium.Color.fromCssColorString("#4fc3f7").withAlpha(0.35);

// Group CSS hex per satellite (same colors as the globe dots + filter swatches)
// so a list row reads as "<orange Starlink> × <blue Other-LEO>".
const GROUP_CSS = new Map(
  (typeof SAT_GROUPS !== "undefined" ? SAT_GROUPS : []).map((g) => [g.key, g.color]));
function groupCss(noradId) {
  const meta = (typeof satelliteMetadata !== "undefined")
    ? satelliteMetadata.get(noradId) : null;
  return (meta && GROUP_CSS.get(meta.group)) || "#e0e0e0";
}
/** A satellite name span tinted by its display group (for the conjunction list). */
function nameSpan(noradId, name) {
  return `<span class="cj-name" style="color:${groupCss(noradId)}">${name}</span>`;
}

/** Miss-distance → threat color: red at ≤0.1 km, orange ~0.5 km, yellow ≥0.9 km.
 *  Closer approach = redder = more alarming. Hue 0 (red) → 55 (yellow). */
function missColor(km) {
  const t = Math.min(Math.max((km - 0.1) / 0.8, 0), 1); // 0.1→0, 0.9→1
  return `hsl(${(t * 55).toFixed(0)}, 100%, 58%)`;
}

// State
let conjEvents = [];                       // meaningful events, closest-first
let conjTotalCount = 0;                    // unique at-risk pairs (from meta)
let conjListShown = CONJ_LIST_MAX;         // rows shown in the list (grown by "Show more")
const conjByNorad = new Map();             // norad_id -> [events involving it]
const conjParticipants = new Set();        // every norad_id that's in ≥1 conjunction

/** True if this satellite takes part in any conjunction (drives the "All" view). */
function isConjunctionParticipant(noradId) {
  return conjParticipants.has(noradId);
}
// --- Focus isolation (10.6 UX round): while a conjunction/selection is in
// focus, ONLY the involved satellites exist on screen — every other dot is
// hidden AND excluded from the worker's propagation batch, so a focused view
// costs a handful of satellites of CPU instead of hundreds. null = off.
// Cleared by clearConjunctionVisuals (Escape / click-away / LIVE / mode switch),
// which restores the previous view (group filters, conjunction-only modes).
let isolationSet = null;

// Zoom-level state (10.6 UX): the view is a 3-level stack that Escape walks up.
//   All Conjunctions  — startup, every participant (no selection, no isolation)
//   Conjunction View  — one satellite + all its conjunction partners (selected)
//   TCA View          — zoomed to ONE approach (clock jumped, camera flown in)
// tcaZoomed distinguishes the deepest level; selectedNoradId (info-panel.js)
// distinguishes the middle from the top. Escape: TCA→Conjunction→All.
let tcaZoomed = false;

// POV indicator (top-center, under the search bar): names the current level and
// what Escape does next, so the zoom stack is legible.
const povEl = document.createElement("div");
povEl.id = "pov-indicator";
document.body.appendChild(povEl);

function updatePovIndicator() {
  const selName = (typeof selectedNoradId !== "undefined" && selectedNoradId !== null)
    ? getSatName(selectedNoradId) : null;
  // The top of the stack depends on the mode: "All Conjunctions" only when the
  // conjunction-only view is actually on; in plain browse mode it's satellites.
  const conjMode = !!(typeof conjOnlyActive !== "undefined" && conjOnlyActive);
  const top = conjMode ? "All Conjunctions" : "All Satellites";
  let html = "";

  if (tcaZoomed && focusedPair) {
    const up = selName ? "Conjunction View" : top;
    html =
      `<span class="pov-level">TCA View</span>` +
      `<span class="pov-ctx">${getSatName(focusedPair[0])} × ${getSatName(focusedPair[1])}</span>` +
      `<span class="pov-esc">Esc → ${up}</span>`;
  } else if (selName) {
    // A satellite is selected — name it. If its conjunction partners are also
    // isolated on screen it's a "Conjunction View"; otherwise just the satellite
    // and its orbit. Either way the badge shows the satellite, not "All …".
    const hasPartners = (typeof isolationSet !== "undefined"
      && isolationSet !== null && isolationSet.size > 1);
    html =
      `<span class="pov-level">${hasPartners ? "Conjunction View" : "Satellite"}</span>` +
      `<span class="pov-ctx">${selName}</span>` +
      `<span class="pov-esc">Esc → ${top}</span>`;
  } else if (conjMode) {
    html = `<span class="pov-level">All Conjunctions</span>`;
  }
  // else: browse mode with nothing selected → no badge (don't claim a view).

  povEl.innerHTML = html;
  povEl.style.display = html ? "" : "none";
}

function isolateSats(ids) {
  isolationSet = new Set(ids);
  if (typeof applyVisibilityState === "function") applyVisibilityState();
  // Re-mask the worker NOW (force: must land even while paused) so the
  // propagation batch shrinks to the isolated set immediately.
  if (typeof refreshSatellites === "function") refreshSatellites(true);
}

function clearIsolation() {
  if (isolationSet === null) return;
  isolationSet = null;
  if (typeof applyVisibilityState === "function") applyVisibilityState();
  // Unmasked forced batch: masked-out sats carry ok=false, so restoring the
  // view needs one full batch even while paused (same pattern as setConjOnly).
  if (typeof refreshSatellites === "function") refreshSatellites(true);
}

let conjOrb = null;                        // translucent yellow orb at the approach
let conjTrails = [];                       // [{teme, positions, entity}] both orbits
let conjTrailTimer = null;                 // re-rotates trails as time advances
let conjLabeled = [];                      // [{id, offset}] saved label offsets
let conjEphemeris = [];                    // nadir line + ground marker under TCA
let conjOnlyPrimitive = null;              // batched partial fading arcs (conj-only view)
let focusedKey = null;                     // which event is highlighted
let focusedPair = null;                    // [id1, id2] of the focused event (filter teardown)
let selectedConjNorad = null;              // satellite whose conjunctions are shown

/** True if the currently-focused conjunction involves this satellite — the group
 *  filters use it to tear down an orphaned focus when a participant is hidden. */
function focusedConjunctionInvolves(noradId) {
  return focusedPair !== null &&
    (focusedPair[0] === noradId || focusedPair[1] === noradId);
}

// --- DOM: top-left list + bottom-right detail panel ---
const conjPanel = document.createElement("div");
conjPanel.id = "conjunction-list";
conjPanel.innerHTML =
  `<div id="conjunction-header">Conjunctions</div><div id="conjunction-body"></div>`;
document.body.appendChild(conjPanel);

const detailPanel = document.createElement("div");
detailPanel.id = "conjunction-detail";
detailPanel.style.display = "none";
document.body.appendChild(detailPanel);

/** Short UTC label, e.g. "Jun 13 07:04 UTC". */
function formatTca(iso) {
  const d = new Date(iso);
  const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const day = d.getUTCDate();
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${mon} ${day} ${hh}:${mm} UTC`;
}

/** Stable id for an event (pair + TCA) so we can track focus. */
function eventKey(e) {
  return `${e.sat1_norad_id}-${e.sat2_norad_id}-${e.tca}`;
}

// --- Orbit trails for the two conjuncting satellites (thin transparent blue) ---

/** Rotate TEME points to ECEF at a given sim time (single GMST, like the trail
 *  in info-panel.js). computeGmst() is a global from info-panel.js. */
function temeToEcef(temePts, simMs) {
  const g = computeGmst(simMs);
  const c = Math.cos(g), s = Math.sin(g);
  return temePts.map(p => new Cesium.Cartesian3(
    c * p.x + s * p.y, -s * p.x + c * p.y, p.z));
}

function addTrail(noradId, centerMs) {
  const meta = (typeof satelliteMetadata !== "undefined")
    ? satelliteMetadata.get(noradId) : null;
  const durationMin = meta ? Math.ceil(meta.period_min) : 95;
  const startMs = centerMs - durationMin * 30000; // centered on the encounter
  try {
    // Client-side SGP4 over one period (snapshot-data.js) — no fetch.
    const teme = computeTrackTEME(noradId, startMs, durationMin, 120);
    if (teme.length < 2) return;
    // Color each trail by its satellite's display group (matches the dot color),
    // so a Starlink × Other-LEO encounter reads as one orange + one blue orbit.
    const col = (typeof groupColor === "function")
      ? groupColor(noradId).withAlpha(0.75) : TRAIL_BLUE;
    const t = { noradId, teme, positions: temeToEcef(teme, simClock.getTimeMs()) };
    t.entity = viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty(() => t.positions, false),
        width: 2,
        material: col,
        arcType: Cesium.ArcType.NONE,
      },
    });
    conjTrails.push(t);
    syncConjTrailVisibility();
  } catch (err) {
    console.error("Could not draw conjunction trail:", err);
  }
}

/**
 * De-duplicate orbit rings: the SELECTED satellite already shows its
 * full-period info-panel trail, and a focused conjunction draws its own trail
 * for both participants — for the selected sat those are the same orbit
 * sampled over slightly different windows, rendering as two near-identical
 * rings a few pixels apart. Hide the conjunction copy while the info-panel
 * trail is visible; re-show it if the panel trail is toggled off (so the pair
 * geometry stays complete) or the selection changes. Called from addTrail,
 * and from info-panel.js on select/deselect/trail-toggle — reactive, so it's
 * correct for every click order (row-then-dot, dot-then-row, …).
 */
function syncConjTrailVisibility() {
  const sel = (typeof selectedNoradId !== "undefined") ? selectedNoradId : null;
  const infoTrailOn = (typeof trailVisible !== "undefined") ? trailVisible : true;
  for (const t of conjTrails) {
    t.entity.show = !(infoTrailOn && t.noradId === sel);
  }
}

// --- Pair highlight: BOTH participants of the focused conjunction get the
// enlarged/outlined dot treatment (same look as the selection ring, so
// "highlighted = involved in what you're looking at" reads consistently).
// Reactive like the trail sync: recomputed on focus, clear, and selection
// changes, so no click order can leave a stale ring. The point belonging to
// the SELECTED satellite is skipped — info-panel.js owns that one's style.
let _pairGlow = []; // norad ids currently styled by the pair highlight

function syncPairHighlight() {
  if (typeof satellites === "undefined") return;
  const prev = _pairGlow;
  _pairGlow = focusedPair ? [...focusedPair] : [];
  // Restyle everything that entered or left the pair set — refreshPointStyle
  // (satellites.js) resolves hover/selection/pair priority in one place.
  const affected = new Set([...prev, ..._pairGlow]);
  for (const id of affected) {
    if (typeof refreshPointStyle === "function") refreshPointStyle(id);
  }
}

function clearTrails() {
  if (conjTrailTimer) { clearInterval(conjTrailTimer); conjTrailTimer = null; }
  for (const t of conjTrails) viewer.entities.remove(t.entity);
  conjTrails = [];
}

// --- "Only display conjunctions" view: a short fading arc for every conjunction ---
// Each participant gets ~1/3 of an orbit centered on its TCA, rotated to the ECEF
// frame at TCA (static — a geographic map of where the encounters happen), with a
// per-vertex alpha ramp that fades to nothing past the middle third so the globe
// isn't buried in full orbit rings. All arcs batch into ONE primitive (one draw
// call), tinted by display group.

function buildConjArc(noradId, e, baseColor) {
  const tcaMs = Date.parse(e.tca);
  const meta = (typeof satelliteMetadata !== "undefined")
    ? satelliteMetadata.get(noradId) : null;
  const period = meta ? meta.period_min : 95;
  const arcMin = period * 0.10;                 // ~10% of the orbit: 5% each side of TCA
  const steps = 24;
  const startMs = tcaMs - (arcMin / 2) * 60000;
  const teme = computeTrackTEME(noradId, startMs, arcMin, steps);
  if (teme.length < 2) return null;
  const ecef = temeToEcef(teme, tcaMs);         // sit at the encounter's ECEF location
  const n = ecef.length;
  const center = (n - 1) / 2;
  const colors = new Array(n);
  for (let i = 0; i < n; i++) {
    const d = Math.abs(i - center) / center;    // 0 at TCA center → 1 at the ends
    const a = Math.max(0, 0.95 * (1 - d * d));  // bright at TCA, gradient to ~0 at the tips
    colors[i] = baseColor.withAlpha(a);
  }
  return new Cesium.GeometryInstance({
    geometry: new Cesium.PolylineGeometry({
      positions: ecef,
      width: 2.0,
      arcType: Cesium.ArcType.NONE,
      colors,
      colorsPerVertex: true,
      vertexFormat: Cesium.PolylineColorAppearance.VERTEX_FORMAT,
    }),
    id: { conjEvent: e }, // pickable: clicking the arc focuses this conjunction
  });
}

/** scope = "top20" (the list's closest CONJ_LIST_MAX) or "all" (every event). */
function renderConjOnlyArcs(scope) {
  clearConjOnlyArcs();
  const events = scope === "all" ? conjEvents : conjEvents.slice(0, CONJ_LIST_MAX);
  const instances = [];
  for (const e of events) {
    for (const id of [e.sat1_norad_id, e.sat2_norad_id]) {
      const color = (typeof groupColor === "function")
        ? groupColor(id) : Cesium.Color.ORANGE;
      const inst = buildConjArc(id, e, color);
      if (inst) instances.push(inst);
    }
  }
  if (!instances.length) return;
  conjOnlyPrimitive = viewer.scene.primitives.add(new Cesium.Primitive({
    geometryInstances: instances,
    appearance: new Cesium.PolylineColorAppearance({ translucent: true }),
    asynchronous: false,
    allowPicking: true,
  }));
  viewer.scene.requestRender();
}

/** Click handler hook for the conjunction arcs (Top-20 view). If an arc was
 *  picked, focus that conjunction (reveals both participants + draws the
 *  encounter + jumps to TCA). Returns true if it handled the pick. */
function handleConjArcPick(picked) {
  if (picked && picked.id && picked.id.conjEvent) {
    focusConjunction(picked.id.conjEvent, true);
    return true;
  }
  return false;
}

function clearConjOnlyArcs() {
  if (conjOnlyPrimitive) {
    viewer.scene.primitives.remove(conjOnlyPrimitive);
    conjOnlyPrimitive = null;
    viewer.scene.requestRender();
  }
}

// --- Label de-overlap: nudge the two names apart so both read at the crossing ---

function offsetLabels(idA, idB) {
  restoreLabels();
  const place = (id, offset) => {
    // Labels are lazy at scale — make sure the pair has them before nudging.
    const label = (typeof ensureLabel === "function") ? ensureLabel(id) : null;
    if (!label) return;
    conjLabeled.push({ id, offset: label.pixelOffset.clone() });
    label.pixelOffset = offset;
  };
  place(idA, new Cesium.Cartesian2(14, -22));  // one name above
  place(idB, new Cesium.Cartesian2(14, 16));   // the other below
}

function restoreLabels() {
  for (const { id, offset } of conjLabeled) {
    const entry = (typeof satellites !== "undefined") ? satellites.get(id) : null;
    if (entry && entry.label) entry.label.pixelOffset = offset;
  }
  conjLabeled = [];
}

// --- Focus a conjunction ---

function clearOrb() {
  if (conjOrb) { viewer.entities.remove(conjOrb); conjOrb = null; }
}

// --- Ephemeris: drop a nadir line from the conjunction point to the ground and
//     mark the sub-point, so you can see what land the encounter happens over. ---

function addConjEphemeris(cart, tcaIso) {
  clearConjEphemeris();
  // Project the TCA point straight down to the ellipsoid surface.
  const surface = new Cesium.Cartesian3();
  Cesium.Cartesian3.normalize(cart, surface);
  Cesium.Cartesian3.multiplyByScalar(
    surface, Cesium.Ellipsoid.WGS84.maximumRadius, surface);

  // Lat/lon of that sub-point (straight from the orb position, so the label and
  // the marker always agree).
  const carto = Cesium.Cartographic.fromCartesian(cart);
  const lat = Cesium.Math.toDegrees(carto.latitude);
  const lon = Cesium.Math.toDegrees(carto.longitude);
  const latlon = `${Math.abs(lat).toFixed(1)}°${lat >= 0 ? "N" : "S"}, ` +
                 `${Math.abs(lon).toFixed(1)}°${lon >= 0 ? "E" : "W"}`;
  // Location + WHEN: the ground label carries the TCA date/time under the
  // sub-point, so the marker answers both "where" and "when" the approach is.
  const labelText = tcaIso ? `${latlon}\n${formatTca(tcaIso)}` : latlon;

  const line = viewer.entities.add({
    polyline: {
      positions: [surface, cart],
      width: 1.5,
      material: Cesium.Color.YELLOW.withAlpha(0.55),
      arcType: Cesium.ArcType.NONE,
    },
  });
  const marker = viewer.entities.add({
    position: surface,
    point: {
      pixelSize: 7,
      color: Cesium.Color.YELLOW,
      outlineColor: Cesium.Color.BLACK,
      outlineWidth: 1,
    },
    label: {
      text: labelText,
      font: "11px monospace",
      fillColor: Cesium.Color.WHITE,
      showBackground: true,
      backgroundColor: new Cesium.Color(0.1, 0.1, 0.12, 0.85),
      pixelOffset: new Cesium.Cartesian2(0, 16),
      style: Cesium.LabelStyle.FILL,
    },
  });
  conjEphemeris = [line, marker];
}

function clearConjEphemeris() {
  for (const ent of conjEphemeris) viewer.entities.remove(ent);
  conjEphemeris = [];
}

// Guard: while focusConjunction refreshes the detail panel, don't let it soft-
// focus a *different* (closest) event and stomp the one we just focused.
let _refreshingDetail = false;

/**
 * Draw a conjunction's geometry at its TCA: the translucent orb, the ground
 * ephemeris, both satellites' orbit trails, and de-overlapped labels. Does NOT
 * move the clock or camera — the caller chooses (focus vs. soft-focus). Returns
 * the TCA ECEF point, or null on propagation failure.
 */
function drawConjunctionGeometry(e) {
  const tcaMs = Date.parse(e.tca);
  const cart = computeEcefAt(e.sat1_norad_id, tcaMs)
    || computeEcefAt(e.sat2_norad_id, tcaMs);
  if (!cart) {
    console.error("Could not locate conjunction: propagation failed at TCA");
    return null;
  }

  clearOrb();
  conjOrb = viewer.entities.add({
    position: cart,
    ellipsoid: {
      radii: new Cesium.Cartesian3(ORB_RADIUS_M, ORB_RADIUS_M, ORB_RADIUS_M),
      material: Cesium.Color.YELLOW.withAlpha(0.22),
    },
    label: {
      text: `${e.miss_distance_km.toFixed(2)} km`,
      font: "12px monospace",
      fillColor: Cesium.Color.WHITE,
      showBackground: true,
      backgroundColor: new Cesium.Color(0.1, 0.1, 0.12, 0.85),
      pixelOffset: new Cesium.Cartesian2(0, -22),
      style: Cesium.LabelStyle.FILL,
    },
  });

  // Ephemeris: nadir line + ground marker (location + TCA date/time) below.
  addConjEphemeris(cart, e.tca);

  // Both orbits in thin blue; re-rotate as time advances so they track the dots.
  clearTrails();
  addTrail(e.sat1_norad_id, tcaMs);
  addTrail(e.sat2_norad_id, tcaMs);
  conjTrailTimer = setInterval(() => {
    if (simClock.isPaused()) return; // frozen time → trails don't move → stay idle
    const ms = simClock.getTimeMs();
    for (const t of conjTrails) t.positions = temeToEcef(t.teme, ms);
    viewer.scene.requestRender(); // reflect the re-rotation (requestRenderMode)
  }, 500);

  offsetLabels(e.sat1_norad_id, e.sat2_norad_id);
  return cart;
}

/**
 * Focus a conjunction. Always draws its geometry + highlights it in the lists.
 * When doJump (default) also rewinds the clock to CONJ_LEAD_MIN before TCA at 1x
 * and flies the camera to the close-approach area, so the whole encounter plays
 * out. doJump=false is a "soft focus" used elsewhere (see softFocusConjunction).
 */
function focusConjunction(e, doJump = true) {
  // Draw the geometry FIRST. If propagation fails for the pair at TCA (stale/
  // decayed elements — drawConjunctionGeometry returns null and logs), bail
  // WITHOUT committing focus/reveal/isolation state, so we never leave the globe
  // isolated to two invisible sats with no orb or trails (an unexplained blank).
  const cart = drawConjunctionGeometry(e);
  if (!cart) return;

  focusedKey = eventKey(e);
  focusedPair = [e.sat1_norad_id, e.sat2_norad_id];
  // Isolate BEFORE revealing. revealSatsExclusive() runs applyVisibilityState(),
  // and if isolationSet still held the PREVIOUS focus's pair, the new pair's
  // sats would read as filteredOut → handleParticipantHidden() → it sees the new
  // focusedPair, thinks the focus is orphaned, and tears it all down mid-call
  // (nulling the just-drawn orb → flyTo(null) throws). Committing isolationSet to
  // the new pair first keeps the new participants "in", so no teardown fires when
  // we refocus straight from one conjunction to another.
  const iso = new Set(focusedPair);
  if (typeof selectedNoradId !== "undefined" && selectedNoradId !== null) {
    iso.add(selectedNoradId);
  }
  isolateSats(iso);
  // Reveal ONLY the two participants (not their whole groups), exclusively — so
  // switching conjunctions re-hides the previous pair. Already-visible ones are
  // unaffected. (No-op for display while isolation is active, but it sets
  // revealedSats for when the focus later clears.)
  if (typeof revealSatsExclusive === "function") revealSatsExclusive(focusedPair);

  syncPairHighlight(); // both participants get the highlight ring

  // In the Top-20 arc view, focusing a conjunction replaces the arc slices with
  // its two full trails + TCA orb — hide the arcs (restored when focus clears).
  if (typeof conjOnlyActive !== "undefined" && conjOnlyActive === "top20") {
    clearConjOnlyArcs();
  }

  if (selectedConjNorad !== null) {
    _refreshingDetail = true;
    showConjunctionsFor(selectedConjNorad); // refresh the detail-panel highlight
    _refreshingDetail = false;
  }
  renderConjunctionList();

  if (doJump) {
    simClock.setTime(Date.parse(e.tca) - CONJ_LEAD_MIN * 60000);
    simClock.setSpeed(1);
    if (simClock.isPaused()) simClock.togglePause();
    if (typeof refreshSatellites === "function") refreshSatellites();
    viewer.flyTo(conjOrb, {
      duration: 1.6,
      offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.toRadians(45), 4.0e6),
    });
  } else {
    viewer.scene.requestRender();
  }
  tcaZoomed = doJump;   // zoomed to one approach → TCA View (Escape steps up)
  updatePovIndicator();
}

/**
 * Soft focus (used by the "All" view when a participant satellite is clicked):
 * draw both orbits + the TCA orb where the view already is, and highlight the
 * event in the list — but do NOT jump the clock or camera. The "Jump to TCA"
 * button in the detail panel does the actual jump. Never calls
 * showConjunctionsFor (that's what triggered it → would recurse).
 */
function softFocusConjunction(e) {
  // Draw first; only commit focus state if the geometry actually rendered.
  if (!drawConjunctionGeometry(e)) return;
  focusedKey = eventKey(e);
  focusedPair = [e.sat1_norad_id, e.sat2_norad_id];
  syncPairHighlight(); // both participants get the highlight ring
  renderConjunctionList();
  viewer.scene.requestRender();
}

/** Remove the focus VISUALS (orb, both trails, label offsets, row highlight) but
 *  leave any satellite selection + its detail panel intact. */
function clearConjunctionVisuals() {
  clearOrb();
  clearTrails();
  clearConjEphemeris();
  restoreLabels();
  focusedKey = null;
  focusedPair = null;
  syncPairHighlight(); // un-ring the pair (focusedPair is null now)
  clearIsolation();    // every other satellite comes back (worker unmasks too)
  // Re-hide any satellite we force-revealed just for this focus (per-sat reveal).
  if (typeof clearRevealedSats === "function") clearRevealedSats();
  // Coming out of a focus while the Top-20 arc view is active → restore the arcs.
  if (typeof conjOnlyActive !== "undefined" && conjOnlyActive === "top20") {
    renderConjOnlyArcs("top20");
  }
  tcaZoomed = false;   // no focus → not the TCA level anymore
  updatePovIndicator();
  viewer.scene.requestRender(); // clear the orb/trails now even if paused
}

// --- Escape zoom stack (10.6): walk UP one level per keypress ---

/** Re-enter a satellite's Conjunction View (from a TCA-zoom Escape): rebuild the
 *  neighborhood (isolate + soft-focus closest + all partner trails, via
 *  showConjunctionsFor) and pull the camera back out of the TCA zoom. */
function enterConjunctionView(noradId) {
  if (typeof showConjunctionsFor === "function") showConjunctionsFor(noradId);
  viewer.camera.flyHome(1.2);
}

/** Return to All Conjunctions: drop the selection + focus + isolation and pull
 *  the camera all the way back to the full-Earth view. */
function exitToAll() {
  if (typeof selectedNoradId !== "undefined" && selectedNoradId !== null
      && typeof deselectSatellite === "function") {
    deselectSatellite();          // clears focus + isolation (clearConjunctionFocus)
  } else {
    clearConjunctionFocus();
  }
  tcaZoomed = false;
  renderConjunctionList();
  updatePovIndicator();
  viewer.camera.flyHome(1.2);
}

/** Full teardown — the focus visuals plus the selected-satellite detail panel.
 *  Used on deselect / LIVE. */
function clearConjunctionFocus() {
  clearConjunctionVisuals();
  selectedConjNorad = null;
  detailPanel.style.display = "none";
}

/** A satellite's display group was just hidden (controls.js). If it's part of
 *  the focused conjunction, drop only the focus visuals — keep any active
 *  selection and its conjunction list (refreshed to drop the stale highlight). */
function handleParticipantHidden(noradId) {
  if (!focusedConjunctionInvolves(noradId)) return;
  clearConjunctionVisuals();
  if (selectedConjNorad !== null) showConjunctionsFor(selectedConjNorad);
}

// --- Top-left list (global closest approaches) ---

function renderConjunctionList() {
  const satCount = (typeof satelliteMetadata !== "undefined")
    ? satelliteMetadata.size : "—";
  // With the display cap (snapshot-data.js) the UI works with the closest N of
  // the full screened set — say so, so the count stays honest.
  const pairsLabel = conjEvents.length < conjTotalCount
    ? `closest ${conjEvents.length} of ${conjTotalCount} pairs`
    : `${conjTotalCount} pairs`;
  document.getElementById("conjunction-header").textContent =
    `Conjunctions · ${pairsLabel} · ${satCount} sats`;

  const body = document.getElementById("conjunction-body");
  if (!conjEvents.length) {
    body.innerHTML = `<div class="conjunction-empty">No conjunctions found.</div>`;
    return;
  }
  const shown = Math.min(conjListShown, conjEvents.length);
  let html = conjEvents.slice(0, shown).map((e, i) => `
    <div class="conjunction-row${eventKey(e) === focusedKey ? " focused" : ""}"
         data-idx="${i}">
      <div class="conjunction-pair">${nameSpan(e.sat1_norad_id, e.sat1_name)} × ${nameSpan(e.sat2_norad_id, e.sat2_name)}</div>
      <div class="conjunction-meta">
        <span class="conjunction-miss" style="color:${missColor(e.miss_distance_km)}">${e.miss_distance_km.toFixed(1)} km</span>
        <span class="conjunction-tca">${formatTca(e.tca)}</span>
      </div>
    </div>`).join("");
  if (shown < conjEvents.length) {
    html += `<button id="conj-show-more">Show more (${conjEvents.length - shown} left)</button>`;
  }
  body.innerHTML = html;
  body.querySelectorAll(".conjunction-row").forEach(row => {
    row.addEventListener("click", () =>
      focusConjunction(conjEvents[parseInt(row.dataset.idx)]));
  });
  const moreBtn = document.getElementById("conj-show-more");
  if (moreBtn) moreBtn.addEventListener("click", () => {
    conjListShown += CONJ_LIST_MAX;
    renderConjunctionList();
  });
}

// --- Bottom-right: the selected satellite's conjunctions ---

/** Called by info-panel.js when a satellite is selected. */
function showConjunctionsFor(noradId) {
  selectedConjNorad = noradId;
  const events = conjByNorad.get(noradId) || [];
  const name = getSatName(noradId);
  const windowH = snapshotMeta ? Math.round(snapshotMeta.screen.window_hours) : 72;

  if (!events.length) {
    detailPanel.innerHTML =
      `<div id="conjunction-detail-header">${name}</div>` +
      `<div class="conjunction-empty">No conjunctions in the ` +
      `${windowH} h screening window.</div>`;
    detailPanel.style.display = "block";
    return;
  }

  detailPanel.innerHTML =
    `<div id="conjunction-detail-header">` +
    `<div class="cd-title">` +
    `<div class="cd-satname">${name}</div>` +
    `<div class="cd-count">${events.length} conjunction${events.length > 1 ? "s" : ""}</div>` +
    `</div>` +
    `<button id="jump-to-tca" title="Jump the clock to the closest approach">⤓ Click to Jump to TCA</button>` +
    `</div>` +
    events.map((e, i) => `
      <div class="cd-row${eventKey(e) === focusedKey ? " focused" : ""}" data-idx="${i}">
        <div class="cd-partner">${nameSpan(e.sat1_norad_id, e.sat1_name)} × ${nameSpan(e.sat2_norad_id, e.sat2_name)}</div>
        <div class="cd-stats">
          <span class="conjunction-miss" style="color:${missColor(e.miss_distance_km)}">${e.miss_distance_km.toFixed(2)} km</span>
          <span>${e.relative_speed_km_s.toFixed(1)} km/s</span>
          <span class="conjunction-tca">${formatTca(e.tca)}</span>
        </div>
        <div class="cd-rtn">R ${e.r_km.toFixed(2)}  T ${e.t_km.toFixed(2)}  N ${e.n_km.toFixed(2)} km</div>
      </div>`).join("");
  detailPanel.style.display = "block";
  detailPanel.querySelectorAll(".cd-row").forEach(row => {
    row.addEventListener("click", () =>
      focusConjunction(events[parseInt(row.dataset.idx)]));
  });
  // "Jump to TCA" jumps to the closest conjunction of this satellite.
  const jumpBtn = document.getElementById("jump-to-tca");
  if (jumpBtn) jumpBtn.addEventListener("click", () => focusConjunction(events[0], true));

  // Selecting a participant (any view, 10.6 UX round) isolates the screen to
  // this satellite + EVERY partner it has a conjunction with, soft-focuses its
  // closest event (orb + pair highlight, no clock jump), and draws the other
  // partners' orbit trails too — the whole neighborhood of this object, and
  // nothing else, on screen. Skip during a focusConjunction refresh (that
  // would stomp the just-focused event).
  if (!_refreshingDetail && events.length) {
    const iso = new Set([noradId]);
    for (const e of events) {
      iso.add(e.sat1_norad_id);
      iso.add(e.sat2_norad_id);
    }
    isolateSats(iso);
    softFocusConjunction(events[0]);
    // Orbit trails for the partners beyond the focused (closest) event —
    // each centered on ITS OWN encounter time. addTrail appends to conjTrails,
    // so the re-rotation timer + clearTrails handle them like the pair's.
    const trailed = new Set(
      [noradId, events[0].sat1_norad_id, events[0].sat2_norad_id]);
    for (const e of events.slice(1)) {
      for (const id of [e.sat1_norad_id, e.sat2_norad_id]) {
        if (trailed.has(id)) continue;
        trailed.add(id);
        addTrail(id, Date.parse(e.tca));
      }
    }
    tcaZoomed = false;      // a fresh selection lands in Conjunction View
    updatePovIndicator();
  }
}

// --- Freshness line: "updated X ago" + the validation report link ---

const freshnessLine = document.createElement("div");
freshnessLine.id = "conjunction-freshness";
conjPanel.appendChild(freshnessLine);

/** Human age of the snapshot, from real wall-clock time (not the sim clock —
 *  freshness is about the data, not where the user scrubbed the view). */
function formatAge(generatedAtIso) {
  const mins = Math.max(0, (Date.now() - Date.parse(generatedAtIso)) / 60000);
  if (mins < 60) return `${Math.round(mins)} min`;
  if (mins < 48 * 60) return `${(mins / 60).toFixed(1)} h`;
  return `${(mins / 1440).toFixed(1)} d`;
}

function renderFreshness() {
  if (!snapshotMeta) return;
  freshnessLine.innerHTML =
    `data updated ${formatAge(snapshotMeta.generated_at)} ago · ` +
    `<a href="${VALIDATION_REPORT_URL}" target="_blank" rel="noopener"` +
    ` title="Screening validated against CelesTrak SOCRATES">validation ↗</a>`;
}

// --- Load: events come pre-computed in the snapshot (screened offline) ---

snapshotReady.then(() => {
  conjTotalCount = snapshotMeta.n_conjunctions;
  conjEvents = snapshotConjunctions; // SFS-screened, suppressed, de-duped, closest-first
  conjByNorad.clear();
  conjParticipants.clear();
  for (const e of conjEvents) {
    for (const id of [e.sat1_norad_id, e.sat2_norad_id]) {
      if (!conjByNorad.has(id)) conjByNorad.set(id, []);
      conjByNorad.get(id).push(e);
      conjParticipants.add(id);
    }
  }
  renderConjunctionList();
  renderFreshness();
  setInterval(renderFreshness, 60000); // the age ticks while the tab stays open
}).catch(() => { /* load failure already reported + shown by satellites.js */ });

// --- Escape: walk UP the zoom stack one level per keypress ---
//   TCA View  → Conjunction View (of the selected sat) — or All if none selected
//   Conjunction View → All Conjunctions (deselect, isolation cleared, camera home)
//   All (a stray focus) → cleared
// Field-level Escapes are NOT ours: the clock's HH:MM:SS edit (contenteditable)
// and the search box (input) handle their own cancel and must keep it.
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  const t = e.target;
  if (t && (t.isContentEditable || t.tagName === "INPUT")) return;
  const hasSelection =
    typeof selectedNoradId !== "undefined" && selectedNoradId !== null;
  if (tcaZoomed) {
    // Level 2 → up one: back to the selected sat's neighborhood, or All.
    if (hasSelection) enterConjunctionView(selectedNoradId);
    else exitToAll();
  } else if (hasSelection || focusedKey !== null) {
    // Level 1 (or a stray focus) → All Conjunctions.
    exitToAll();
  }
});

updatePovIndicator(); // initial: "All Conjunctions"
