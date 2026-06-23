"""
Pydantic response models for the OrbitWatch API.

These models serve two purposes:
1. Auto-generate typed OpenAPI/Swagger docs at /docs
2. Validate response serialization (catches stray numpy types, missing fields)
"""

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Satellites
# ---------------------------------------------------------------------------

class SatelliteInfo(BaseModel):
    name: str
    norad_id: int
    object_type: str
    epoch: str
    epoch_age_days: float
    period_min: float
    inclination_deg: float
    apoapsis_km: float
    periapsis_km: float


class SatelliteListResponse(BaseModel):
    count: int
    group: str
    satellites: list[SatelliteInfo]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

class PositionResult(BaseModel):
    name: str
    norad_id: int
    lat: float
    lon: float
    alt_km: float
    speed_km_s: float
    epoch_age_days: float


class PositionError(BaseModel):
    name: str
    norad_id: int
    reason: str


class BatchPositionResponse(BaseModel):
    count: int
    timestamp: str
    positions: list[PositionResult]
    errors: list[PositionError] | None = None


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------

class TrackPoint(BaseModel):
    lat: float
    lon: float
    alt_km: float
    teme_x: float
    teme_y: float
    teme_z: float
    timestamp: str


class TrackResponse(BaseModel):
    norad_id: int
    name: str
    duration_min: int
    steps: int
    track: list[TrackPoint]


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

class RefreshResponse(BaseModel):
    status: str
    group: str
    satellite_count: int
    fetch_time: str


# ---------------------------------------------------------------------------
# Conjunctions
# ---------------------------------------------------------------------------

class ConjunctionEvent(BaseModel):
    """One close-approach event, in the CDM-like geometry our pipeline
    produces. Geometric screening only — no Pc (see project scope)."""
    sat1_norad_id: int
    sat1_name: str
    sat1_object_type: str
    sat1_epoch_age_days: float
    sat2_norad_id: int
    sat2_name: str
    sat2_object_type: str
    sat2_epoch_age_days: float

    tca: str                    # Time of Closest Approach, ISO 8601 UTC
    miss_distance_km: float
    relative_speed_km_s: float

    # Miss vector in the primary's RTN frame (the frame CDMs use)
    r_km: float                 # radial
    t_km: float                 # in-track
    n_km: float                 # cross-track

    # SFS screening regime whose RTN ellipsoid this event satisfies (e.g.
    # "LEO 1"); "" for the legacy Euclidean-threshold path.
    screening_regime: str = ""


class ConjunctionResponse(BaseModel):
    count: int                  # at-risk pairs (de-duped) in SFS mode
    screening_start: str        # ISO 8601 UTC
    duration_hours: float
    # SFS ellipsoidal screening volumes (per-regime) when null; a number means
    # the legacy Euclidean miss threshold (km) was used instead.
    threshold_km: float | None
    # Co-located / persistent-proximity pairs dropped (docked modules, parked
    # formations) — 0 in legacy mode. See screening_volumes / Conservative-drop.
    suppressed_count: int = 0
    # Catalog freshness: when the GP data was last fetched, and the oldest
    # orbital epoch in the set (epoch age drives SGP4 accuracy — see Phase 8
    # epoch-matching). last_fetched is None if the catalog carries no fetch time.
    last_fetched: str | None
    data_max_epoch_age_days: float
    events: list[ConjunctionEvent]
