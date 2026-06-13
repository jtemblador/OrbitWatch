"""
Demo seed — a synthetic "crosser" conjunction for the live demo.

Real catalogs rarely contain a *visibly dramatic* close approach in any given
screening window: genuine conjunctions are close (small miss → a near-invisible
line), and constellations are station-kept to avoid them. So for the demo we
inject one synthetic object that is guaranteed to cross a real satellite.

`append_demo_crosser` clones the first satellite in the loaded catalog and
flips its orbit plane (RAAN + 180°) and phase (mean anomaly + 180.2°). Two
orbits with the same shape and opposite ascending nodes cross near the line of
nodes; the mean-anomaly offset phases the clone to arrive there at nearly the
same time → a real, repeating close approach that the screener flags.

This is demo scaffolding (gated by ORBITWATCH_DEMO_SEED — see backend/main.py)
and is removed/replaced when real dense data drives the screen in Phase 7.
"""

from datetime import datetime, timezone

import pandas as pd

# Synthetic NORAD id for the seeded crosser — high, fixed, unlikely to collide
# with any real catalog entry (real catalog numbers are currently 6 digits).
DEMO_CROSSER_ID = 9900001

# Plane/phase offsets that turn a clone into a crossing partner. The 180.2°
# (not exactly 180°) gives a small but non-zero miss rather than a perfect
# co-location; exact miss varies with the base orbit, caught by a generous
# screening threshold.
_DMO_DEG = 180.2
_DNODE_DEG = 180.0


def append_demo_crosser(df: pd.DataFrame) -> pd.DataFrame:
    """
    Return `df` with one synthetic crossing partner appended.

    The crosser clones row 0 (any catalog: a station, a Starlink sat, …) so it
    shares that orbit's altitude band — coarse_filter always pairs them — but
    sits in the opposing node, producing a genuine close approach.

    No-op (returns `df` unchanged) if the catalog is empty or already seeded,
    so it is safe to call on every data load.
    """
    if df.empty or DEMO_CROSSER_ID in df["norad_cat_id"].values:
        return df

    # iloc[[0]] (double brackets) returns a 1-row DataFrame, preserving each
    # column's dtype. iloc[0].to_frame().T would collapse the mixed row to a
    # Series and upcast every column to object on concat, contaminating the
    # whole served catalog's dtypes.
    crosser = df.iloc[[0]].copy()
    crosser["norad_cat_id"] = DEMO_CROSSER_ID
    crosser["object_name"] = "CROSSER (DEMO)"
    crosser["object_id"] = "DEMO"
    crosser["object_type"] = "PAYLOAD"
    crosser["mean_anomaly"] = (crosser["mean_anomaly"] + _DMO_DEG) % 360.0
    crosser["ra_of_asc_node"] = (crosser["ra_of_asc_node"] + _DNODE_DEG) % 360.0
    # Same orbit shape as the base → period/apoapsis/periapsis/eccentricity/
    # epoch all stay valid as-copied; only the plane and phase changed.

    return pd.concat([df, crosser], ignore_index=True)


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
