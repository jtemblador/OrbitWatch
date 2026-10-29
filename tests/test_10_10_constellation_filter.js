#!/usr/bin/env node
/**
 * Node harness for the 10.10 "Hide same-constellation pairs" filter.
 *
 *     node tests/test_10_10_constellation_filter.js
 *
 * The matcher is name-based, so its failure mode is silent: a regex that
 * over-matches hides real cross-operator conjunctions; one that under-matches
 * leaves the Starlink × Starlink flood in place. Reads the REAL source
 * (frontend/js/snapshot-data.js), like the 9.3 / 9.10 harnesses. Exits
 * non-zero on failure.
 */

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "frontend", "js", "snapshot-data.js"), "utf8");
let checks = 0;
let failures = 0;

function check(name, pass, detail) {
  checks++;
  if (pass) return;
  failures++;
  console.error(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
}

/** A top-level statement (`const X = ...;`) or function, pulled from the real
 *  source. Throws on a rename so the harness can't silently test nothing. */
function extractConst(name) {
  const m = SRC.match(new RegExp(`const ${name} =[\\s\\S]*?;\\n`));
  if (!m) throw new Error(`snapshot-data.js: const ${name} not found — renamed?`);
  return m[0];
}
function extractFn(name) {
  const start = SRC.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`snapshot-data.js: function ${name}() not found — renamed?`);
  let depth = 0;
  for (let i = SRC.indexOf("{", start); i < SRC.length; i++) {
    if (SRC[i] === "{") depth++;
    else if (SRC[i] === "}" && --depth === 0) return SRC.slice(start, i + 1);
  }
  throw new Error(`snapshot-data.js: unbalanced braces in ${name}()`);
}

eval(extractConst("_CONSTELLATION_RE") + extractFn("constellationOf") +
     extractFn("isSameConstellation"));

const pair = (a, b) => ({ a_name: a, b_name: b });

console.log("constellation matcher");
check("Starlink x Starlink is same-constellation",
      isSameConstellation(pair("STARLINK-5278", "STARLINK-32363")));
check("Starlink x debris is cross",
      !isSameConstellation(pair("STARLINK-5278", "COSMOS 1408 DEB")));
check("Starlink x OneWeb is cross (two operators)",
      !isSameConstellation(pair("STARLINK-5278", "ONEWEB-0012")));
check("Planet's FLOCK and SKYSAT are one constellation",
      isSameConstellation(pair("FLOCK 4Q-16", "SKYSAT-A")));
check("prefix needs a word boundary (HAWKSAT is not HAWK)",
      constellationOf("HAWKSAT-1") === null && constellationOf("HAWK-8A") === "HAWK");
check("matches only at the start of the name",
      constellationOf("OBJECT STARLINK") === null);
check("unlisted families stay visible (YAOGAN x YAOGAN is not hidden)",
      !isSameConstellation(pair("YAOGAN-2", "YAOGAN-3")));
check("missing names never count as same",
      !isSameConstellation(pair(undefined, undefined)) &&
      !isSameConstellation(pair(null, "STARLINK-1")));
check("Guowang test sats (HULIANWANG) group together",
      isSameConstellation(pair("HULIANWANG JISHU SHIYAN 1A", "HULIANWANG JISHU SHIYAN 1B")));

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
