#!/usr/bin/env node
/**
 * Node harness for the 11.2b SOCRATES overlap line in the freshness area.
 *
 *     node tests/test_11_2b_socrates_line.js
 *
 * Locks the cross-language contract with build_snapshot.py's `meta.socrates`
 * (OPTIONAL — the deploy's reuse path serves pre-11.2b snapshots to new code):
 * the line renders only for status "ok" with both rates finite, and nothing
 * but rounded numbers reaches the innerHTML. Reads the REAL conjunctions.js.
 * Exits non-zero on failure. Not wired into CI (like the 9.10/10.10/11.0 ones).
 */

const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(
  path.join(__dirname, "..", "frontend", "js", "conjunctions.js"), "utf8");
const m = SRC.match(/function socratesAgreementText\([\s\S]*?\n}\n/);
if (!m) { console.error("socratesAgreementText not found — renamed?"); process.exit(1); }
const socratesAgreementText = new Function(`${m[0]}; return socratesAgreementText;`)();

let checks = 0, failures = 0;
function check(name, got, want) {
  checks++;
  if (got === want) return;
  failures++;
  console.error(`  FAIL  ${name}\n        got ${JSON.stringify(got)} want ${JSON.stringify(want)}`);
}

const ok = (a, b) => ({ socrates: { status: "ok",
  socrates_to_ours: { n: 10, rate: a }, ours_to_socrates: { n: 20, rate: b } } });

check("renders both rates", socratesAgreementText(ok(0.6, 0.7435)), "SOCRATES overlap 60% · 74%");
check("pre-11.2b snapshot (no block)", socratesAgreementText({ generated_at: "x" }), "");
check("null meta", socratesAgreementText(null), "");
check("unavailable stub", socratesAgreementText({ socrates: { status: "unavailable" } }), "");
check("no_overlap", socratesAgreementText({ socrates: { status: "no_overlap" } }), "");
check("empty side (rate null)", socratesAgreementText(ok(null, 0.5)), "");
check("string rate is not coerced", socratesAgreementText(ok("0.5", 0.5)), "");
check("markup can't ride a rate", socratesAgreementText(ok("<img onerror=x>", 0.5)), "");
const zero = ok(0.5, 0.5); zero.socrates.socrates_to_ours.n = 0;
check("n = 0 side hides the line", socratesAgreementText(zero), "");

console.log(`${checks - failures}/${checks} checks passed`);
process.exit(failures ? 1 : 0);
