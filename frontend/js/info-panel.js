/**
 * OrbitWatch — Satellite info panel + orbit trail.
 *
 * Click a satellite point → bottom-left info panel with metadata + live position.
 * Orbit trail (90-min ground track) auto-renders with solid color; toggle in panel.
 *
 * Depends on: viewer, satellites, satelliteMetadata, REFRESH_INTERVAL_MS
 *   (globals from app.js / satellites.js)
 */

// --- State ---
let selectedNoradId = null;
let trailVisible = true;
let lastTrailRefresh = 0;
const TRAIL_REFRESH_MS = 30000; // re-fetch trail every 30s to keep it aligned with satellite

// Orbit trail — Entity polyline projected as ground track (industry standard).
// Rendering at orbital altitude causes perspective "lift" near the globe's limb;
// projecting onto the surface matches how satvis, trackthesky, etc. render trails.
let trailEntity = null;

// Selection indicator — cyan ring around the selected satellite.
let selectionIndicator = null;

// --- DOM: Info Panel ---
const panel = document.createElement("div");
panel.id = "info-panel";
panel.style.display = "none";
panel.innerHTML = `
  <div id="info-panel-header">
    <span id="info-panel-title"></span>
    <button id="info-panel-close" title="Close">&times;</button>
  </div>
  <table id="info-panel-table"><tbody></tbody></table>
  <label id="info-panel-trail-toggle">
    <input type="checkbox" id="trail-checkbox" checked>
    Orbit Trail
  </label>
`;
document.body.appendChild(panel);

// --- Click Handler ---
const handler = new Cesium.ScreenSpaceEventHandler(viewer.scene.canvas);

handler.setInputAction(function (click) {
  const picked = viewer.scene.pick(click.position);

  if (Cesium.defined(picked) && Cesium.defined(picked.primitive) &&
      picked.primitive.id !== undefined && satellites.has(picked.primitive.id)) {
    selectSatellite(picked.primitive.id);
  } else {
    deselectSatellite();
  }
}, Cesium.ScreenSpaceEventType.LEFT_CLICK);

document.getElementById("info-panel-close").addEventListener("click", deselectSatellite);

document.getElementById("trail-checkbox").addEventListener("change", function () {
  trailVisible = this.checked;
  if (trailEntity) {
    trailEntity.show = trailVisible;
  }
});

// --- Selection Logic ---

async function selectSatellite(noradId) {
  selectedNoradId = noradId;
  panel.style.display = "block";

  // Show name immediately from the satellites map
  const entry = satellites.get(noradId);
  const name = entry ? entry.label.text : `NORAD ${noradId}`;
  document.getElementById("info-panel-title").textContent = name;

  // Highlight selected satellite with a cyan ring
  updateSelectionIndicator(noradId);

  // Fetch fresh position + show panel data
  await refreshPanelData(noradId);

  // Fetch and render orbit trail
  trailVisible = true;
  document.getElementById("trail-checkbox").checked = true;
  await fetchAndRenderTrail(noradId);
}

function deselectSatellite() {
  selectedNoradId = null;
  panel.style.display = "none";
  clearTrail();
  clearSelectionIndicator();
}

// --- Selection Indicator (highlight selected satellite's point) ---

const SELECTED_STYLE = { pixelSize: 10, outlineColor: Cesium.Color.CYAN, outlineWidth: 3 };
const DEFAULT_STYLE = { pixelSize: 6, outlineColor: Cesium.Color.TRANSPARENT, outlineWidth: 0 };

function updateSelectionIndicator(noradId) {
  clearSelectionIndicator();
  const entry = satellites.get(noradId);
  if (!entry) return;

  // Enlarge point + add cyan outline ring
  entry.point.pixelSize = SELECTED_STYLE.pixelSize;
  entry.point.outlineColor = SELECTED_STYLE.outlineColor;
  entry.point.outlineWidth = SELECTED_STYLE.outlineWidth;
  selectionIndicator = noradId; // track which satellite is highlighted
}

function clearSelectionIndicator() {
  if (selectionIndicator !== null) {
    const entry = satellites.get(selectionIndicator);
    if (entry) {
      entry.point.pixelSize = DEFAULT_STYLE.pixelSize;
      entry.point.outlineColor = DEFAULT_STYLE.outlineColor;
      entry.point.outlineWidth = DEFAULT_STYLE.outlineWidth;
    }
    selectionIndicator = null;
  }
}

async function refreshPanelData(noradId) {
  try {
    const resp = await fetch(`/api/positions/${noradId}`);
    if (!resp.ok) return;
    const pos = await resp.json();

    const meta = satelliteMetadata.get(noradId);

    const rows = [
      ["NORAD ID", noradId],
      ["Latitude", `${pos.lat.toFixed(2)}°`],
      ["Longitude", `${pos.lon.toFixed(2)}°`],
      ["Altitude", `${pos.alt_km.toFixed(1)} km`],
      ["Speed", `${pos.speed_km_s.toFixed(2)} km/s`],
      ["Epoch Age", `${pos.epoch_age_days.toFixed(1)} days`],
    ];

    if (meta) {
      rows.push(
        ["Type", meta.object_type],
        ["Period", `${meta.period_min.toFixed(1)} min`],
        ["Inclination", `${meta.inclination_deg.toFixed(1)}°`],
        ["Apoapsis", `${meta.apoapsis_km.toFixed(1)} km`],
        ["Periapsis", `${meta.periapsis_km.toFixed(1)} km`],
      );
    }

    const tbody = document.querySelector("#info-panel-table tbody");
    tbody.innerHTML = rows
      .map(([label, value]) => `<tr><td class="info-label">${label}</td><td class="info-value">${value}</td></tr>`)
      .join("");
  } catch (err) {
    console.error("Failed to fetch position for panel:", err);
  }
}

// --- Orbit Trail ---

function clearTrail() {
  if (trailEntity) {
    viewer.entities.remove(trailEntity);
    trailEntity = null;
  }
}

async function fetchAndRenderTrail(noradId) {
  clearTrail();

  try {
    // Start trail 45 min in the past so the satellite sits mid-trail,
    // showing both where it's been and where it's going.
    const startTime = new Date(Date.now() - 45 * 60 * 1000).toISOString();
    // 360 steps (one every ~15s) eliminates chord sag between points at altitude.
    const resp = await fetch(
      `/api/positions/${noradId}/track?duration_min=90&steps=360&time=${startTime}`
    );
    if (!resp.ok) return;
    const data = await resp.json();

    if (selectedNoradId !== noradId) return; // selection changed during fetch

    // Ground track: project onto surface (industry standard for LEO trackers).
    // Rendering at orbital altitude causes perspective "lift" near the globe's limb.
    const positions = data.track.map(pt =>
      Cesium.Cartesian3.fromDegrees(pt.lon, pt.lat, 0)
    );

    if (positions.length < 2) return;

    trailEntity = viewer.entities.add({
      polyline: {
        positions: positions,
        width: 2.0,
        arcType: Cesium.ArcType.GEODESIC, // follow Earth's curvature for surface track
        material: new Cesium.Color(0.31, 0.76, 0.97, 0.8),
        clampToGround: true,
      },
      show: trailVisible,
    });

    lastTrailRefresh = performance.now();
  } catch (err) {
    console.error("Failed to fetch orbit trail:", err);
  }
}

// --- Auto-refresh panel data + trail ---
setInterval(async () => {
  if (selectedNoradId !== null) {
    await refreshPanelData(selectedNoradId);

    // Re-fetch trail every 30s so it stays anchored to the satellite's current position
    const now = performance.now();
    if (trailVisible && now - lastTrailRefresh > TRAIL_REFRESH_MS) {
      await fetchAndRenderTrail(selectedNoradId);
    }
  }
}, REFRESH_INTERVAL_MS);
