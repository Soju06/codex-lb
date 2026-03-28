from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str


class HealthCheckResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: str
    checks: dict[str, str] | None = None
