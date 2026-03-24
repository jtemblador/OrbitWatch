"""
Satellite metadata and position endpoints.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

router = APIRouter(prefix="/api")


@router.get("/satellites")
async def list_satellites(request: Request):
    """Return metadata for all satellites in the group (no propagation needed)."""
    # Phase 1: single shared propagator, always "stations".
    # Multi-group support deferred to Phase 2.
    propagator = request.app.state.propagator
    df = propagator._ensure_data()
    group = propagator.group
    now = datetime.now(timezone.utc)

    satellites = []
    for i in range(len(df)):
        row = df.iloc[i]
        # Recompute epoch age from current time (cached value goes stale)
        epoch = row["epoch"]
        if hasattr(epoch, "to_pydatetime"):
            epoch = epoch.to_pydatetime()
        if epoch.tzinfo is None:
            epoch = epoch.replace(tzinfo=timezone.utc)
        epoch_age_days = round((now - epoch).total_seconds() / 86400.0, 2)

        satellites.append({
            "name": row["object_name"],
            "norad_id": int(row["norad_cat_id"]),
            "object_type": row["object_type"] if pd.notna(row["object_type"]) else "UNKNOWN",
            "epoch": epoch.isoformat(),
            "epoch_age_days": epoch_age_days,
            "period_min": float(row["period"]),
            "inclination_deg": float(row["inclination"]),
            "apoapsis_km": float(row["apoapsis"]),
            "periapsis_km": float(row["periapsis"]),
        })

    return {
        "count": len(satellites),
        "group": group,
        "satellites": satellites,
    }


def _parse_time(time: str | None) -> datetime:
    """Parse optional ISO 8601 time param, default to utcnow()."""
    if time is None:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(time)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _format_position(result: dict) -> dict:
    """Map propagator result dict to API response format."""
    return {
        "name": result["name"],
        "norad_id": int(result["norad_id"]),
        "lat": float(result["lat"]),
        "lon": float(result["lon"]),
        "alt_km": float(result["alt"]),
        "speed_km_s": float(result["speed_km_s"]),
        "epoch_age_days": float(result["epoch_age_days"]),
    }


@router.get("/positions")
async def get_positions(request: Request, time: str | None = None):
    """Batch-propagate all satellites to current or specified UTC time."""
    propagator = request.app.state.propagator
    try:
        utc_dt = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    results, errors = propagator.get_all_positions(utc_dt)

    positions = [_format_position(r) for r in results]
    response = {
        "count": len(positions),
        "timestamp": utc_dt.isoformat(),
        "positions": positions,
    }
    if errors:
        response["errors"] = errors
    return response


@router.get("/positions/{norad_id}")
async def get_position(request: Request, norad_id: int, time: str | None = None):
    """Propagate a single satellite by NORAD catalog number."""
    propagator = request.app.state.propagator
    try:
        utc_dt = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    try:
        result = propagator.get_position_by_norad_id(norad_id, utc_dt)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"NORAD ID {norad_id} not found")
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=f"Propagation failed for NORAD ID {norad_id}: {e}")

    return _format_position(result)


@router.get("/positions/{norad_id}/track")
async def get_track(
    request: Request,
    norad_id: int,
    duration_min: int = Query(default=90, ge=1, le=1440),
    steps: int = Query(default=60, ge=2, le=500),
    time: str | None = None,
):
    """Return ground track points for orbit trail rendering."""
    propagator = request.app.state.propagator
    try:
        utc_dt = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    # Look up satellite name from NORAD ID
    try:
        row = propagator.find_by_norad_id(norad_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"NORAD ID {norad_id} not found")

    name = row["object_name"]

    # Generate evenly-spaced time steps
    step_size = timedelta(minutes=duration_min / steps)
    utc_dts = [utc_dt + step_size * i for i in range(steps)]

    try:
        results = propagator.get_positions_at_times(name, utc_dts)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=f"Propagation failed for NORAD ID {norad_id}: {e}")

    track = [
        {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "alt_km": float(r["alt"]),
            "timestamp": r["timestamp"].isoformat(),
        }
        for r in results
    ]

    return {
        "norad_id": norad_id,
        "name": name,
        "duration_min": duration_min,
        "steps": steps,
        "track": track,
    }
