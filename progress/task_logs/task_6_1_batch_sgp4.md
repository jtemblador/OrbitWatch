# Task 6.1 — C++ Batch SGP4 (`propagate_batch`)

**Date:** Jun 11, 2026
**Status:** DONE
**Tests:** 293 passing, 1 skipped (was 280) — 13 new in `tests/test_sgp4_cpp.py::TestPropagateBatch`

---

## Goal

Propagate many satellites in a single Python→C++ crossing instead of a Python loop calling
`orbitcore.sgp4()` once per satellite. This is the batch building block for the conjunction
pipeline (Phase 6) and the Phase 7 scale-up. Per-satellite failures (decayed orbit, bad elements)
yield a `None` sentinel at that index instead of throwing — one bad satellite must not kill a
6,000-satellite screen.

---

## Approach

### Signature and contract

```
orbitcore.propagate_batch(satrecs, tsince_list) -> list[((x,y,z),(vx,vy,vz)) | None]
```

- `tsince_list` is **per-satellite minutes from each sat's own epoch** — epochs differ per
  satellite, so the caller maps a common UTC instant to per-sat tsince:
  `tsince = (jd_target − (jdsatepoch + jdsatepochF)) × 1440`. The batch deliberately does NOT
  take a UTC time (keeps the C++ side free of calendar logic; the propagator already owns it).
- Output tuples are bit-identical in shape and value to `orbitcore.sgp4()` — same code path.
- Length mismatch → `ValueError` (`"3 vs 5"`); non-Satrec item → `TypeError` naming the index;
  empty → empty.

### Items passed by reference, not copied

The lambda iterates the `py::sequence` and casts each item to `elsetrec*` — operating on the
caller's actual Satrec objects (`t`, `error` mutate, same as the single-call binding).
`std::vector<elsetrec>` via pybind11/stl was rejected: it would copy ~10 KB per satrec per call
*and* silently diverge from single-call mutation semantics.

### Why sentinels are safe

Vallado's `sgp4()` **clears `satrec.error` at the start of every call** (SGP4.cpp:1779), so a
failed propagation doesn't poison later calls on the same Satrec. Verified by test: a decayed
sat returns `None` at t=+30d, then propagates fine at t=0 with `error == 0`.

### Scope decisions

- **`get_all_positions()` NOT wired to batch** — at 25 stations there's no benefit and it touches
  the `(results, errors)` API contract. Deferred to Phase 7 (scale-up), where profiling will show
  whether the bottleneck is even the sgp4 dispatch (spoiler from the benchmark: it isn't — it'll
  be the per-sat Python transform work).
- **No GIL release** — only matters for multithreaded callers; requires a two-pass
  extract-pointers/compute/build-output structure. Deferred until profiling demands it.

---

## Implementation

| File | Change |
|------|--------|
| `orbitcore/src/bindings.cpp` | `propagate_batch` binding (~60 lines incl. docstring); header function list fixed (stale `propagate()` entry removed) |
| `tests/test_sgp4_cpp.py` | `TestPropagateBatch` — 13 tests |
| `backend/orbitcore...so` | Rebuilt + copied (now gitignored — build artifact) |

No CMakeLists change (no new source files). Rebuild: `cmake --build orbitcore/build` then
**copy the .so to `backend/`** (tests and app import from there — shadowing gotcha).

---

## Validation

- **Bit-identical to single calls** (`==`, no tolerance) across ISS (LEO), GPS (MEO), Molniya
  (HEO), including backward propagation (−30 min) and mixed-orbit single-batch calls.
- **Cross-validation vs python-sgp4:** ISS at epoch+60 min agrees sub-meter (<0.001 km). The
  identity with `sgp4()` also transitively inherits the existing TestCrossValidation +
  TestValladoVerification coverage (33 Vallado sats).
- **Sentinel:** high-bstar decayer (bstar=0.1) between two good sats → `[ok, None, ok]`,
  `decayer.error != 0` (code 1 — drag pushes ecc out of range before radius decay triggers 6).
- **Error reset:** same decayer then succeeds at tsince=0, `error == 0`.
- **Mutation by reference:** `satrec.t == 42.0` after batch — items not copied.
- **Suite:** 293 passed, 1 skipped, stable across repeated runs.

---

## Performance — the honest finding

**Batch is only ~1.05× the Python loop** (2.08 ms vs 2.20 ms for 1,000 propagations, min-of-3).
Predicted 1.5–3×; measured ~5%. Why: `sgp4()` compute (~2 µs) dominates, and the Python-facing
batch still builds three Python tuples per satellite — the only thing eliminated is per-call
dispatch overhead, which pybind11 keeps small here.

**Implication for the roadmap narrative:** the "batch SGP4 is the performance foundation" claim
needs nuance. The real order-of-magnitude win is Phase 6.3's medium filter, whose
pairs×timesteps loop runs **entirely inside C++** — positions never become Python objects at
all. `propagate_batch`'s value is the batch *semantics* (sentinels, single call site) and the
pattern it establishes, not a Python-side speedup. If Python-facing batch throughput ever
matters (Phase 7), the levers are array output (NumPy) + GIL release — both deferred.

**Test-design consequence:** asserting `batch < loop` on a ~5% margin is a flaky timing race
(the week-3 lesson). The test asserts "not meaningfully slower" (`< loop × 1.10`) and records
the measured ratio via `print` — enforcement of correctness, measurement of speed.

---

## Test Coverage

| Test | What it covers |
|------|----------------|
| `test_exposed` | Symbol present after rebuild |
| `test_matches_single_calls_exactly` | Bit-identity, 3 orbit types × 4 times (incl. backward) |
| `test_mixed_satellites_one_call` | LEO+MEO+HEO, different tsince each, one call |
| `test_result_shape_and_sanity` | Tuple shape, finite, ISS radius 6500–7000 km, speed 7–8 km/s |
| `test_empty_inputs` | `([], []) → []` |
| `test_accepts_tuple_inputs` | `py::sequence` accepts tuples; int tsince coerces |
| `test_length_mismatch_raises_valueerror` | Boundary validation with counts in message |
| `test_non_satrec_item_raises_typeerror_with_index` | str/None/int → TypeError naming index (None = segfault regression case) |
| `test_failed_sat_yields_none_others_unaffected` | Sentinel isolation mid-batch |
| `test_failed_sat_reusable_at_other_times` | Error flag reset per call |
| `test_mutates_satrec_like_single_call` | Reference semantics (`satrec.t`) |
| `test_cross_validation_vs_python_sgp4` | Sub-meter vs independent library |
| `test_batch_faster_than_python_loop` | Correctness identity + perf record + not-slower bound |

---

## Lessons Learned

- **pybind11 None→nullptr segfault:** casting a Python object to a *pointer* type
  (`item.cast<elsetrec*>()`) converts `None` to `nullptr` **without throwing** — dereferencing
  segfaulted the process (exit 139). A *reference* cast throws `cast_error` instead. When using
  pointer casts for nicer error messages, always nullptr-check. Caught in the review phase by
  testing `None` explicitly; now a permanent regression test.
- **`SGP4.cpp` is invisible to plain grep:** the file contains a stray binary byte (plus CRLF
  endings), so `grep` silently treats it as binary and reports zero matches — which looked like
  "sgp4() has no error handling." Use `grep -a`. Cost ~10 minutes of confusion.
- **`sgp4()` clears `error` per call** (SGP4.cpp:1779) — load-bearing fact for sentinel design;
  failed satellites stay usable.
- **Measure before claiming perf.** The expected "batch beats loop" headline turned out to be
  ~5%. Honest measurement redirected the perf story to the right place (6.3's all-C++ loop).

---

## Function Reference

### `orbitcore.propagate_batch(satrecs, tsince_list)`
- `satrecs`: sequence of `Satrec` (each from `sgp4init`); passed by reference (t/error mutate)
- `tsince_list`: minutes from each sat's own epoch, one per sat
- Returns `list[((x,y,z),(vx,vy,vz)) | None]` — TEME km / km·s; `None` = that sat failed
- Raises `ValueError` (length mismatch), `TypeError` (non-Satrec item, names the index)
