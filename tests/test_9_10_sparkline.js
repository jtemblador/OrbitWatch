#!/usr/bin/env node
/**
 * Node harness for the 9.10 prediction-evolution trail (frontend side).
 *
 *     node tests/test_9_10_sparkline.js
 *
 * Frontend JS has no pytest coverage by convention, so the two riskiest pieces
 * of 9.10 live outside the 610-test suite:
 *
 *   1. The CROSS-LANGUAGE CONTRACT. Python writes `hist`; `_adaptConjunction`
 *      reads `c.hist`. Rename either side and the sparkline silently disappears
 *      from the site — no error, no failing test. `test_snapshot.py` locks the
 *      Python half; this locks the boundary.
 *
 *   2. THE SCALING MATH in `historySparkline` — a log axis over values that can
 *      be 0 after 3-dp rounding, with a flat-trail guard. `log10(0)` and a
 *      zero-width domain both yield NaN coordinates, which render as an
 *      invisibly broken chart rather than an exception.
 *
 * Follows 9.3's harness pattern: it reads the REAL frontend sources rather than
 * a copy, so it fails when production drifts. Exits non-zero on failure, so it
 * is CI-gateable as-is.
 */

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
let checks = 0;
let failures = 0;

function check(name, pass, detail) {
  checks++;
  if (pass) return;
  failures++;
  console.error(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
}

/** Pull named top-level functions out of a real source file by brace matching.
 *  Throws if a name is missing — a rename must break this harness loudly rather
 *  than silently testing nothing. */
function extract(file, names) {
  const src = fs.readFileSync(path.join(ROOT, file), "utf8");
  return names.map(n => {
    const start = src.indexOf(`function ${n}(`);
    if (start === -1) throw new Error(`${file}: function ${n}() not found — renamed?`);
    let depth = 0;
    for (let i = src.indexOf("{", start); i < src.length; i++) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
    }
    throw new Error(`${file}: unbalanced braces in ${n}()`);
  }).join("\n");
}

// Mutable binding the extracted code closes over, so the null case is reachable.
let snapshotMeta = { generated_at: "2026-08-18T05:45:38.026Z" };
eval(extract("frontend/js/snapshot-data.js", ["_isoToEcma", "_adaptConjunction"]));
eval(extract("frontend/js/conjunctions.js", ["missColor", "historySparkline"]));

// --- 1. cross-language contract ------------------------------------------
// Field names mirror backend/core/snapshot.py::_conjunction exactly.
const SNAPSHOT_RECORD = {
  a: 54164, b: 61678, a_name: "STARLINK-5278", b_name: "STARLINK-32363",
  a_type: "PAYLOAD", b_type: "PAYLOAD",
  tca: "2026-08-18T10:46:50.127181+00:00",
  miss_km: 0.068, rel_speed_km_s: 6.791, rtn_km: [0.012, -0.06, -0.029],
  regime: "LEO 1", hist: [[1786974980, 0.184], [1787002935, 0.184]],
};

console.log("1. snapshot.json -> frontend contract");
{
  const e = _adaptConjunction(SNAPSHOT_RECORD);
  check("hist survives the adapter as `history`",
        Array.isArray(e.history) && e.history.length === 2,
        `got ${JSON.stringify(e.history)}`);
  check("trail points stay [epochSeconds, missKm]",
        e.history[0][0] === 1786974980 && e.history[0][1] === 0.184);
  check("trail is oldest-first", e.history[0][0] < e.history[1][0]);

  // A pre-9.10 snapshot has no `hist` at all — the REUSE deploy path serves
  // exactly that to this code, so it must read as "no trail", not as a crash.
  const { hist, ...noHist } = SNAPSHOT_RECORD;
  check("absent hist -> history === null", _adaptConjunction(noHist).history === null);
}

// --- 2. sparkline rendering ----------------------------------------------
console.log("2. historySparkline");
const ev = (over = {}) => ({
  tca: "2026-08-18T12:00:00.000+00:00", miss_distance_km: 0.37,
  history: [[1787000000, 40.06], [1787030000, 1.18]], ...over,
});
const clean = svg => !/NaN|Infinity|undefined/.test(svg);

{
  const out = historySparkline(ev());
  check("renders an svg for a normal trail", out.includes("<svg") && out.includes("polyline"));
  check("no NaN/Infinity in a normal trail", clean(out));
  check("plots prior points + the live value (3 dots)",
        (out.match(/<circle/g) || []).length === 3);
  check("caption counts predictions", out.includes("3 predictions"));

  // Every point must land inside the 0..46 viewBox, or the chart silently
  // renders off-canvas instead of erroring.
  const ys = [...out.matchAll(/,(\d+\.\d+)/g)].map(m => parseFloat(m[1]));
  check("all y coordinates are inside the viewBox",
        ys.length > 0 && ys.every(y => y >= 0 && y <= 46), `ys=${ys}`);
}

check("omits the block when there is no history", historySparkline(ev({ history: null })) === "");
check("omits the block for an empty trail", historySparkline(ev({ history: [] })) === "");

{
  // 0.18 -> 0.18 -> 0.07 barely moves; without the flat-trail guard the domain
  // auto-scales to the data and a non-event is drawn as a full-height collapse.
  // PLOT_H is the drawable band (BOT - TOP in historySparkline).
  const PLOT_H = 20;
  const cy = svg => [...svg.matchAll(/<circle cx="[\d.]+" cy="([\d.]+)"/g)].map(m => +m[1]);

  const flat = historySparkline(ev({ miss_distance_km: 0.07,
                                     history: [[1787000000, 0.184], [1787030000, 0.184]] }));
  check("flat trail still renders", flat.includes("<svg"));
  check("flat trail has no NaN", clean(flat));
  const fy = cy(flat);
  check("equal values map to equal heights", fy[0] === fy[1], `${fy}`);
  const flatExtent = Math.max(...fy) - Math.min(...fy);
  check("flat trail is NOT stretched to full height (guard active)",
        flatExtent < PLOT_H * 0.9,
        `used ${flatExtent.toFixed(1)} of ${PLOT_H} — the guard is missing, so a ` +
        `0.18->0.07 nudge is drawn as a full-scale collapse`);

  // The complement: a genuine ~100x swing SHOULD use the whole band, otherwise
  // the "guard" would just be flattening every chart.
  const wide = cy(historySparkline(ev()));   // 40.06 -> 1.18 -> 0.37
  check("a real 100x swing does use the full height",
        Math.abs((Math.max(...wide) - Math.min(...wide)) - PLOT_H) < 0.01,
        `used ${(Math.max(...wide) - Math.min(...wide)).toFixed(1)} of ${PLOT_H}`);
}

{
  // miss_km is rounded to 3 dp, so a sub-metre pass serializes as 0.0 and would
  // put log10(0) = -Infinity into the domain.
  const out = historySparkline(ev({ miss_distance_km: 0, history: [[1787000000, 0.5]] }));
  check("a zero live miss does not produce NaN", clean(out), out.slice(0, 160));
}
check("an all-zero trail is omitted rather than rendered broken",
      historySparkline(ev({ miss_distance_km: 0, history: [[1787000000, 0]] })) === "");

{
  const out = historySparkline(ev({ history: [["x", "y"], [null, 3], [1787000000, 2.0]] }));
  check("malformed trail points are filtered, not rendered", clean(out) && out.includes("<svg"));
  check("only the valid point survives (2 dots)",
        (out.match(/<circle/g) || []).length === 2);
}

{
  // Runs inside the .cd-row template, so a throw takes down the whole detail
  // panel rather than just the chart.
  const saved = snapshotMeta;
  snapshotMeta = null;
  let threw = null;
  try { historySparkline(ev()); } catch (err) { threw = err.message; }
  check("null snapshotMeta is handled, not thrown", threw === null, threw);
  snapshotMeta = saved;
}

check("an unparseable TCA is handled, not thrown",
      historySparkline(ev({ tca: "not-a-date" })) === "");

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
