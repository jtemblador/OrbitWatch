/**
 * OrbitWatch — Minimal conjunction visualization (Task 6.8).
 *
 * Fetches /api/conjunctions once, lists the closest approaches in a corner
 * overlay, and draws a connecting line between the two satellites of each
 * non-degenerate event (highlighting their points). Crude by design — the
 * polished alert table / fly-to / detail panel come in Phase 9.
 *
 * Note on visibility: a conjunction is two objects coming *close*, so the line
 * at TCA is tiny. We instead draw a live line between the pair's current
 * points (CallbackProperty) — long while they're apart, shrinking as they
 * converge — which is visible and clearly ties the flagged pair together.
 *
 * Depends on: viewer (app.js), simClock (clock.js), satellites (satellites.js).
 * Loaded last (after info-panel.js).
 */

const CONJ_DURATION_HOURS = 24;
// Generous threshold: catches the seeded crosser regardless of the base
// satellite's current epoch, and surfaces real near-misses. Phase 7 replaces
// this with per-regime asymmetric RTN screening volumes.
const CONJ_THRESHOLD_KM = 100;
const CONJ_MAX_LINES = 3;          // how many connecting lines to draw
// Skip only essentially co-located objects (docked station modules sit at <5 m).
// Kept low so genuine sub-km conjunctions (real Starlink pairs reach ~0.3 km)
// still appear in the list.
const CONJ_MIN_VISIBLE_KM = 0.05;
const CONJ_LIST_MAX = 10;

// Entities for the connecting lines (cleared/rebuilt on each fetch).
let conjunctionLines = [];
// NORAD ids we recolored, so we can restore them.
const highlightedIds = new Set();
const CONJ_COLOR = Cesium.Color.ORANGE;

// --- DOM overlay (top-left; clear of info panel / controls / time bar) ---
const conjPanel = document.createElement("div");
conjPanel.id = "conjunction-list";
conjPanel.innerHTML =
  `<div id="conjunction-header">Conjunctions</div><div id="conjunction-body"></div>`;
document.body.appendChild(conjPanel);

/** Short UTC label, e.g. "Jun 13 07:04 UTC". */
function formatTca(iso) {
  const d = new Date(iso);
  const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  const day = d.getUTCDate();
  const hh = String(d.getUTCHours()).padStart(2, "0");
  const mm = String(d.getUTCMinutes()).padStart(2, "0");
  return `${mon} ${day} ${hh}:${mm} UTC`;
}

function clearConjunctionLines() {
  for (const e of conjunctionLines) viewer.entities.remove(e);
  conjunctionLines = [];
  for (const id of highlightedIds) {
    const entry = satellites.get(id);
    if (entry) entry.point.color = Cesium.Color.RED; // satellites.js default
  }
  highlightedIds.clear();
}

/** Live connecting line between two satellites' current interpolated points. */
function drawConjunctionLine(idA, idB) {
  const line = viewer.entities.add({
    polyline: {
      positions: new Cesium.CallbackProperty(() => {
        const a = satellites.get(idA);
        const b = satellites.get(idB);
        if (!a || !b) return [];
        return [a.point.position, b.point.position];
      }, false),
      width: 2,
      material: CONJ_COLOR,
      arcType: Cesium.ArcType.NONE,
    },
  });
  conjunctionLines.push(line);

  for (const id of [idA, idB]) {
    const entry = satellites.get(id);
    if (entry) {
      entry.point.color = CONJ_COLOR;
      highlightedIds.add(id);
    }
  }
}

function renderConjunctionList(events, totalCount) {
  document.getElementById("conjunction-header").textContent =
    `Conjunctions · ${totalCount} flagged`;
  const body = document.getElementById("conjunction-body");
  if (!events.length) {
    body.innerHTML = `<div class="conjunction-empty">No conjunctions found.</div>`;
    return;
  }
  body.innerHTML = events.slice(0, CONJ_LIST_MAX).map(e => `
    <div class="conjunction-row">
      <div class="conjunction-pair">${e.sat1_name} × ${e.sat2_name}</div>
      <div class="conjunction-meta">
        <span class="conjunction-miss">${e.miss_distance_km.toFixed(1)} km</span>
        <span class="conjunction-tca">${formatTca(e.tca)}</span>
      </div>
    </div>`).join("");
}

async function fetchConjunctions() {
  try {
    const url = `/api/conjunctions?time=${simClock.getTime()}`
      + `&duration_hours=${CONJ_DURATION_HOURS}&threshold_km=${CONJ_THRESHOLD_KM}`;
    const resp = await fetch(url);
    if (!resp.ok) {
      console.error("Failed to fetch conjunctions:", resp.status);
      return;
    }
    const data = await resp.json();

    // Drop only essentially co-located objects (docked station modules).
    const meaningful = data.events.filter(
      e => e.miss_distance_km >= CONJ_MIN_VISIBLE_KM);

    // List: closest approaches first (most significant) — the API already
    // sorts ascending by miss distance.
    renderConjunctionList(meaningful, data.count);

    // Lines: a conjunction line is tiny at the miss point (objects are close
    // by definition), so a *visible* line needs a pair currently far apart —
    // i.e. crossing geometry, which correlates with the LARGER miss distances
    // (opposing planes), not the smallest. Draw the widest flagged separations.
    clearConjunctionLines();
    const forLines = [...meaningful]
      .sort((a, b) => b.miss_distance_km - a.miss_distance_km)
      .slice(0, CONJ_MAX_LINES);
    for (const e of forLines) {
      drawConjunctionLine(e.sat1_norad_id, e.sat2_norad_id);
    }
  } catch (err) {
    console.error("Failed to fetch conjunctions:", err);
  }
}

// Fetch shortly after startup so the satellite points exist (the lines attach
// to them). The CallbackProperty tolerates points that appear later anyway.
setTimeout(fetchConjunctions, 2500);
