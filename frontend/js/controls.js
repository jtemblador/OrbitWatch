/**
 * OrbitWatch — Display controls (label toggle + group filters).
 *
 * Top-right panel: a Labels toggle plus one checkbox per display group
 * (Starlink, Space Stations, Navigation/GNSS, Other LEO, GEO/high) with a
 * colored swatch and a live count. Each satellite belongs to exactly one group
 * (classified in snapshot-data.js). Unchecking a group HIDES its points AND
 * stops their per-frame animation work (satellites.js skips hidden points) —
 * so the filters are a real performance dial, not just visual. Re-checking a
 * group resumes it from the current position.
 *
 * Depends on: viewer (app.js), satellites + SAT_GROUPS + satelliteMetadata
 *   (snapshot-data.js / satellites.js), selectedNoradId, deselectSatellite
 *   (info-panel.js).
 */

// --- Toggle State ---
const toggleState = {
  labels: true,
  groups: {}, // group key -> bool (built from the snapshot's populated groups)
};

// Satellites force-shown regardless of their group filter — set EXCLUSIVELY by
// conjunctions.js when a conjunction is focused (only the two participants), so
// selecting another conjunction re-hides the previous pair.
const revealedSats = new Set();

// "Only display conjunctions" view: null (off), "top20", or "all". When active
// every satellite point is hidden (the fading conjunction arcs are the display)
// EXCEPT any explicitly revealed (a focused conjunction's pair).
let conjOnlyActive = null;

// --- Build Panel (waits for metadata + conjunctions to load) ---

let _initWaited = 0;
function initControls() {
  const metaReady = satelliteMetadata.size > 0 && typeof SAT_GROUPS !== "undefined";
  // Wait for conjunction participants too, so the default "All" view has data.
  // Give up after ~5s in case a snapshot genuinely has no conjunctions.
  const conjReady = typeof conjParticipants !== "undefined"
    && (conjParticipants.size > 0 || _initWaited > 25);
  if (!metaReady || !conjReady) { _initWaited++; setTimeout(initControls, 200); return; }

  // Count satellites per group; only surface groups that actually appear.
  const counts = {};
  for (const meta of satelliteMetadata.values()) {
    counts[meta.group] = (counts[meta.group] || 0) + 1;
  }
  const groups = SAT_GROUPS.filter((g) => counts[g.key] > 0);
  // Groups start OFF — the site opens on the "All" conjunction view (below), so
  // only the ~few-hundred participants render on load. Users toggle groups on.
  for (const g of groups) toggleState.groups[g.key] = false;

  const panel = document.createElement("div");
  panel.id = "controls-panel";

  let html = `<div id="controls-header">Display</div>`;
  html += `<label class="control-toggle">
    <input type="checkbox" id="toggle-labels" checked> Labels
  </label>`;
  html += `<div id="controls-section-label">Satellites</div>`;
  for (const g of groups) {
    html += `<label class="group-toggle">
      <input type="checkbox" data-group="${g.key}">
      <span class="group-swatch" style="background:${g.color}"></span>
      <span class="group-name">${g.label}</span>
      <span class="group-count">${counts[g.key].toLocaleString()}</span>
    </label>`;
  }
  // "Only display conjunctions" — Top 20 / All are either-or (click the active
  // one again to turn it off). Hides all sats and draws the fading arcs instead.
  html += `<div id="controls-section-label">Conjunctions only</div>`;
  html += `<div id="conj-only-buttons">
    <button class="conj-only-btn" data-scope="top20">Top 20</button>
    <button class="conj-only-btn" data-scope="all">All</button>
  </div>`;
  panel.innerHTML = html;
  document.body.appendChild(panel);

  // --- Event Listeners ---

  document.getElementById("toggle-labels").addEventListener("change", function () {
    toggleState.labels = this.checked;
    applyVisibilityState();
  });

  for (const cb of panel.querySelectorAll("[data-group]")) {
    cb.addEventListener("change", function () {
      // Touching a group filter exits the conjunction-only view (you want sats back).
      if (conjOnlyActive) setConjOnly(conjOnlyActive);
      toggleState.groups[this.dataset.group] = this.checked;
      applyVisibilityState();
    });
  }

  for (const btn of panel.querySelectorAll(".conj-only-btn")) {
    btn.addEventListener("click", function () {
      setConjOnly(this.dataset.scope);
    });
  }

  // Open on the "All" conjunction view (group filters start off, above) so only
  // the conjunction participants render on load — a light, focused first screen.
  if (conjParticipants.size > 0) setConjOnly("all");
}

/**
 * Apply current toggle state to all satellites. Called on toggle change and
 * after each position batch (satellites.js). A hidden group's points are also
 * skipped by the animation loop, so this is the perf dial, not just a display
 * one. Requests a render so a toggle shows immediately even while paused.
 */
function applyVisibilityState() {
  for (const [noradId, entry] of satellites) {
    const meta = satelliteMetadata.get(noradId);

    // filteredOut = hidden by the filter/mode (NOT by a failed propagation) —
    // only a filter hide tears down a selection/focus, an ok=false batch doesn't.
    let show, filteredOut;
    if (conjOnlyActive === "top20") {
      // Arc view: hide all dots (the fading arcs are the display); a focused pair
      // still shows over them.
      filteredOut = !revealedSats.has(noradId);
      show = !filteredOut && entry.ok !== false;
    } else if (conjOnlyActive === "all") {
      // Participant view: show only satellites that take part in a conjunction.
      const participant = (typeof isConjunctionParticipant === "function")
        && isConjunctionParticipant(noradId);
      filteredOut = !(participant || revealedSats.has(noradId));
      show = !filteredOut && entry.ok !== false;
    } else {
      // Normal: group filters, with a per-sat reveal override.
      const groupVisible = (meta ? toggleState.groups[meta.group] !== false : true)
        || revealedSats.has(noradId);
      filteredOut = !groupVisible;
      show = groupVisible && entry.ok !== false;
    }

    // On a hidden→shown transition, snap the point to its current interpolated
    // position. The animation loop skips hidden points and doesn't run at all
    // while paused, so without this a re-shown satellite would appear at the
    // stale spot it was hidden at (start/target stay current; .position doesn't).
    if (show && entry.point.show === false) {
      const f = typeof lerpFactor === "number" ? lerpFactor : 1;
      const p = Cesium.Cartesian3.lerp(entry.start, entry.target, f, new Cesium.Cartesian3());
      entry.point.position = p;
      if (entry.label) entry.label.position = p;
    }

    entry.point.show = show;
    if (entry.label) entry.label.show = show && toggleState.labels; // labels are lazy at scale

    // Tear down interactions that reference a now-hidden satellite: the info
    // panel selection, and (only the visuals of) a conjunction focus that would
    // otherwise be left orphaned pointing at an invisible dot. handleParticipant-
    // Hidden keeps any still-valid selection + its conjunction list.
    if (filteredOut && selectedNoradId === noradId) {
      deselectSatellite();
    }
    if (filteredOut && typeof handleParticipantHidden === "function") {
      handleParticipantHidden(noradId);
    }
  }
  viewer.scene.requestRender(); // reflect the change now (requestRenderMode)
}

/** Ensure the display groups of these satellites are visible — used when
 *  focusing a conjunction whose participant is in a filtered-off group. Reveals
 *  all given groups first, then applies once (so the single applyVisibilityState
 *  pass never sees a focused participant as still-hidden). */
function revealGroups(noradIds) {
  let changed = false;
  for (const id of noradIds) {
    const meta = satelliteMetadata.get(id);
    if (meta && toggleState.groups[meta.group] === false) {
      toggleState.groups[meta.group] = true;
      const cb = document.querySelector(`[data-group="${meta.group}"]`);
      if (cb) cb.checked = true;
      changed = true;
    }
  }
  if (changed) applyVisibilityState();
}

/** Force-show EXACTLY these satellites (overriding their group filters), hiding
 *  any previously-revealed ones. Used by conjunctions.js on focus so only the
 *  focused pair is un-hidden, and switching conjunctions re-hides the old pair. */
function revealSatsExclusive(noradIds) {
  revealedSats.clear();
  for (const id of noradIds) revealedSats.add(id);
  applyVisibilityState();
}

/** Drop all per-satellite reveals (called when a conjunction focus is cleared). */
function clearRevealedSats() {
  if (revealedSats.size === 0) return;
  revealedSats.clear();
  applyVisibilityState();
}

/** Toggle the "only display conjunctions" view. scope = "top20" | "all".
 *  Clicking the active scope turns it off. Hides all satellite points and draws
 *  the fading conjunction arcs (conjunctions.js). */
function setConjOnly(scope) {
  // A mode switch redefines what the globe shows — tear down any active
  // conjunction focus first, or its orb/trails/ephemeris would linger on top of
  // the new view (e.g. focus a pair, then click "Top 20" → orb over the arcs).
  if (typeof clearConjunctionVisuals === "function") clearConjunctionVisuals();

  conjOnlyActive = conjOnlyActive === scope ? null : scope;
  for (const btn of document.querySelectorAll(".conj-only-btn")) {
    btn.classList.toggle("active", btn.dataset.scope === conjOnlyActive);
  }
  // Only the Top-20 view draws the fading arcs; "All" shows participant dots.
  if (conjOnlyActive === "top20") {
    if (typeof renderConjOnlyArcs === "function") renderConjOnlyArcs("top20");
  } else if (typeof clearConjOnlyArcs === "function") {
    clearConjOnlyArcs();
  }
  applyVisibilityState();
  // Re-request a batch so the participant mask (or its removal) applies now, not
  // on the next scheduled refresh. force=true: entering/leaving "all" must land
  // one batch even while PAUSED — masked-out sats carry ok=false, so without it
  // a group re-enabled while paused would stay invisible until unpause.
  if (typeof refreshSatellites === "function") refreshSatellites(true);
}

initControls();
