/**
 * OrbitWatch — Cesium.js globe initialization.
 *
 * Sets up the 3D viewer with minimal chrome. Satellite rendering,
 * click interaction, and orbit trails are added in later tasks.
 */

// Authenticate with Cesium Ion (token loaded from config.js)
if (typeof CESIUM_ION_TOKEN === "undefined" || CESIUM_ION_TOKEN === "YOUR_CESIUM_ION_TOKEN_HERE") {
  document.body.innerHTML =
    '<p style="color:white;font-family:monospace;padding:2em;">' +
    'Missing Cesium Ion token. Copy <code>frontend/js/config.example.js</code> ' +
    'to <code>frontend/js/config.js</code> and add your token. ' +
    'Get one free at <a href="https://ion.cesium.com/tokens" style="color:#4fc3f7;">ion.cesium.com/tokens</a></p>';
  throw new Error("CESIUM_ION_TOKEN not configured — see config.example.js");
}
Cesium.Ion.defaultAccessToken = CESIUM_ION_TOKEN;

const viewer = new Cesium.Viewer("cesiumContainer", {
  // CartoDB dark tiles — country borders + labels on a dark background.
  // Dark base makes satellite points pop visually.
  baseLayer: new Cesium.ImageryLayer(
    new Cesium.UrlTemplateImageryProvider({
      url: "https://a.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png",
      credit: "Map tiles by CartoDB, under CC BY 3.0. Data by OpenStreetMap, under ODbL.",
    })
  ),
  terrain: undefined,

  // Strip default UI widgets we don't need yet
  baseLayerPicker: false,
  geocoder: false,
  homeButton: false,
  sceneModePicker: false,
  navigationHelpButton: false,
  animation: false,
  timeline: false,
  fullscreenButton: false,
  infoBox: false,         // We'll build our own info panel (Task 4.3)
  selectionIndicator: false,

  // Render on demand, not every frame. By default Cesium redraws the globe at
  // ~60 fps forever (pinning the GPU + a core → fan noise) even when nothing
  // moves. With requestRenderMode the scene only redraws when we call
  // scene.requestRender() (satellites.js drives that at 30 fps while playing),
  // when the camera moves, or when a scene property changes — so the GPU idles
  // whenever the clock is paused or the tab is hidden. maximumRenderTimeChange
  // is Infinity because we animate off our own simClock, not Cesium's clock, so
  // Cesium must never auto-render on elapsed time.
  requestRenderMode: true,
  maximumRenderTimeChange: Infinity,
});

// Minimize credit display but keep it visible (Cesium Ion ToS requires attribution)
viewer.cesiumWidget.creditContainer.style.fontSize = "10px";

// Cap pixel ratio at 1x — saves GPU fill on integrated graphics
viewer.resolutionScale = 1.0;

// Fog is per-frame GPU cost that mostly just hazes the limb — off is cheaper
// and clearer for a satellite tracker.
viewer.scene.fog.enabled = false;
