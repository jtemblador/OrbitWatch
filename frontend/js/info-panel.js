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

// Orbit trail — TWO Primitives with PolylineGeometry at orbital altitude.
// Near-side: depth test ON, bright (0.8 alpha) — only camera-facing arc visible.
// Far-side: depth test OFF, faint (0.2 alpha) — full ring visible as a ghost.
// This makes the ring structure clear: bright arc in front, faint arc behind the globe.
// Client-side densification (360 API pts × 10 = ~3600 pts) keeps chords <12 km (<1 m sag).
let trailPrimitives = [];

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
  for (const p of trailPrimitives) {
    p.show = trailVisible;
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

// Client-side spherical interpolation: adds intermediate points between each API
// sample so that Cartesian chords are short enough (<25 km) to appear smooth.
// Uses lerp + normalize-to-radius (equivalent to SLERP for small angles).
function densifyPositions(positions, factor) {
  if (factor <= 1 || positions.length < 2) return positions;
  const result = [];
  for (let i = 0; i < positions.length - 1; i++) {
    result.push(positions[i]);
    const a = positions[i];
    const b = positions[i + 1];
    const rA = Cesium.Cartesian3.magnitude(a);
    const rB = Cesium.Cartesian3.magnitude(b);
    for (let j = 1; j < factor; j++) {
      const t = j / factor;
      const interp = Cesium.Cartesian3.lerp(a, b, t, new Cesium.Cartesian3());
      const r = rA + (rB - rA) * t;
      Cesium.Cartesian3.normalize(interp, interp);
      Cesium.Cartesian3.multiplyByScalar(interp, r, interp);
      result.push(interp);
    }
  }
  result.push(positions[positions.length - 1]);
  return result;
}

function clearTrail() {
  for (const p of trailPrimitives) {
    viewer.scene.primitives.remove(p);
  }
  trailPrimitives = [];
}

async function fetchAndRenderTrail(noradId) {
  clearTrail();

  try {
    // Use the satellite's actual orbital period so the trail forms one complete loop.
    // Works for LEO (~90 min), MEO (~12 hr), GEO (~1436 min), and everything in between.
    const meta = satelliteMetadata.get(noradId);
    const durationMin = meta ? Math.ceil(meta.period_min) + 2 : 95; // +2 min overlap to close loop
    const startTime = new Date(Date.now() - (durationMin / 2) * 60 * 1000).toISOString();

    const resp = await fetch(
      `/api/positions/${noradId}/track?duration_min=${durationMin}&steps=360&time=${startTime}`
    );
    if (!resp.ok) return;
    const data = await resp.json();

    if (selectedNoradId !== noradId) return; // selection changed during fetch

    // De-rotate ECEF positions to remove Earth rotation effect.
    // Without this, Earth's ~23°/orbit rotation warps the clean orbital ellipse
    // into a helix that visibly "bends." By rotating each point's ECEF position
    // backward/forward to a single reference time, we recover the true orbital
    // plane — a clean tilted ring that passes through the satellite's current position.
    const EARTH_OMEGA = 7.2921159e-5; // rad/s (Earth's rotation rate)
    const refTime = Date.now(); // freeze Earth rotation at "now"

    let positions = data.track.map(pt => {
      const ecef = Cesium.Cartesian3.fromDegrees(pt.lon, pt.lat, pt.alt_km * 1000);
      const dt = (new Date(pt.timestamp).getTime() - refTime) / 1000;
      const theta = dt * EARTH_OMEGA;
      const cosT = Math.cos(theta);
      const sinT = Math.sin(theta);
      return new Cesium.Cartesian3(
        ecef.x * cosT - ecef.y * sinT,
        ecef.x * sinT + ecef.y * cosT,
        ecef.z
      );
    });
    // Densify to ~3600 pts so Cartesian chords are <12 km (<1 m sag).
    positions = densifyPositions(positions, 10);

    if (positions.length < 2) return;

    // Near-side primitive: depth test ON → only camera-facing arc visible, bright
    const nearInstance = new Cesium.GeometryInstance({
      geometry: new Cesium.PolylineGeometry({
        positions: positions,
        width: 2.5,
        arcType: Cesium.ArcType.NONE,
        vertexFormat: Cesium.PolylineMaterialAppearance.VERTEX_FORMAT,
      }),
    });

    // Far-side primitive: depth test OFF → full ring visible, faint ghost
    const farInstance = new Cesium.GeometryInstance({
      geometry: new Cesium.PolylineGeometry({
        positions: positions.slice(), // separate copy for second geometry
        width: 1.5,
        arcType: Cesium.ArcType.NONE,
        vertexFormat: Cesium.PolylineMaterialAppearance.VERTEX_FORMAT,
      }),
    });

    // Add far (faint) first, near (bright) second — rendering order
    const farPrim = viewer.scene.primitives.add(new Cesium.Primitive({
      geometryInstances: farInstance,
      appearance: new Cesium.PolylineMaterialAppearance({
        material: Cesium.Material.fromType("Color", {
          color: new Cesium.Color(0.31, 0.76, 0.97, 0.2),
        }),
        translucent: true,
        renderState: {
          depthTest: { enabled: false },
          depthMask: false,
        },
      }),
      asynchronous: false,
      show: trailVisible,
    }));

    const nearPrim = viewer.scene.primitives.add(new Cesium.Primitive({
      geometryInstances: nearInstance,
      appearance: new Cesium.PolylineMaterialAppearance({
        material: Cesium.Material.fromType("Color", {
          color: new Cesium.Color(0.31, 0.76, 0.97, 0.8),
        }),
        translucent: true,
      }),
      asynchronous: false,
      show: trailVisible,
    }));

    trailPrimitives = [farPrim, nearPrim];

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
