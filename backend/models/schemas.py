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
