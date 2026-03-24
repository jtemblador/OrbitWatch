"""
Satellite metadata and position endpoints.
"""

from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Request

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
    # ⚠ PERF: iterrows() is fine at 30 sats, replace with vectorized build at Phase 3 (6k)
    for _, row in df.iterrows():
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
