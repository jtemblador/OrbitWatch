/**
 * OrbitWatch — Simulated clock with play/pause and speed control.
 *
 * Manages a simulated time that can run at 1x, 10x, or 60x real-time.
 * All API calls use simClock.getTime() instead of real UTC.
 * Renders a time bar at bottom-center of the viewport.
 *
 * Depends on: nothing (loaded after app.js, before satellites.js)
 */

const simClock = (() => {
  // --- State ---
  let playing = true;
  let speed = 1;
  let baseSimTime = Date.now();   // simulated time anchor (ms)
  let baseWallTime = Date.now();  // real wall-clock anchor (ms)

  // --- Core API ---

  /** Current simulated time in milliseconds. */
  function getTimeMs() {
    if (!playing) return baseSimTime;
    return baseSimTime + (Date.now() - baseWallTime) * speed;
  }

  /** Current simulated time as ISO 8601 string for API ?time= param. */
  function getTime() {
    return new Date(getTimeMs()).toISOString();
  }

  function isPaused() {
    return !playing;
  }

  function togglePause() {
    if (playing) {
      // Pause: freeze simulated time at current value
      baseSimTime = getTimeMs();
      baseWallTime = Date.now();
      playing = false;
    } else {
      // Resume: anchor from current sim time
      baseWallTime = Date.now();
      playing = true;
    }
    updateUI();
  }

  function setSpeed(n) {
    // Re-anchor before changing speed to avoid time jumps
    baseSimTime = getTimeMs();
    baseWallTime = Date.now();
    speed = n;
    updateUI();
  }

  function getSpeed() {
    return speed;
  }

  /** Jump simulated time to an absolute instant (ms since Unix epoch).
   *  Re-anchors so the new time advances correctly from here. Used to fast-
   *  travel to a conjunction's TCA so the two satellites converge on screen. */
  function setTime(ms) {
    baseSimTime = ms;
    baseWallTime = Date.now();
    updateUI();
  }

  // --- Time Bar UI ---

  const bar = document.createElement("div");
  bar.id = "time-bar";
  bar.innerHTML = `
    <button id="time-pause" title="Pause">⏸</button>
    <span id="time-display">
      <span id="time-date"></span>
      <span class="time-edit" id="time-hh" contenteditable="true" title="Click to edit, Enter to jump"></span><span class="time-colon">:</span><span class="time-edit" id="time-mm" contenteditable="true" title="Click to edit, Enter to jump"></span><span class="time-colon">:</span><span class="time-edit" id="time-ss" contenteditable="true" title="Click to edit, Enter to jump"></span>
      <span id="time-utc">UTC</span>
    </span>
    <span id="time-speed-group">
      <button class="time-speed active" data-speed="1">1×</button>
      <button class="time-speed" data-speed="5">5×</button>
      <button class="time-speed" data-speed="10">10×</button>
    </span>
    <button id="time-live" title="Jump back to the current time">LIVE</button>
  `;
  document.body.appendChild(bar);

  const pauseBtn = bar.querySelector("#time-pause");
  const dateEl = bar.querySelector("#time-date");
  const hhEl = bar.querySelector("#time-hh");
  const mmEl = bar.querySelector("#time-mm");
  const ssEl = bar.querySelector("#time-ss");
  const editEls = [hhEl, mmEl, ssEl];

  pauseBtn.addEventListener("click", togglePause);

  // --- Editable HH:MM:SS — click a field, type, Enter (or click away) to jump ---
  function isEditingTime() {
    return editEls.includes(document.activeElement);
  }

  /** Read the three fields, clamp to valid ranges, and jump the clock to that
   *  time on the current simulated DATE. Invalid digits fall back to current. */
  function commitTimeEdit() {
    const now = new Date(getTimeMs());
    const clamp = (el, max, fallback) => {
      const n = parseInt((el.textContent || "").replace(/\D/g, ""), 10);
      return Number.isFinite(n) ? Math.min(Math.max(n, 0), max) : fallback;
    };
    const hh = clamp(hhEl, 23, now.getUTCHours());
    const mm = clamp(mmEl, 59, now.getUTCMinutes());
    const ss = clamp(ssEl, 59, now.getUTCSeconds());
    const target = Date.UTC(
      now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), hh, mm, ss);
    setTime(target);   // re-anchors; clock keeps playing from the new instant
    tick();            // normalize the fields to the committed value
  }

  // Escape must CANCEL the edit, but blur always fires commitTimeEdit — and a
  // tick() while the field still has focus won't restore it (isEditingTime()
  // gates it). So Escape sets a flag; the blur handler consumes it and restores
  // instead of committing (tick() runs post-blur, when the guard is clear).
  let cancelTimeEdit = false;
  for (const el of editEls) {
    // Select the whole field on focus so it's easy to overtype.
    el.addEventListener("focus", () => {
      const range = document.createRange();
      range.selectNodeContents(el);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    });
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); el.blur(); }      // commit
      else if (e.key === "Escape") {                                 // cancel
        e.preventDefault();
        cancelTimeEdit = true;
        el.blur();
      }
    });
    el.addEventListener("blur", () => {
      if (cancelTimeEdit) { cancelTimeEdit = false; tick(); return; }
      commitTimeEdit();
    });
  }

  bar.querySelector("#time-speed-group").addEventListener("click", (e) => {
    if (!e.target.classList.contains("time-speed")) return;
    const n = parseInt(e.target.dataset.speed);
    setSpeed(n);
  });

  // LIVE — return the clock to the present and drop any conjunction focus.
  bar.querySelector("#time-live").addEventListener("click", () => {
    setTime(Date.now());
    setSpeed(1);
    if (typeof clearConjunctionFocus === "function") clearConjunctionFocus();
  });

  function updateUI() {
    // Pause button icon
    pauseBtn.textContent = playing ? "⏸" : "▶";
    pauseBtn.title = playing ? "Pause" : "Play";

    // Speed button highlight
    for (const btn of bar.querySelectorAll(".time-speed")) {
      btn.classList.toggle("active", parseInt(btn.dataset.speed) === speed);
    }
  }

  /** Update the time display — called externally from a render loop or interval. */
  function tick() {
    const d = new Date(getTimeMs());
    const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
    dateEl.textContent = `${mon} ${d.getUTCDate()} `;
    // Don't overwrite a field the user is mid-edit in (would fight their typing).
    if (isEditingTime()) return;
    hhEl.textContent = String(d.getUTCHours()).padStart(2, "0");
    mmEl.textContent = String(d.getUTCMinutes()).padStart(2, "0");
    ssEl.textContent = String(d.getUTCSeconds()).padStart(2, "0");
  }

  // Tick the display every 250ms — fast enough to look live, cheap enough to not matter
  setInterval(tick, 250);
  tick(); // initial render

  return { getTime, getTimeMs, isPaused, togglePause, setSpeed, getSpeed, setTime };
})();
