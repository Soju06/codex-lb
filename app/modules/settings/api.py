from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.core.config.settings_cache import get_settings_cache
from app.core.errors import dashboard_error
from app.dependencies import SettingsContext, get_settings_context
from app.modules.settings.schemas import DashboardSettingsResponse, DashboardSettingsUpdateRequest
from app.modules.settings.service import DashboardSettingsUpdateData

router = APIRouter(prefix="/api/settings", tags=["dashboard"])


@router.get("", response_model=DashboardSettingsResponse)
async def get_settings(
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse:
    settings = await context.service.get_settings()
    return DashboardSettingsResponse(
        sticky_threads_enabled=settings.sticky_threads_enabled,
        prefer_earlier_reset_accounts=settings.prefer_earlier_reset_accounts,
        import_without_overwrite=settings.import_without_overwrite,
        totp_required_on_login=settings.totp_required_on_login,
        totp_configured=settings.totp_configured,
        api_key_auth_enabled=settings.api_key_auth_enabled,
    )


@router.put("", response_model=DashboardSettingsResponse)
async def update_settings(
    payload: DashboardSettingsUpdateRequest = Body(...),
    context: SettingsContext = Depends(get_settings_context),
) -> DashboardSettingsResponse | JSONResponse:
    current = await context.service.get_settings()
    try:
        updated = await context.service.update_settings(
            DashboardSettingsUpdateData(
                sticky_threads_enabled=payload.sticky_threads_enabled,
                prefer_earlier_reset_accounts=payload.prefer_earlier_reset_accounts,
                import_without_overwrite=(
                    payload.import_without_overwrite
                    if payload.import_without_overwrite is not None
                    else current.import_without_overwrite
                ),
                totp_required_on_login=(
                    payload.totp_required_on_login
                    if payload.totp_required_on_login is not None
                    else current.totp_required_on_login
                ),
                api_key_auth_enabled=(
                    payload.api_key_auth_enabled
                    if payload.api_key_auth_enabled is not None
                    else current.api_key_auth_enabled
                ),
            )
        )
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content=dashboard_error("invalid_totp_config", str(exc)),
        )

    await get_settings_cache().invalidate()
    return DashboardSettingsResponse(
        sticky_threads_enabled=updated.sticky_threads_enabled,
        prefer_earlier_reset_accounts=updated.prefer_earlier_reset_accounts,
        import_without_overwrite=updated.import_without_overwrite,
        totp_required_on_login=updated.totp_required_on_login,
        totp_configured=updated.totp_configured,
        api_key_auth_enabled=updated.api_key_auth_enabled,
    )
