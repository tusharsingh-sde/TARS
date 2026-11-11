from fastapi import FastAPI
from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Response model for the liveness health check endpoint."""

    status: str
    version: str


app = FastAPI(title="TARS", version="0.1.0")


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Return service liveness status and version."""
    return HealthResponse(status="ok", version="0.1.0")

