from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse

from app.core.errors import dashboard_error
from app.dependencies import FirewallContext, get_firewall_context
from app.modules.firewall.schemas import (
    FirewallDeleteResponse,
    FirewallIpCreateRequest,
    FirewallIpEntry,
    FirewallIpsResponse,
)
from app.modules.firewall.service import FirewallIpAlreadyExistsError, FirewallValidationError

router = APIRouter(prefix="/api/firewall", tags=["dashboard"])


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
) -> FirewallIpEntry | JSONResponse:
    try:
        created = await context.service.add_ip(payload.ip_address)
    except FirewallValidationError as exc:
        return JSONResponse(status_code=400, content=dashboard_error("invalid_ip", str(exc)))
    except FirewallIpAlreadyExistsError:
        return JSONResponse(status_code=409, content=dashboard_error("ip_exists", "IP address already exists"))
    return FirewallIpEntry(ip_address=created.ip_address, created_at=created.created_at)


@router.delete("/ips/{ip_address}", response_model=FirewallDeleteResponse)
async def delete_firewall_ip(
    ip_address: str,
    context: FirewallContext = Depends(get_firewall_context),
) -> FirewallDeleteResponse | JSONResponse:
    try:
        deleted = await context.service.remove_ip(ip_address)
    except FirewallValidationError as exc:
        return JSONResponse(status_code=400, content=dashboard_error("invalid_ip", str(exc)))
    if not deleted:
        return JSONResponse(status_code=404, content=dashboard_error("ip_not_found", "IP address not found"))
    return FirewallDeleteResponse(status="deleted")
