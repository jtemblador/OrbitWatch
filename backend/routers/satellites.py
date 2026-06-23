"""
Satellite metadata, position, and data refresh endpoints.
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool

from backend.core.conjunctions import ConjunctionScreener
from backend.models.schemas import (
    BatchPositionResponse,
    ConjunctionResponse,
    PositionResult,
    RefreshResponse,
    SatelliteListResponse,
    TrackResponse,
)

router = APIRouter(prefix="/api")


@router.get("/satellites", response_model=SatelliteListResponse)
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


@router.get("/positions", response_model=BatchPositionResponse)
async def get_positions(request: Request, time: str | None = None):
    """Batch-propagate all satellites to current or specified UTC time."""
    propagator = request.app.state.propagator
    try:
        utc_dt = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    async with _propagator_lock:
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


@router.get("/positions/{norad_id}", response_model=PositionResult)
async def get_position(request: Request, norad_id: int, time: str | None = None):
    """Propagate a single satellite by NORAD catalog number."""
    propagator = request.app.state.propagator
    try:
        utc_dt = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    try:
        async with _propagator_lock:
            result = propagator.get_position_by_norad_id(norad_id, utc_dt)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"NORAD ID {norad_id} not found")
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=f"Propagation failed for NORAD ID {norad_id}: {e}")

    return _format_position(result)


@router.get("/positions/{norad_id}/track", response_model=TrackResponse)
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
        async with _propagator_lock:
            results = propagator.get_positions_at_times(name, utc_dts)
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=f"Propagation failed for NORAD ID {norad_id}: {e}")

    track = [
        {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "alt_km": float(r["alt"]),
            "teme_x": float(r["pos_teme"][0]),
            "teme_y": float(r["pos_teme"][1]),
            "teme_z": float(r["pos_teme"][2]),
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


# Synchronous screening is O(N^2): the full 10,544-sat Starlink catalog takes
# ~258 s / 4.5 GB (7.1 profile), which would hang the worker on a single
# request. Cap the catalog size we'll screen in-request and return a clear 413
# above it. The demo operating point (<=300 sats) sits far under the default;
# raise ORBITWATCH_MAX_SCREEN_SATS to override for an offline/batch box.
_DEFAULT_MAX_SCREEN_SATS = 1500


def _max_screen_sats() -> int:
    """Screening cap from the environment, default _DEFAULT_MAX_SCREEN_SATS.
    A non-numeric override falls back to the default rather than erroring."""
    raw = os.environ.get("ORBITWATCH_MAX_SCREEN_SATS")
    if not raw:
        return _DEFAULT_MAX_SCREEN_SATS
    try:
        return int(raw)
    except ValueError:
        return _DEFAULT_MAX_SCREEN_SATS


# Serialize ALL propagation that touches the shared Satrec cache. Every request
# shares the propagator's cached Satrec objects, and sgp4() mutates each one's
# time/error state on every call. The screen now runs on a worker thread
# (run_in_threadpool) and medium_filter releases the GIL during its scan, so
# without this lock a concurrent position/track request — the frontend polls
# positions continuously — would propagate the same Satrec struct on the event
# loop while the worker thread mutates it: a genuine data race (corrupt position
# or spurious propagation error). One lock guards the screen AND the position
# endpoints; it is uncontended in normal use and the event loop stays free while
# a request waits on it.
_propagator_lock = asyncio.Lock()


@router.get("/conjunctions", response_model=ConjunctionResponse)
async def get_conjunctions(
    request: Request,
    duration_hours: float = Query(default=24.0, gt=0, le=72.0),
    threshold_km: float | None = Query(default=None, gt=0, le=500.0),
    step_sec: float = Query(default=60.0, ge=1.0, le=600.0),
    time: str | None = None,
):
    """Screen the loaded catalog for close approaches over a time window.

    By default uses the SFS Handbook per-regime RTN screening **ellipsoids**
    (radial-tight, along/cross-track loose), with co-located suppression and
    de-dupe to unique pairs — so `count` is the number of at-risk pairs.
    Geometric screening only — no Pc (see project scope). Pass `threshold_km`
    to fall back to a single Euclidean miss threshold (legacy/debugging; no
    suppression or de-dupe). Events are sorted by miss distance, closest first.
    An empty result is count: 0, not an error.

    Note: use a 'Z' suffix (or %2B) in the time param — a literal '+' in a
    query string decodes to a space and breaks ISO parsing.
    """
    propagator = request.app.state.propagator
    try:
        start_utc = _parse_time(time)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid time format: {time}")

    # Refuse an oversized synchronous screen up front (see _max_screen_sats).
    cap = _max_screen_sats()
    n_sats = propagator.catalog_size()
    if n_sats > cap:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Catalog of {n_sats} satellites exceeds the synchronous "
                f"screening cap of {cap}. Lower ORBITWATCH_MAX_SATS to screen "
                f"a smaller shell, or raise ORBITWATCH_MAX_SCREEN_SATS."),
        )

    screener = ConjunctionScreener(propagator)
    stats: dict = {}
    try:
        # CPU-bound screen off the event loop: medium_filter and the batched
        # fine stage release the GIL, so the worker thread runs the C++ scan
        # while the event loop stays responsive to other requests. The lock
        # keeps the screen from racing with position/track requests (and other
        # screens) on the shared, sgp4()-mutated Satrec cache.
        async with _propagator_lock:
            events = await run_in_threadpool(
                screener.screen, start_utc, duration_hours, threshold_km,
                step_sec, timings=stats)
    except ValueError as e:
        # e.g. a screening window shorter than the time step
        raise HTTPException(status_code=422, detail=str(e))

    freshness = propagator.data_freshness()
    return {
        "count": len(events),
        "screening_start": start_utc.isoformat(),
        "duration_hours": duration_hours,
        "threshold_km": threshold_km,
        "suppressed_count": stats.get("n_suppressed", 0),
        "last_fetched": freshness["last_fetched"],
        "data_max_epoch_age_days": freshness["max_epoch_age_days"],
        "events": events,
    }


@router.post("/refresh", response_model=RefreshResponse)
async def refresh_data(request: Request):
    """Trigger a TLE data refresh from CelesTrak.

    Synchronous for Phase 1 (~30 sats, <2s fetch). Respects CelesTrak's
    2-hour update interval — returns "rate_limited" if cache is still fresh.
    """
    propagator = request.app.state.propagator
    fetcher = propagator.fetcher
    group = propagator.group

    # Snapshot old fetch_time to detect whether fetch() actually hit CelesTrak
    try:
        old_df = fetcher.load_cached(group)
        old_fetch_time = pd.Timestamp(old_df["fetch_time"].iloc[0])
    except (FileNotFoundError, KeyError, IndexError):
        old_fetch_time = None

    try:
        new_df = fetcher.fetch(group)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Refresh failed: {e}")

    new_fetch_time = pd.Timestamp(new_df["fetch_time"].iloc[0])

    if old_fetch_time is not None and new_fetch_time == old_fetch_time:
        status = "rate_limited"
    else:
        status = "fetched"
        propagator.reload_data()

    return {
        "status": status,
        "group": group,
        "satellite_count": len(new_df),
        "fetch_time": str(new_fetch_time.isoformat()),
    }
