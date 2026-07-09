"""
Synthetic dense-shell fixture for tests and profiling.

`build_synthetic_shell` produces a deterministic, OMM-shaped catalog with no
network access — used by the scale-regression tests and
scripts/profile_screening.py to exercise the screener at constellation scale.

(The synthetic "demo crosser" that once lived here — `append_demo_crosser`,
gated by ORBITWATCH_DEMO_SEED — was removed in Phase 9.9: the deployed site
screens the real active catalog, so a fabricated conjunction had no remaining
purpose.)
"""

from datetime import datetime, timezone

import pandas as pd


def build_synthetic_shell(
    n: int = 300,
    base_norad: int = 8000000,
    inclination_deg: float = 53.0,
    mean_motion: float = 15.05,   # rev/day ≈ 550 km circular
    epoch: datetime | None = None,
) -> pd.DataFrame:
    """
    Build a deterministic dense single shell — an OMM-shaped DataFrame, no
    network — for verifying the screener at ~constellation scale.

    `n` satellites share one altitude + inclination (so coarse_filter leaves
    ~all pairs and the medium filter does the real work, like a real Starlink
    shell), spread across RAAN planes × mean-anomaly slots. Columns match
    GPFetcher's parsed output so it flows through the propagator unchanged.

    Used by the dense-shell scale test; the live demo uses a real shell built
    with `python -m backend.core.tle_fetcher starlink-shell`.
    """
    from backend.core.tle_fetcher import GPFetcher

    if epoch is None:
        epoch = datetime(2026, 6, 1, tzinfo=timezone.utc)
    ecc = 0.0001
    derived = GPFetcher._derive_orbit_params(mean_motion, ecc)

    planes = max(1, round(n ** 0.5))
    slots = -(-n // planes)  # ceil(n / planes)

    rows = []
    idx = 0
    for p in range(planes):
        if idx >= n:
            break
        raan = (p / planes) * 360.0
        for s in range(slots):
            if idx >= n:
                break
            rows.append({
                "object_name": f"SYNTH-{idx:04d}",
                "object_id": f"SYNTH-{idx}",
                "norad_cat_id": base_norad + idx,
                "classification": "U",
                "epoch": epoch,
                "epoch_age_days": 0.0,
                "mean_motion": mean_motion,
                "eccentricity": ecc,
                "inclination": inclination_deg,
                "ra_of_asc_node": raan,
                "arg_of_pericenter": 0.0,
                "mean_anomaly": (s / slots) * 360.0,
                "bstar": 1e-5,
                "mean_motion_dot": 0.0,
                "mean_motion_ddot": 0.0,
                **derived,
                "object_type": "PAYLOAD",
                "rcs_size": "MEDIUM",
                "country_code": "US",
                "launch_date": "2026-01-01",
                "decay_date": None,
                "ephemeris_type": 0,
                "element_set_no": 999,
                "rev_at_epoch": 1,
                "fetch_time": epoch,
            })
            idx += 1

    return pd.DataFrame(rows)
