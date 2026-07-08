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
    const t = { teme, positions: temeToEcef(teme, simClock.getTimeMs()) };
    t.entity = viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty(() => t.positions, false),
        width: 2,
        material: col,
        arcType: Cesium.ArcType.NONE,
      },
    });
    conjTrails.push(t);
  } catch (err) {
    console.error("Could not draw conjunction trail:", err);
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

function addConjEphemeris(cart) {
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
      text: latlon,
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

  // Ephemeris: nadir line + ground marker so the land point below is visible.
  addConjEphemeris(cart);

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
  focusedKey = eventKey(e);
  focusedPair = [e.sat1_norad_id, e.sat2_norad_id];
  // Reveal ONLY the two participants (not their whole groups), exclusively — so
  // switching conjunctions re-hides the previous pair. Already-visible ones are
  // unaffected.
  if (typeof revealSatsExclusive === "function") revealSatsExclusive(focusedPair);

  const cart = drawConjunctionGeometry(e);
  if (!cart) return;

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
}

/**
 * Soft focus (used by the "All" view when a participant satellite is clicked):
 * draw both orbits + the TCA orb where the view already is, and highlight the
 * event in the list — but do NOT jump the clock or camera. The "Jump to TCA"
 * button in the detail panel does the actual jump. Never calls
 * showConjunctionsFor (that's what triggered it → would recurse).
 */
function softFocusConjunction(e) {
  focusedKey = eventKey(e);
  focusedPair = [e.sat1_norad_id, e.sat2_norad_id];
  if (!drawConjunctionGeometry(e)) return;
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
  // Re-hide any satellite we force-revealed just for this focus (per-sat reveal).
  if (typeof clearRevealedSats === "function") clearRevealedSats();
  // Coming out of a focus while the Top-20 arc view is active → restore the arcs.
  if (typeof conjOnlyActive !== "undefined" && conjOnlyActive === "top20") {
    renderConjOnlyArcs("top20");
  }
  viewer.scene.requestRender(); // clear the orb/trails now even if paused
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
  document.getElementById("conjunction-header").textContent =
    `Conjunctions · ${conjTotalCount} pairs · ${satCount} sats`;

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
    `<button id="jump-to-tca" title="Jump the clock to the closest approach">⤓ Jump to TCA</button>` +
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

  // In the "All" view, selecting a participant immediately soft-focuses its
  // closest conjunction (both trails + orb, no clock jump). Skip during a
  // focusConjunction refresh (that would stomp the just-focused event).
  if (!_refreshingDetail && events.length &&
      typeof conjOnlyActive !== "undefined" && conjOnlyActive === "all") {
    softFocusConjunction(events[0]);
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
