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

// Orbit trail — PolylineCollection for GPU-batched rendering.
const trailCollection = viewer.scene.primitives.add(
  new Cesium.PolylineCollection()
);
let activeTrail = null; // current Polyline reference

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
  if (activeTrail) {
    activeTrail.show = trailVisible;
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
  if (activeTrail) {
    trailCollection.remove(activeTrail);
    activeTrail = null;
  }
}

async function fetchAndRenderTrail(noradId) {
  clearTrail();

  try {
    // Start trail 45 min in the past so the satellite sits mid-trail,
    // showing both where it's been and where it's going.
    const startTime = new Date(Date.now() - 45 * 60 * 1000).toISOString();
    const resp = await fetch(
      `/api/positions/${noradId}/track?duration_min=90&steps=120&time=${startTime}`
    );
    if (!resp.ok) return;
    const data = await resp.json();

    if (selectedNoradId !== noradId) return; // selection changed during fetch

    const positions = data.track.map(pt =>
      Cesium.Cartesian3.fromDegrees(pt.lon, pt.lat, pt.alt_km * 1000)
    );

    if (positions.length < 2) return;

    activeTrail = trailCollection.add({
      positions: positions,
      width: 2.0,
      material: Cesium.Material.fromType("Color", {
        color: new Cesium.Color(0.31, 0.76, 0.97, 0.8), // #4fc3f7 with slight transparency
      }),
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
