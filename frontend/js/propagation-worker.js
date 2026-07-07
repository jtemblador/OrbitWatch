/**
 * OrbitWatch — SGP4 propagation web worker (Phase 9.3).
 *
 * Propagates the WHOLE catalog (~11k satellites) off the main thread so the
 * globe never stutters. The main thread (satellites.js) sends the snapshot's
 * OMM records once, then asks for a full position batch at a sim time; we
 * answer with a transferable Float32Array of ECEF positions (zero-copy).
 *
 * Measured: ~13 ms per 11k-satellite batch (warm) — far inside the refresh
 * cadence, so the worker sits idle most of the time.
 *
 * Float32 is deliberate: ~0.5 m resolution at Earth-orbit magnitudes, far
 * below a screen pixel for 6-px points. Precision-sensitive readouts (info
 * panel, trails) are computed in float64 on the main thread instead.
 *
 * Protocol:
 *   in : {type:'init', satellites: [OMM…]}
 *   out: {type:'ready', n, nFailed}
 *   in : {type:'compute', timeMs}
 *   out: {type:'positions', timeMs, positions: Float32Array(3N) ECEF meters,
 *         ok: Uint8Array(N)}   — ok[i]=0 → propagation failed (decayed etc.),
 *                                mirrors propagate_batch's per-sat sentinels
 */

/* global satellite, importScripts */

// Same pinned build the page loads — keep the two in lockstep.
importScripts("https://cdn.jsdelivr.net/npm/satellite.js@6.0.1/dist/satellite.min.js");

let satrecs = [];

self.onmessage = (e) => {
  const msg = e.data;

  if (msg.type === "init") {
    // Per-satellite error tolerance: one bad record must not kill the batch.
    satrecs = msg.satellites.map((omm) => {
      try {
        const sr = satellite.json2satrec(omm);
        return sr.error === 0 ? sr : null;
      } catch (err) {
        return null;
      }
    });
    const nFailed = satrecs.filter((sr) => sr === null).length;
    if (nFailed) console.warn(`propagation-worker: ${nFailed} satrec init failures`);
    self.postMessage({ type: "ready", n: satrecs.length, nFailed });
    return;
  }

  if (msg.type === "compute") {
    const n = satrecs.length;
    const date = new Date(msg.timeMs);
    const gmst = satellite.gstime(date); // one GMST for the whole batch
    const positions = new Float32Array(3 * n);
    const ok = new Uint8Array(n);

    for (let i = 0; i < n; i++) {
      const sr = satrecs[i];
      if (!sr) continue;
      const pv = satellite.propagate(sr, date);
      if (!pv || !pv.position) continue; // decayed / diverged at this time
      const ecf = satellite.eciToEcf(pv.position, gmst);
      positions[3 * i] = ecf.x * 1000;     // km → m
      positions[3 * i + 1] = ecf.y * 1000;
      positions[3 * i + 2] = ecf.z * 1000;
      ok[i] = 1;
    }

    self.postMessage(
      { type: "positions", timeMs: msg.timeMs, positions, ok },
      [positions.buffer, ok.buffer] // transfer, don't copy
    );
  }
};
