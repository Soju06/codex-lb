from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.dependencies import (
    require_dashboard_write_access,
    set_dashboard_error_format,
    validate_dashboard_session,
)
from app.db.session import get_session
from app.modules.telemetry.consent import ResolvedConsent, TelemetryConsentStore
from app.modules.telemetry.schemas import (
    TelemetryConsentResponse,
    TelemetryConsentUpdate,
    TelemetrySnapshotEnvelope,
    build_snapshot_envelope,
)
from app.modules.telemetry.snapshot import TelemetrySnapshotBuilder

router = APIRouter(
    prefix="/api/settings",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/telemetry", response_model=TelemetryConsentResponse)
async def get_telemetry_consent(
    include_preview: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
) -> TelemetryConsentResponse:
    store = TelemetryConsentStore(session)
    consent = await store.resolve()
    return await _response(
        session,
        store,
        consent,
        include_preview=include_preview or (consent.state == "undecided" and consent.source == "default"),
    )


@router.put("/telemetry", response_model=TelemetryConsentResponse)
async def update_telemetry_consent(
    payload: TelemetryConsentUpdate = Body(...),
    _write_access=Depends(require_dashboard_write_access),
    session: AsyncSession = Depends(get_session),
) -> TelemetryConsentResponse:
    store = TelemetryConsentStore(session)
    consent = await store.set_decision(payload.enabled)
    return await _response(session, store, consent, include_preview=False)


async def _response(
    session: AsyncSession,
    store: TelemetryConsentStore,
    consent: ResolvedConsent,
    *,
    include_preview: bool,
) -> TelemetryConsentResponse:
    preview: TelemetrySnapshotEnvelope | None = None
    if include_preview:
        identity = await store.get_or_create_identity()
        snapshot = await TelemetrySnapshotBuilder(session).build(identity.instance_id)
        preview = build_snapshot_envelope(snapshot)
    return TelemetryConsentResponse(
        state=consent.state,
        source=consent.source,
        active=consent.active,
        preview=preview,
    )
