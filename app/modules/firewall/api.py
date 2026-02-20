from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from app.core.auth.dependencies import set_dashboard_error_format, validate_dashboard_session
from app.core.exceptions import DashboardBadRequestError, DashboardConflictError, DashboardNotFoundError
from app.dependencies import FirewallContext, get_firewall_context
from app.modules.firewall.schemas import (
    FirewallDeleteResponse,
    FirewallIpCreateRequest,
    FirewallIpEntry,
    FirewallIpsResponse,
)
from app.modules.firewall.service import FirewallIpAlreadyExistsError, FirewallValidationError

router = APIRouter(
    prefix="/api/firewall",
    tags=["dashboard"],
    dependencies=[Depends(validate_dashboard_session), Depends(set_dashboard_error_format)],
)


@router.get("/ips", response_model=FirewallIpsResponse)
async def list_firewall_ips(
    context: FirewallContext = Depends(get_firewall_context),
) -> FirewallIpsResponse:
    payload = await context.service.list_ips()
    return FirewallIpsResponse(
        mode=payload.mode,
        entries=[
            FirewallIpEntry(ip_address=entry.ip_address, created_at=entry.created_at) for entry in payload.entries
        ],
    )


@router.post("/ips", response_model=FirewallIpEntry)
async def add_firewall_ip(
    payload: FirewallIpCreateRequest = Body(...),
    context: FirewallContext = Depends(get_firewall_context),
) -> FirewallIpEntry:
    try:
        created = await context.service.add_ip(payload.ip_address)
    except FirewallValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_ip") from exc
    except FirewallIpAlreadyExistsError as exc:
        raise DashboardConflictError(str(exc), code="ip_exists") from exc
    return FirewallIpEntry(ip_address=created.ip_address, created_at=created.created_at)


@router.delete("/ips/{ip_address}", response_model=FirewallDeleteResponse)
async def delete_firewall_ip(
    ip_address: str,
    context: FirewallContext = Depends(get_firewall_context),
) -> FirewallDeleteResponse:
    try:
        deleted = await context.service.remove_ip(ip_address)
    except FirewallValidationError as exc:
        raise DashboardBadRequestError(str(exc), code="invalid_ip") from exc
    if not deleted:
        raise DashboardNotFoundError("IP address not found", code="ip_not_found")
    return FirewallDeleteResponse(status="deleted")
