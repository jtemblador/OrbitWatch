#!/usr/bin/env node
/**
 * Node harness for the 11.0 trust annotations (element age, beyond-horizon,
 * persistent formations) in the frontend.
 *
 *     node tests/test_11_0_trust_fields.js
 *
 * Locks the cross-language contract — snapshot.py emits `age_a_d`/`age_b_d`,
 * `beyond_horizon`, `runs`, `persistent` (all OPTIONAL); the frontend must
 * (1) read them strictly typed, (2) hide persistent formations by default
 * BEFORE the 500 cap, like the 10.10 constellation filter, and (3) render a
 * pre-11.0 snapshot exactly as before — the deploy's reuse path serves an old
 * snapshot to new code. Reads the REAL sources (snapshot-data.js,
 * conjunctions.js), like the 9.3 / 9.10 / 10.10 harnesses. Exits non-zero on
 * failure. Not wired into CI (ci.yml's path filter is engine code).
 */

const fs = require("fs");
const path = require("path");

const JS = path.join(__dirname, "..", "frontend", "js");
const DATA = fs.readFileSync(path.join(JS, "snapshot-data.js"), "utf8");
const CONJ = fs.readFileSync(path.join(JS, "conjunctions.js"), "utf8");
let checks = 0;
let failures = 0;

function check(name, pass, detail) {
  checks++;
  if (pass) return;
  failures++;
  console.error(`  FAIL  ${name}${detail ? `\n        ${detail}` : ""}`);
}

/** Pull a top-level `const X = ...;` or `function X(` from a real source.
 *  Throws on a rename so the harness can't silently test nothing. */
function extractConst(src, file, name) {
  const m = src.match(new RegExp(`const ${name} =[\\s\\S]*?;\\n`));
  if (!m) throw new Error(`${file}: const ${name} not found — renamed?`);
  return m[0];
}
function extractFn(src, file, name) {
  const start = src.indexOf(`function ${name}(`);
  if (start === -1) throw new Error(`${file}: function ${name}() not found — renamed?`);
  let depth = 0;
  for (let i = src.indexOf("{", start); i < src.length; i++) {
    if (src[i] === "{") depth++;
    else if (src[i] === "}" && --depth === 0) return src.slice(start, i + 1);
  }
  throw new Error(`${file}: unbalanced braces in ${name}()`);
}
const D = (n) => extractFn(DATA, "snapshot-data.js", n);
const C = (n) => extractFn(CONJ, "conjunctions.js", n);

eval([
  extractConst(DATA, "snapshot-data.js", "_CONSTELLATION_RE"),
  extractConst(DATA, "snapshot-data.js", "STALE_ELEMENT_AGE_D"),
  D("constellationOf"), D("isSameConstellation"), D("isPersistentFormation"),
  D("filterConjunctions"), D("isStaleEvent"), D("_isoToEcma"), D("_adaptConjunction"),
  extractConst(CONJ, "conjunctions.js", "HORIZON_TIP"),
  extractConst(CONJ, "conjunctions.js", "STALE_TIP"),
  C("missColor"), C("missHtml"), C("ageText"), C("trustLine"),
].join("\n"));

/** A raw snapshot record (snapshot.py `_conjunction` shape). */
const raw = (over = {}) => Object.assign({
  a: 100, b: 200, a_name: "SAT-A", b_name: "SAT-B", a_type: "PAYLOAD", b_type: "PAYLOAD",
  tca: "2026-11-17T05:13:24.000000+00:00", miss_km: 1.5, rel_speed_km_s: 10.0,
  rtn_km: [0.1, 0.2, 0.3], regime: "LEO 1",
}, over);

console.log("adapter: optional fields, strictly typed");
{
  const old = _adaptConjunction(raw());   // a pre-11.0 record
  check("pre-11.0 record: ages are null, flags false, runs null",
        old.sat1_age_d === null && old.sat2_age_d === null && old.beyond_horizon === false &&
        old.persistent === false && old.runs === null);
  const cur = _adaptConjunction(raw({ age_a_d: 0.91, age_b_d: 3.4, beyond_horizon: true,
                                      runs: 12, persistent: true }));
  check("11.0 record: a→sat1, b→sat2 ages; flags carried",
        cur.sat1_age_d === 0.91 && cur.sat2_age_d === 3.4 && cur.beyond_horizon === true &&
        cur.persistent === true && cur.runs === 12);
  const junk = _adaptConjunction(raw({ age_a_d: "5", age_b_d: null, beyond_horizon: "yes",
                                       persistent: 1, runs: 2.5 }));
  check("wrong types never pass as values (no '5' string age, no truthy flags)",
        junk.sat1_age_d === null && junk.sat2_age_d === null && junk.beyond_horizon === false &&
        junk.persistent === false && junk.runs === null);
}

console.log("persistent-formation filter (default ON) — filter BEFORE the cap");
{
  const list = [];
  for (let i = 0; i < 600; i++) list.push(raw({ a: i, b: i + 1000, persistent: true, miss_km: 0.1 }));
  list.push(raw({ a: 1, b: 2, miss_km: 9.0 }), raw({ a: 3, b: 4, miss_km: 9.5 }));
  const v = filterConjunctions(list, { hidePersistent: true }, 500);
  check("hidden formations don't consume the cap: both crossings shown",
        v.shown.length === 2 && v.total === 2, `shown=${v.shown.length} total=${v.total}`);
  const off = filterConjunctions(list, { hidePersistent: false }, 500);
  check("toggle off: everything (capped)", off.total === 602 && off.shown.length === 500);
  const old = [raw(), raw({ a: 5 })];
  check("pre-11.0 snapshot: hiding formations is a no-op",
        filterConjunctions(old, { hidePersistent: true }, 500).total === 2);
  const both = [raw({ a_name: "STARLINK-1", b_name: "STARLINK-2" }),
                raw({ persistent: true }), raw({ a_name: "STARLINK-3", b_name: "ONEWEB-1" })];
  const vb = filterConjunctions(both, { hideSame: true, hidePersistent: true }, 500);
  check("filters compose (same-constellation AND persistent removed)",
        vb.total === 1 && vb.shown[0].b_name === "ONEWEB-1");
  check("persistent: only an explicit true counts",
        !isPersistentFormation({ persistent: "true" }) && !isPersistentFormation({ persistent: 1 }));
}

console.log("stale-elements tag (> 3 d on either side)");
check("fresh both sides → not stale", !isStaleEvent({ sat1_age_d: 0.9, sat2_age_d: 1.7 }));
check("exactly 3 d → not stale (strict >)", !isStaleEvent({ sat1_age_d: 3.0, sat2_age_d: 0.5 }));
check("either side over → stale",
      isStaleEvent({ sat1_age_d: 0.5, sat2_age_d: 3.01 }) &&
      isStaleEvent({ sat1_age_d: 17.2, sat2_age_d: null }));
check("unknown ages (pre-11.0) → not stale", !isStaleEvent({ sat1_age_d: null, sat2_age_d: null }));

console.log("rendering");
{
  const e = { miss_distance_km: 0.119, beyond_horizon: false };
  const plain = missHtml(e, 2);
  check("ordinary miss: plain 'X km', no ≥, no tooltip",
        plain.includes(">0.12 km<") && !plain.includes("≥") && !plain.includes("title="));
  const ub = missHtml({ miss_distance_km: 4.25, beyond_horizon: true }, 1);
  check("beyond-horizon miss: '≥ X km' + upper-bound tooltip",
        ub.includes("≥ 4.3 km") && ub.includes("upper bound") && ub.includes("beyond-horizon"));
  check("age text: hours under a day, days above, ? when unknown",
        ageText(0.5) === "12 h" && ageText(1.25) === "1.3 d" && ageText(null) === "?");
  const pre = { sat1_age_d: null, sat2_age_d: null, persistent: false, runs: null };
  check("pre-11.0 event: trust line renders NOTHING (row unchanged)", trustLine(pre) === "");
  const stale = trustLine({ sat1_age_d: 0.5, sat2_age_d: 4.2, persistent: false, runs: null });
  check("stale event: both ages + 'low confidence (stale elements)' tag",
        stale.includes("12 h") && stale.includes("4.2 d") &&
        stale.includes("low confidence (stale elements)"));
  const fresh = trustLine({ sat1_age_d: 0.5, sat2_age_d: 1.2, persistent: false, runs: null });
  check("fresh event: ages, no stale tag", fresh.includes("1.2 d") && !fresh.includes("low confidence"));
  const form = trustLine({ sat1_age_d: 0.8, sat2_age_d: 0.8, persistent: true, runs: 365 });
  check("persistent event: tagged with its run count",
        form.includes("persistent formation") && form.includes("365 runs"));
}

console.log("wiring");
check("the persistent toggle starts CHECKED (hidden by default)",
      /id="conj-hide-persistent"[^>]*\bchecked\b/.test(CONJ));
check("the default filter state hides persistent formations",
      /let conjHidePersistent = true;/.test(CONJ));
check("the load path builds the set from the filtered view, not the raw list",
      /snapshotReady\.then\(\(\) => \{[\s\S]*?currentConjunctionView\(\)/.test(CONJ));

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
