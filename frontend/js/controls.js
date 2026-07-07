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

// --- Build Panel (waits for metadata to load) ---

function initControls() {
  if (satelliteMetadata.size === 0 || typeof SAT_GROUPS === "undefined") {
    setTimeout(initControls, 200);
    return;
  }

  // Count satellites per group; only surface groups that actually appear.
  const counts = {};
  for (const meta of satelliteMetadata.values()) {
    counts[meta.group] = (counts[meta.group] || 0) + 1;
  }
  const groups = SAT_GROUPS.filter((g) => counts[g.key] > 0);
  for (const g of groups) toggleState.groups[g.key] = true;

  const panel = document.createElement("div");
  panel.id = "controls-panel";

  let html = `<div id="controls-header">Display</div>`;
  html += `<label class="control-toggle">
    <input type="checkbox" id="toggle-labels" checked> Labels
  </label>`;
  html += `<div id="controls-section-label">Satellites</div>`;
  for (const g of groups) {
    html += `<label class="group-toggle">
      <input type="checkbox" checked data-group="${g.key}">
      <span class="group-swatch" style="background:${g.color}"></span>
      <span class="group-name">${g.label}</span>
      <span class="group-count">${counts[g.key].toLocaleString()}</span>
    </label>`;
  }
  panel.innerHTML = html;
  document.body.appendChild(panel);

  // --- Event Listeners ---

  document.getElementById("toggle-labels").addEventListener("change", function () {
    toggleState.labels = this.checked;
    applyVisibilityState();
  });

  for (const cb of panel.querySelectorAll("[data-group]")) {
    cb.addEventListener("change", function () {
      toggleState.groups[this.dataset.group] = this.checked;
      applyVisibilityState();
    });
  }
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
    const groupVisible = meta ? toggleState.groups[meta.group] !== false : true;

    // Fold in the propagation ok-mask (a decayed/diverged sat has no valid
    // position this batch — mirrors propagate_batch's per-sat sentinels).
    const show = groupVisible && entry.ok !== false;

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
    if (!groupVisible && selectedNoradId === noradId) {
      deselectSatellite();
    }
    if (!groupVisible && typeof handleParticipantHidden === "function") {
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

initControls();
