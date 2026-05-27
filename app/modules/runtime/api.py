from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.modules.runtime.schemas import RuntimeVersionResponse
from app.modules.runtime.service import get_runtime_version_service

router = APIRouter(
    prefix="/api/runtime",
    tags=["runtime"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/version", response_model=RuntimeVersionResponse)
async def get_runtime_version() -> RuntimeVersionResponse:
    return await get_runtime_version_service().get_version_status()
