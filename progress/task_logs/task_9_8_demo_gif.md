# Task 9.8 — Demo GIF

**Date:** Jul 8, 2026
**Status:** DONE
**Tests:** none (docs-only).

Jose screen-recorded a ~89 s walkthrough of the live site (startup conjunction
view → search → focusing a conjunction → TCA playback with the ephemeris).
Converted to `docs/img/demo.gif` with ffmpeg: browser chrome cropped
(`crop=2048:1086:0:66`), 800 px wide, 10 fps, two-pass palette
(`palettegen=stats_mode=diff` + `paletteuse=dither=bayer`) → **9.0 MB** for the
full clip (the dark scene diff-compresses well; no trim/speed-up needed).

README: the GIF replaced the static hero screenshot in the same clickable
live-site link (the GIF opens on that exact view); `live-conjunction-view.png`
stays in `docs/img/` for other uses.

**Gotcha worth keeping:** OBS/desktop recordings need the browser chrome cropped
before GIF-ing, and GIF size is dominated by per-frame pixel *change*, not
duration — a dark, mostly-static scene at 10 fps costs ~100 KB/s; two-pass
palette with `stats_mode=diff` + bayer dither is the right default.
