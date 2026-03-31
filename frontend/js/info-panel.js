/**
 * OrbitWatch — Satellite info panel + orbit trail.
 *
 * Click a satellite point → bottom-left info panel with metadata + live position.
 * Orbit trail (full-period ring at orbital altitude) auto-renders with solid color; toggle in panel.
 *
 * Depends on: viewer, satellites, satelliteMetadata, getRefreshInterval
 *   (globals from app.js / satellites.js)
 */

// --- State ---
let selectedNoradId = null;
let trailVisible = true;
let lastTrailRefresh = 0;
const BASE_TRAIL_REFRESH_MS = 30000; // re-fetch trail every 30s at 1x

/** Trail refresh interval — scales with speed so trail stays centered at high speeds. */
function getTrailRefreshInterval() {
  return Math.max(Math.floor(BASE_TRAIL_REFRESH_MS / simClock.getSpeed()), 5000);
}

// Orbit trail — TWO Primitives with PolylineGeometry at orbital altitude.
// Near-side: depth test ON, bright (0.8 alpha) — only camera-facing arc visible.
// Far-side: depth test OFF, faint (0.2 alpha) — full ring visible as a ghost.
// This makes the ring structure clear: bright arc in front, faint arc behind the globe.
// API returns TEME (inertial) positions — orbit is a clean near-ellipse in TEME.
// One GMST rotation places all points in the current ECEF frame for Cesium.
// Client-side densification (360 API pts × 10 = ~3600 pts) keeps chords <12 km (<1 m sag).
let trailPrimitives = [];

// Nadir line — vertical line from sub-satellite ground point to satellite at altitude.
// Uses a Cesium Entity with CallbackProperty to track the interpolated position every frame.
let nadirEntity = null;

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

  // Fetch and render orbit trail + nadir line
  trailVisible = true;
  document.getElementById("trail-checkbox").checked = true;
  createNadirLine(noradId);
  await fetchAndRenderTrail(noradId);
}

function deselectSatellite() {
  selectedNoradId = null;
  panel.style.display = "none";
  clearTrail();
  cachedDenseTEME = null;
  clearNadirLine();
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
    const resp = await fetch(`/api/positions/${noradId}?time=${simClock.getTime()}`);
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

// --- Nadir Line ---
// Uses CallbackProperty to read the satellite's current interpolated position
// every frame, so the line moves smoothly with the point (no 5s lag).

function createNadirLine(noradId) {
  clearNadirLine();
  nadirEntity = viewer.entities.add({
    polyline: {
      positions: new Cesium.CallbackProperty(() => {
        const entry = satellites.get(noradId);
        if (!entry) return [];
        const satPos = entry.point.position;
        // Project to surface: normalize to unit vector, scale to Earth radius
        const surface = new Cesium.Cartesian3();
        Cesium.Cartesian3.normalize(satPos, surface);
        Cesium.Cartesian3.multiplyByScalar(surface, Cesium.Ellipsoid.WGS84.maximumRadius, surface);
        return [surface, satPos];
      }, false),
      width: 1.5,
      material: new Cesium.Color(0.31, 0.76, 0.97, 0.4),
      arcType: Cesium.ArcType.NONE,
    },
  });
}

function clearNadirLine() {
  if (nadirEntity) {
    viewer.entities.remove(nadirEntity);
    nadirEntity = null;
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

/** IAU 1982 GMST from Unix milliseconds. Returns angle in radians. */
function computeGmst(simMs) {
  const jd = simMs / 86400000 + 2440587.5;
  const T = (jd - 2451545.0) / 36525.0;
  const sec = 67310.54841
    + (876600.0 * 3600.0 + 8640184.812866) * T
    + 0.093104 * T * T
    - 6.2e-6 * T * T * T;
  return (sec * Math.PI / 43200.0) % (2.0 * Math.PI);
}

// Cache for client-side trail re-rotation — avoids API call when only Earth has rotated.
// Stores ~3600 densified TEME Cartesian3 (meters). Set by fetchAndRenderTrail,
// cleared on deselect or new selection.
let cachedDenseTEME = null;
let lastTrailRotation = 0;

function clearTrail() {
  for (const p of trailPrimitives) {
    viewer.scene.primitives.remove(p);
  }
  trailPrimitives = [];
}

/** Build near+far trail primitives from ECEF positions array. */
function buildTrailPrimitives(positions) {
  clearTrail();
  if (positions.length < 2) return;

  const nearInstance = new Cesium.GeometryInstance({
    geometry: new Cesium.PolylineGeometry({
      positions: positions,
      width: 2.5,
      arcType: Cesium.ArcType.NONE,
      vertexFormat: Cesium.PolylineMaterialAppearance.VERTEX_FORMAT,
    }),
  });

  const farInstance = new Cesium.GeometryInstance({
    geometry: new Cesium.PolylineGeometry({
      positions: positions.slice(),
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
}

/**
 * Rotate cached dense TEME positions to current ECEF and rebuild trail primitives.
 * Pure client-side — no API call. Cost: ~3600 rotations + 2 primitive rebuilds.
 */
function renderTrailFromCache() {
  if (!cachedDenseTEME || cachedDenseTEME.length < 2) return;

  const gmst = computeGmst(simClock.getTimeMs());
  const cosG = Math.cos(gmst);
  const sinG = Math.sin(gmst);

  const ecef = cachedDenseTEME.map(pt =>
    new Cesium.Cartesian3(
       cosG * pt.x + sinG * pt.y,
      -sinG * pt.x + cosG * pt.y,
      pt.z
    )
  );

  buildTrailPrimitives(ecef);
  lastTrailRotation = performance.now();
}

async function fetchAndRenderTrail(noradId) {
  clearTrail();
  cachedDenseTEME = null;

  try {
    // Use the satellite's actual orbital period so the trail forms one complete loop.
    const meta = satelliteMetadata.get(noradId);
    const durationMin = meta ? Math.ceil(meta.period_min) : 93; // one full orbit
    const simNowMs = simClock.getTimeMs();
    const startTime = new Date(simNowMs - (durationMin / 2) * 60 * 1000).toISOString();

    const resp = await fetch(
      `/api/positions/${noradId}/track?duration_min=${durationMin}&steps=360&time=${startTime}`
    );
    if (!resp.ok) return;
    const data = await resp.json();

    if (selectedNoradId !== noradId) return; // selection changed during fetch

    // Single-GMST rotation: all TEME points rotated by the same angle so the
    // trail forms a clean closed orbital ring. Re-rotation via renderTrailFromCache()
    // keeps it aligned with the satellite at accelerated speeds.
    const rawTEME = data.track.map(pt =>
      new Cesium.Cartesian3(pt.teme_x * 1000, pt.teme_y * 1000, pt.teme_z * 1000)
    );
    cachedDenseTEME = densifyPositions(rawTEME, 10);

    renderTrailFromCache();
    lastTrailRefresh = performance.now();
  } catch (err) {
    console.error("Failed to fetch orbit trail:", err);
  }
}

// Re-rotate trail at high speeds so it stays aligned with the satellite point.
// At speed > 1, Earth rotation causes visible drift — this re-applies the current
// GMST angle to cached TEME data (no API call, ~3600 rotations + 2 primitive rebuilds).
viewer.scene.preRender.addEventListener(() => {
  if (simClock.getSpeed() <= 1 || simClock.isPaused()) return;
  if (!cachedDenseTEME || !trailVisible) return;
  const now = performance.now();
  if (now - lastTrailRotation < 500) return;
  renderTrailFromCache();
});

// --- Auto-refresh panel data + trail ---
// Self-scheduling loop — adapts interval to clock speed (mirrors satellites.js pattern).
(function schedulePanelRefresh() {
  setTimeout(async () => {
    if (selectedNoradId !== null && !simClock.isPaused()) {
      await refreshPanelData(selectedNoradId);

      // Re-fetch trail at speed-scaled interval so it stays centered at high speeds
      const now = performance.now();
      if (trailVisible && now - lastTrailRefresh > getTrailRefreshInterval()) {
        await fetchAndRenderTrail(selectedNoradId);
      }
    }
    schedulePanelRefresh();
  }, getRefreshInterval());
})();
