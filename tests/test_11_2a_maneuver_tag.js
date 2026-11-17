#!/usr/bin/env node
/**
 * Node harness for the 11.2a maneuver label in the frontend.
 *
 *     node tests/test_11_2a_maneuver_tag.js
 *
 * Locks the cross-language contract: snapshot.py (backend/core/maneuvers.py
 * annotate_conjunctions) emits OPTIONAL `mnv_h` (hours, ≤ 48) and `mnv_who`
 * ("a" | "b" | "ab"); the frontend must (1) read them strictly typed, (2) show
 * "<who> maneuvered N h ago" in the conjunction detail with a tooltip that says
 * the prediction may be stale AND that no tag does not mean no maneuver, (3)
 * escape the upstream name, and (4) render a pre-11.2a snapshot exactly as
 * before (the reuse deploy path serves an old snapshot to new code). Reads the
 * REAL sources, like the 9.10 / 10.10 / 11.0 harnesses. Exits non-zero on
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
// eval-scoped consts are invisible out here — evaluate the tooltip on its own.
const TIP = new Function(`${extractConst(CONJ, "conjunctions.js", "MANEUVER_TIP")} return MANEUVER_TIP;`)();

eval([
  extractConst(DATA, "snapshot-data.js", "STALE_ELEMENT_AGE_D"),
  D("escapeHtml"), D("isStaleEvent"), D("_isoToEcma"), D("_adaptConjunction"),
  extractConst(CONJ, "conjunctions.js", "STALE_TIP"),
  extractConst(CONJ, "conjunctions.js", "MANEUVER_TIP"),
  C("ageText"), C("maneuverWho"), C("maneuverTag"), C("trustLine"),
].join("\n"));

const raw = (over = {}) => Object.assign({
  a: 100, b: 200, a_name: "SAT-A", b_name: "SAT-B", a_type: "PAYLOAD", b_type: "PAYLOAD",
  tca: "2026-11-17T05:13:24.000000+00:00", miss_km: 1.5, rel_speed_km_s: 10.0,
  rtn_km: [0.1, 0.2, 0.3], regime: "LEO 1",
}, over);

console.log("adapter: optional, strictly typed");
{
  const old = _adaptConjunction(raw());
  check("pre-11.2a record: mnv_h / mnv_who null", old.mnv_h === null && old.mnv_who === null);
  const cur = _adaptConjunction(raw({ mnv_h: 4.6, mnv_who: "ab" }));
  check("11.2a record carried", cur.mnv_h === 4.6 && cur.mnv_who === "ab");
  check("0 h is a value, not 'absent'", _adaptConjunction(raw({ mnv_h: 0, mnv_who: "a" })).mnv_h === 0);
  for (const [h, w] of [["5", "a"], [-1, "a"], [null, "b"], [true, "a"], [3, "c"], [3, 1], [3, "A"]]) {
    const j = _adaptConjunction(raw({ mnv_h: h, mnv_who: w }));
    check(`junk (${JSON.stringify(h)}, ${JSON.stringify(w)}) never passes as a value`,
          (h === 3 ? j.mnv_who === null : j.mnv_h === null),
          `got mnv_h=${j.mnv_h} mnv_who=${j.mnv_who}`);
  }
}

console.log("tag rendering");
{
  const ev = (over) => Object.assign(_adaptConjunction(raw()), over);
  check("no label → empty", maneuverTag(ev({})) === "");
  check("hours without who → empty (half a label is no label)",
        maneuverTag(ev({ mnv_h: 3 })) === "");
  const a = maneuverTag(ev({ mnv_h: 4.6, mnv_who: "a" }));
  check("who=a: names object A, rounds the hours", a.includes("SAT-A maneuvered 5 h ago"), a);
  check("who=b: names object B",
        maneuverTag(ev({ mnv_h: 30, mnv_who: "b" })).includes("SAT-B maneuvered 30 h ago"));
  check("who=ab: 'both'", maneuverTag(ev({ mnv_h: 12, mnv_who: "ab" })).includes("both maneuvered 12 h ago"));
  check("under an hour: '<1 h'", maneuverTag(ev({ mnv_h: 0.4, mnv_who: "a" })).includes("&lt;1 h ago") ||
        maneuverTag(ev({ mnv_h: 0.4, mnv_who: "a" })).includes("<1 h ago"));
  const x = maneuverTag(ev({ mnv_h: 2, mnv_who: "a", sat1_name: '<img src=x onerror=alert(1)>"' }));
  check("upstream name is escaped", !x.includes("<img") && x.includes("&lt;img"), x);
  check("tooltip: prediction may be stale", a.includes("may be stale"));
  check("tooltip: no tag does NOT mean no maneuver", /No tag does NOT mean no maneuver/.test(a));
  check("tooltip: states the detection floor", a.includes("100–300 m") && a.includes("3x/day"));
  check("tooltip has no double quote (would break the title attribute)", !TIP.includes('"'));
  check("tooltip never calls an untagged event stable", !/\bstable\b/i.test(TIP));
}

console.log("trust line");
{
  const pre = { sat1_age_d: null, sat2_age_d: null, persistent: false, runs: null,
                mnv_h: null, mnv_who: null };
  check("pre-11.0/11.2a event: still renders NOTHING", trustLine(pre) === "");
  const only = trustLine(Object.assign({}, pre, { mnv_h: 6, mnv_who: "ab",
                                                   sat1_name: "A", sat2_name: "B" }));
  check("a label alone still renders the line", only.includes("cd-trust") && only.includes("mnv-tag"));
  const withAges = trustLine({ sat1_age_d: 0.5, sat2_age_d: 0.7, persistent: false, runs: null,
                               mnv_h: 6, mnv_who: "a", sat1_name: "STARLINK-1", sat2_name: "X" });
  check("alongside the element ages",
        withAges.includes("elements 12 h") && withAges.includes("STARLINK-1 maneuvered 6 h ago"));
  const noMnv = trustLine({ sat1_age_d: 0.5, sat2_age_d: 0.7, persistent: false, runs: null,
                            mnv_h: null, mnv_who: null });
  check("untagged event: no maneuver text at all", !noMnv.includes("maneuver"));
}

console.log("wiring");
check("the detail panel renders trustLine(e)", /\$\{trustLine\(e\)\}/.test(CONJ));
check("the tag has its CSS class",
      fs.readFileSync(path.join(__dirname, "..", "frontend", "css", "style.css"), "utf8")
        .includes(".mnv-tag"));

console.log(`\n${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
