/**
 * OrbitWatch — Display controls (label toggle, satellite type filters).
 *
 * Top-right panel with checkboxes to show/hide satellite labels and
 * filter by object type. Type filters only shown when multiple meaningful
 * types exist (Phase 2+). Reads satelliteMetadata for type grouping.
 *
 * Depends on: satellites, satelliteMetadata (from satellites.js),
 *             selectedNoradId, deselectSatellite (from info-panel.js)
 */

// --- Toggle State ---
const toggleState = {
  labels: true,
  types: {}, // e.g. { "PAYLOAD": true, "ROCKET BODY": false }
};

// --- Build Panel (waits for metadata to load) ---

function initControls() {
  if (satelliteMetadata.size === 0) {
    setTimeout(initControls, 500);
    return;
  }

  // Collect unique object types from metadata
  const types = new Set();
  for (const meta of satelliteMetadata.values()) {
    types.add(meta.object_type);
  }
  for (const t of types) {
    toggleState.types[t] = true;
  }

  // Build DOM
  const panel = document.createElement("div");
  panel.id = "controls-panel";

  let html = `<div id="controls-header">Display</div>`;
  html += `<label class="control-toggle">
    <input type="checkbox" id="toggle-labels" checked> Labels
  </label>`;

  // Type filter checkboxes — only show when there are multiple meaningful types.
  // Phase 1 stations are all "UNKNOWN"; type filters become useful in Phase 2+.
  const meaningfulTypes = [...types].filter(t => t !== "UNKNOWN").sort();
  if (meaningfulTypes.length > 0) {
    for (const type of [...types].sort()) {
      html += `<label class="control-toggle">
        <input type="checkbox" checked data-type="${type}"> ${type}
      </label>`;
    }
  }

  panel.innerHTML = html;
  document.body.appendChild(panel);

  // --- Event Listeners ---

  document.getElementById("toggle-labels").addEventListener("change", function () {
    toggleState.labels = this.checked;
    applyVisibilityState();
  });

  for (const cb of panel.querySelectorAll("[data-type]")) {
    cb.addEventListener("change", function () {
      toggleState.types[this.dataset.type] = this.checked;
      applyVisibilityState();
    });
  }
}

/**
 * Apply current toggle state to all satellites.
 * Called on toggle change and after each position refresh (from satellites.js).
 */
function applyVisibilityState() {
  for (const [noradId, entry] of satellites) {
    const meta = satelliteMetadata.get(noradId);
    const typeVisible = meta ? toggleState.types[meta.object_type] !== false : true;

    entry.point.show = typeVisible;
    entry.label.show = typeVisible && toggleState.labels;

    // If hiding the currently selected satellite, deselect it
    if (!typeVisible && selectedNoradId === noradId) {
      deselectSatellite();
    }
  }
}

initControls();
