/**
 * OrbitWatch — Satellite search (Phase 9.6).
 *
 * A search box at top-center: type a NORAD id or a name (ISS, Starlink-1234,
 * GPS…) → an autocomplete dropdown → pick one to select it, fly the camera to
 * it, and reveal it if its group was filtered off. Pure client-side over the
 * snapshot's already-loaded metadata — no fetch.
 *
 * Depends on: viewer (app.js), simClock (clock.js), snapshotReady +
 *   satelliteMetadata + getSatName + computeEcefAt (snapshot-data.js),
 *   satellites (satellites.js), selectSatellite (info-panel.js),
 *   revealGroups / setConjOnly / conjOnlyActive (controls.js),
 *   groupCss (conjunctions.js). Loaded last.
 */

const MAX_RESULTS = 8;

// Common aliases where the searched name isn't the OBJECT_NAME (ISS already
// matches "ISS (ZARYA)" by substring; these fill the gaps).
const SEARCH_ALIASES = {
  "HUBBLE": 20580,          // OBJECT_NAME is "HST"
  "SPACE STATION": 25544,   // ISS
};

// Built from satelliteMetadata once the snapshot loads.
let searchIndex = []; // [{ id, name, upper }]

// --- DOM ---
const searchBar = document.createElement("div");
searchBar.id = "search-bar";
searchBar.innerHTML = `
  <input id="search-input" type="text" autocomplete="off" spellcheck="false"
         placeholder="Search satellites — name or NORAD id">
  <div id="search-results"></div>
`;
document.body.appendChild(searchBar);

const input = searchBar.querySelector("#search-input");
const resultsEl = searchBar.querySelector("#search-results");
let matches = [];      // current result norad ids
let highlighted = -1;  // keyboard-highlighted index

snapshotReady.then(() => {
  for (const [id, meta] of satelliteMetadata) {
    searchIndex.push({ id, name: meta.name, upper: (meta.name || "").toUpperCase() });
  }
}).catch(() => { /* snapshot load failure already surfaced elsewhere */ });

/** Match a query to up to MAX_RESULTS norad ids: exact/prefix on the NORAD id
 *  for numeric queries, name-prefix then name-substring otherwise, plus aliases. */
function runSearch(raw) {
  const q = raw.trim().toUpperCase();
  if (!q) return [];
  const out = [];
  const seen = new Set();
  const add = (id) => {
    if (!seen.has(id) && satelliteMetadata.has(id)) { seen.add(id); out.push(id); }
  };

  for (const [alias, id] of Object.entries(SEARCH_ALIASES)) {
    if (alias.includes(q)) add(id);
  }
  if (/^\d+$/.test(q)) {
    for (const it of searchIndex) {
      if (String(it.id).startsWith(q)) add(it.id);
      if (out.length >= MAX_RESULTS) break;
    }
  } else {
    for (const it of searchIndex) {           // name prefix first (best matches)
      if (it.upper.startsWith(q)) add(it.id);
      if (out.length >= MAX_RESULTS) break;
    }
    if (out.length < MAX_RESULTS) {
      for (const it of searchIndex) {          // then substring
        if (it.upper.includes(q)) add(it.id);
        if (out.length >= MAX_RESULTS) break;
      }
    }
  }
  return out.slice(0, MAX_RESULTS);
}

function dotColor(id) {
  return (typeof groupCss === "function") ? groupCss(id) : "#4fc3f7";
}

function renderResults() {
  if (!matches.length) { resultsEl.style.display = "none"; resultsEl.innerHTML = ""; return; }
  resultsEl.innerHTML = matches.map((id, i) => `
    <div class="search-result${i === highlighted ? " highlighted" : ""}" data-id="${id}">
      <span class="search-dot" style="background:${dotColor(id)}"></span>
      <span class="search-name">${getSatName(id)}</span>
      <span class="search-id">${id}</span>
    </div>`).join("");
  resultsEl.style.display = "block";
  resultsEl.querySelectorAll(".search-result").forEach((row) => {
    row.addEventListener("mousedown", (e) => {   // mousedown: fires before input blur
      e.preventDefault();
      selectResult(parseInt(row.dataset.id, 10));
    });
  });
}

function closeResults() {
  matches = [];
  highlighted = -1;
  resultsEl.style.display = "none";
  resultsEl.innerHTML = "";
}

/** Fly the camera to a satellite (position computed fresh, so it works even for
 *  a currently-hidden sat). */
function flyToSat(id) {
  let target = (typeof computeEcefAt === "function")
    ? computeEcefAt(id, simClock.getTimeMs()) : null;
  if (!target) {
    const entry = (typeof satellites !== "undefined") ? satellites.get(id) : null;
    target = entry && entry.point ? entry.point.position : null;
  }
  if (!target) return;
  viewer.camera.flyToBoundingSphere(
    new Cesium.BoundingSphere(target, 2.5e6), { duration: 1.2 });
}

function selectResult(id) {
  // Clear any active conjunction focus/isolation FIRST. A focus can leave an
  // isolationSet active while conjOnlyActive is null (e.g. group filter on →
  // click a conjunction dot); without this, the isolation mask would keep the
  // searched satellite hidden and only its stray trail/nadir would show.
  // (selectSatellite → showConjunctionsFor re-establishes the correct isolation
  // for the new selection.)
  if (typeof clearConjunctionFocus === "function") clearConjunctionFocus();
  // Make sure it's actually visible: leave the conjunction-only view if on, and
  // un-hide the sat's group.
  if (typeof conjOnlyActive !== "undefined" && conjOnlyActive &&
      typeof setConjOnly === "function") {
    setConjOnly(conjOnlyActive); // turns it off
  }
  if (typeof revealGroups === "function") revealGroups([id]);
  // selectSatellite frames the whole orbit (info-panel.js flyToOrbit); don't
  // also flyToSat, which would override it with a close-in view.
  if (typeof selectSatellite === "function") selectSatellite(id);
  input.value = getSatName(id);
  input.blur();
  closeResults();
}

// --- Input events ---
input.addEventListener("input", () => {
  matches = runSearch(input.value);
  highlighted = matches.length ? 0 : -1;
  renderResults();
});

input.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown") {
    e.preventDefault();
    if (matches.length) { highlighted = (highlighted + 1) % matches.length; renderResults(); }
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    if (matches.length) { highlighted = (highlighted - 1 + matches.length) % matches.length; renderResults(); }
  } else if (e.key === "Enter") {
    e.preventDefault();
    if (highlighted >= 0 && matches[highlighted] !== undefined) selectResult(matches[highlighted]);
  } else if (e.key === "Escape") {
    closeResults();
    input.blur();
  }
});

// Re-open results on focus if there's a query; close when clicking away.
input.addEventListener("focus", () => {
  if (input.value.trim()) { matches = runSearch(input.value); highlighted = matches.length ? 0 : -1; renderResults(); }
});
input.addEventListener("blur", () => { setTimeout(closeResults, 120); }); // let a click land first
