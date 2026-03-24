"""
OrbitWatch FastAPI Backend — serves real-time satellite positions.

Entry point: uvicorn backend.main:app --reload
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.core.propagator import SatellitePropagator
from backend.models.schemas import HealthResponse
from backend.routers.satellites import router as satellites_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Propagator is lazy — no data loaded until first request calls _ensure_data()
    app.state.propagator = SatellitePropagator()
    yield


app = FastAPI(
    title="OrbitWatch API",
    description="Real-time satellite orbit tracking and collision prediction",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in Week 8 Docker deployment
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(satellites_router)


@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
