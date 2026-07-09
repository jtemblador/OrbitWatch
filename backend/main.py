"""
OrbitWatch FastAPI Backend — serves real-time satellite positions.

Entry point: uvicorn backend.main:app --reload
"""

import os
import sys
from contextlib import asynccontextmanager

# Make `import backend.*` resolve whether launched as a module
# (`uvicorn backend.main:app`) or as a script (`python backend/main.py`):
# the latter puts backend/ on sys.path instead of the project root, so add
# the root explicitly.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.core.propagator import SatellitePropagator
from backend.models.schemas import HealthResponse
from backend.routers.satellites import router as satellites_router

# Dataset selection via environment (defaults keep the test suite on the
# ISS-bearing "stations" group, cached):
#   ORBITWATCH_GROUP       — catalog to load (e.g. "starlink"); default "stations"
#   ORBITWATCH_LIVE=1      — fetch fresh GP data on load (re-downloads only past
#                            CelesTrak's 2 h cache; cache fallback offline)
#   ORBITWATCH_MAX_SATS=N  — slice the catalog to one dense shell of ≤N sats
# Live dense demo:
#   ORBITWATCH_LIVE=1 ORBITWATCH_GROUP=starlink ORBITWATCH_MAX_SATS=300 \
#   python backend/main.py
# Offline fallback (static snapshot): ORBITWATCH_GROUP=starlink_shell …
_GROUP = os.getenv("ORBITWATCH_GROUP", "stations")
_LIVE = os.getenv("ORBITWATCH_LIVE") == "1"
_MAX_SATS = int(os.environ["ORBITWATCH_MAX_SATS"]) if os.getenv("ORBITWATCH_MAX_SATS") else None


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Propagator is lazy — no data loaded until first request calls _ensure_data()
    app.state.propagator = SatellitePropagator(
        group=_GROUP, live=_LIVE, max_sats=_MAX_SATS)
    yield


app = FastAPI(
    title="OrbitWatch API",
    description="Real-time satellite orbit tracking and collision prediction",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # OK for local dev; the deployed site is a static export (no live API)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(satellites_router)


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return {"status": "ok"}


# Serve frontend static files — mounted AFTER API routes so /api/* resolves first.
# html=True makes "/" serve index.html. Absolute path (off the project root) so
# it works regardless of the current working directory.
app.mount(
    "/",
    StaticFiles(directory=os.path.join(_ROOT, "frontend"), html=True),
    name="frontend",
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
