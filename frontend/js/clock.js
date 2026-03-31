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

  // --- Time Bar UI ---

  const bar = document.createElement("div");
  bar.id = "time-bar";
  bar.innerHTML = `
    <button id="time-pause" title="Pause">⏸</button>
    <span id="time-display"></span>
    <span id="time-speed-group">
      <button class="time-speed active" data-speed="1">1×</button>
      <button class="time-speed" data-speed="10">10×</button>
      <button class="time-speed" data-speed="60">60×</button>
    </span>
  `;
  document.body.appendChild(bar);

  const pauseBtn = bar.querySelector("#time-pause");
  const display = bar.querySelector("#time-display");

  pauseBtn.addEventListener("click", togglePause);

  bar.querySelector("#time-speed-group").addEventListener("click", (e) => {
    if (!e.target.classList.contains("time-speed")) return;
    const n = parseInt(e.target.dataset.speed);
    setSpeed(n);
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
    const hh = String(d.getUTCHours()).padStart(2, "0");
    const mm = String(d.getUTCMinutes()).padStart(2, "0");
    const ss = String(d.getUTCSeconds()).padStart(2, "0");
    const mon = d.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
    const day = d.getUTCDate();
    display.textContent = `${mon} ${day}  ${hh}:${mm}:${ss} UTC`;
  }

  // Tick the display every 250ms — fast enough to look live, cheap enough to not matter
  setInterval(tick, 250);
  tick(); // initial render

  return { getTime, getTimeMs, isPaused, togglePause, setSpeed, getSpeed };
})();
