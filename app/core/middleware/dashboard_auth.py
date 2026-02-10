from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from app.core.errors import dashboard_error
from app.db.session import SessionLocal
from app.modules.dashboard_auth.service import DASHBOARD_SESSION_COOKIE, get_dashboard_session_store
from app.modules.settings.repository import SettingsRepository


def add_dashboard_totp_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def dashboard_totp_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        if path.startswith("/api/dashboard-auth/"):
            return await call_next(request)

        totp_required = False
        async with SessionLocal() as session:
            settings = await SettingsRepository(session).get_or_create()
            totp_required = settings.totp_required_on_login

        if not totp_required:
            return await call_next(request)

        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)
        if get_dashboard_session_store().is_totp_verified(session_id):
            return await call_next(request)

        return JSONResponse(
            status_code=401,
            content=dashboard_error("totp_required", "TOTP verification is required for dashboard access"),
        )
