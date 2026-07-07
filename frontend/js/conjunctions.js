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
const CONJ_LIST_MAX = 12;
const VALIDATION_REPORT_URL =
  "https://github.com/jtemblador/OrbitWatch/blob/main/validation/socrates_report.md";
const CONJ_COLOR = Cesium.Color.ORANGE;
// Start the clock this far BEFORE TCA (at 1x) so the full approach + closest
// pass + separation play out on screen, rather than snapping to the instant.
const CONJ_LEAD_MIN = 5;
const ORB_RADIUS_M = 75000;                // yellow "conjunction area" orb
const TRAIL_BLUE = Cesium.Color.fromCssColorString("#4fc3f7").withAlpha(0.35);

// State
let conjEvents = [];                       // meaningful events, closest-first
let conjTotalCount = 0;                    // unique at-risk pairs (from meta)
const conjByNorad = new Map();             // norad_id -> [events involving it]
let conjOrb = null;                        // translucent yellow orb at the approach
let conjTrails = [];                       // [{teme, positions, entity}] both orbits
let conjTrailTimer = null;                 // re-rotates trails as time advances
let conjLabeled = [];                      // [{id, offset}] saved label offsets
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
    const t = { teme, positions: temeToEcef(teme, simClock.getTimeMs()) };
    t.entity = viewer.entities.add({
      polyline: {
        positions: new Cesium.CallbackProperty(() => t.positions, false),
        width: 1.5,
        material: TRAIL_BLUE,
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

/**
 * Play out a conjunction: rewind the clock to CONJ_LEAD_MIN before TCA at 1x
 * (so the approach and separation are visible), fly to the close-approach area,
 * mark it with a translucent orb, and draw both satellites' orbit trails.
 */
function focusConjunction(e) {
  focusedKey = eventKey(e);
  focusedPair = [e.sat1_norad_id, e.sat2_norad_id];
  // The list shows all close approaches regardless of the display filters, so a
  // participant may be in a hidden group — reveal both groups (both first, then
  // one apply) so the encounter is actually visible, not an orphaned orb/trail.
  if (typeof revealGroups === "function") revealGroups(focusedPair);
  if (selectedConjNorad !== null) showConjunctionsFor(selectedConjNorad);
  renderConjunctionList();

  // Close-approach location = either object's position at TCA, propagated
  // client-side (snapshot-data.js) — try the partner if the first one fails.
  const tcaMs = Date.parse(e.tca);
  const cart = computeEcefAt(e.sat1_norad_id, tcaMs)
    || computeEcefAt(e.sat2_norad_id, tcaMs);
  if (!cart) {
    console.error("Could not locate conjunction: propagation failed at TCA");
    return;
  }

  // Rewind to the lead-in and play at 1x so the whole encounter unfolds.
  simClock.setTime(tcaMs - CONJ_LEAD_MIN * 60000);
  simClock.setSpeed(1);
  if (simClock.isPaused()) simClock.togglePause();
  if (typeof refreshSatellites === "function") refreshSatellites();

  // Yellow orb marking the conjunction area.
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

  // Both orbits in thin blue; re-rotate as time advances so they track the dots.
  clearTrails();
  addTrail(e.sat1_norad_id, tcaMs);
  addTrail(e.sat2_norad_id, tcaMs);
  conjTrailTimer = setInterval(() => {
    const ms = simClock.getTimeMs();
    for (const t of conjTrails) t.positions = temeToEcef(t.teme, ms);
  }, 500);

  offsetLabels(e.sat1_norad_id, e.sat2_norad_id);

  viewer.flyTo(conjOrb, {
    duration: 1.6,
    offset: new Cesium.HeadingPitchRange(0, -Cesium.Math.toRadians(45), 4.0e6),
  });
}

/** Remove the focus VISUALS (orb, both trails, label offsets, row highlight) but
 *  leave any satellite selection + its detail panel intact. */
function clearConjunctionVisuals() {
  clearOrb();
  clearTrails();
  restoreLabels();
  focusedKey = null;
  focusedPair = null;
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
  body.innerHTML = conjEvents.slice(0, CONJ_LIST_MAX).map((e, i) => `
    <div class="conjunction-row${eventKey(e) === focusedKey ? " focused" : ""}"
         data-idx="${i}">
      <div class="conjunction-pair">${e.sat1_name} × ${e.sat2_name}</div>
      <div class="conjunction-meta">
        <span class="conjunction-miss">${e.miss_distance_km.toFixed(1)} km</span>
        <span class="conjunction-tca">${formatTca(e.tca)}</span>
      </div>
    </div>`).join("");
  body.querySelectorAll(".conjunction-row").forEach(row => {
    row.addEventListener("click", () =>
      focusConjunction(conjEvents[parseInt(row.dataset.idx)]));
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
    `<div id="conjunction-detail-header">${name} · ${events.length} conjunction` +
    `${events.length > 1 ? "s" : ""}</div>` +
    events.map((e, i) => {
      const partnerName = e.sat1_norad_id === noradId ? e.sat2_name : e.sat1_name;
      return `
      <div class="cd-row${eventKey(e) === focusedKey ? " focused" : ""}" data-idx="${i}">
        <div class="cd-partner">▶ ${partnerName}</div>
        <div class="cd-stats">
          <span class="conjunction-miss">${e.miss_distance_km.toFixed(2)} km</span>
          <span>${e.relative_speed_km_s.toFixed(1)} km/s</span>
          <span>${formatTca(e.tca)}</span>
        </div>
        <div class="cd-rtn">R ${e.r_km.toFixed(2)}  T ${e.t_km.toFixed(2)}  N ${e.n_km.toFixed(2)} km</div>
      </div>`;
    }).join("");
  detailPanel.style.display = "block";
  detailPanel.querySelectorAll(".cd-row").forEach(row => {
    row.addEventListener("click", () =>
      focusConjunction(events[parseInt(row.dataset.idx)]));
  });
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
  for (const e of conjEvents) {
    for (const id of [e.sat1_norad_id, e.sat2_norad_id]) {
      if (!conjByNorad.has(id)) conjByNorad.set(id, []);
      conjByNorad.get(id).push(e);
    }
  }
  renderConjunctionList();
  renderFreshness();
  setInterval(renderFreshness, 60000); // the age ticks while the tab stays open
}).catch(() => { /* load failure already reported + shown by satellites.js */ });
