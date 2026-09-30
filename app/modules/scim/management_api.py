"""Issuing, rotating and revoking SCIM credentials (``security:write``).

Issue and rotate carry one gate the rest of the module does not:
``assert_can_delegate(principal.grants, ADMIN_GRANTS)``. The reasoning is
§4.6-A1's, applied to a fourth lever. A SCIM token lets a machine assert who
somebody is *and* attach a role, with no human in the loop per push, which is
the same class of lever as writing an OIDC connection or turning on e-mail
linking — and those are gated on admin-level delegation precisely because
``security:write`` is permission to administer sign-in, not permission to
become somebody else. Revoking is never gated: the narrowing direction stays
open everywhere in this plan.

The plaintext appears in the response to issue and to rotate and nowhere else —
not in a list, not in a later read, not in a log line, not in an audit row.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.core.audit.service import AuditActor, AuditService, AuditTarget
from app.core.auth.dashboard_access import (
    ADMIN_GRANTS,
    DashboardPrincipal,
    InsufficientDelegationError,
    Permission,
    assert_can_delegate,
)
from app.core.auth.dependencies import require_dashboard_permission, set_dashboard_error_format
from app.core.exceptions import DashboardNotFoundError, DashboardPermissionError
from app.db.models import DashboardScimToken
from app.dependencies import ScimTokensContext, get_scim_tokens_context
from app.modules.scim.api import SCIM_BASE_PATH
from app.modules.scim.repository import SCIM_PROVIDER
from app.modules.scim.schemas import (
    ScimTokenCreateRequest,
    ScimTokenIssuedResponse,
    ScimTokenListResponse,
    ScimTokenResponse,
)
from app.modules.scim.tokens import generate_token, hash_token, token_prefix

router = APIRouter(
    prefix="/api/scim-tokens",
    tags=["dashboard"],
    dependencies=[
        Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
        Depends(set_dashboard_error_format),
    ],
)

#: Every token this release issues owns the install's single identity
#: namespace. The column is enforced on every lookup and insert anyway, because
#: deriving the namespace from the request instead is the bug the requirement
#: exists to prevent, and a scoping field that exists and is unenforced is
#: worse than none.
DEFAULT_PROVIDER_KEY = "default"


def _response(row: DashboardScimToken) -> ScimTokenResponse:
    return ScimTokenResponse(
        id=row.id,
        label=row.label,
        token_prefix=row.token_prefix,
        created_at=row.created_at,
        created_by_user_id=row.created_by_user_id,
        last_used_at=row.last_used_at,
        rotated_at=row.rotated_at,
    )


def _require_admin_delegation(principal: DashboardPrincipal) -> None:
    try:
        assert_can_delegate(principal.grants, ADMIN_GRANTS)
    except InsufficientDelegationError as exc:
        raise DashboardPermissionError(
            "Issuing an automatic-account-management credential needs the permissions it could hand out",
            code="insufficient_delegation",
        ) from exc


def _audit(action: str, principal: DashboardPrincipal, row: DashboardScimToken, request: Request) -> None:
    """The row id and the label travel; the secret and its digest never do.

    The audit sanitiser drops keys by exact name from a fixed set and knows
    none of these, so it is not a safety net here — the rule is simply that no
    detail dictionary ever carries the credential under any name.
    """

    AuditService.log_async(
        action,
        actor_ip=request.client.host if request.client else None,
        details={"label": row.label, "token_prefix": row.token_prefix, "provider": SCIM_PROVIDER},
        actor=AuditActor.from_principal(principal),
        target=AuditTarget("scim_token", row.id),
    )


@router.get("", response_model=ScimTokenListResponse)
async def list_scim_tokens(
    context: ScimTokensContext = Depends(get_scim_tokens_context),
) -> ScimTokenListResponse:
    rows = await context.repository.list_tokens()
    return ScimTokenListResponse(tokens=[_response(row) for row in rows], base_path=SCIM_BASE_PATH)


@router.post("", response_model=ScimTokenIssuedResponse, status_code=201)
async def issue_scim_token(
    request: Request,
    payload: ScimTokenCreateRequest,
    context: ScimTokensContext = Depends(get_scim_tokens_context),
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> ScimTokenIssuedResponse:
    _require_admin_delegation(principal)
    secret = generate_token()
    row = await context.repository.create(
        label=payload.label,
        token_hash=hash_token(secret),
        token_prefix=token_prefix(secret),
        provider_key=DEFAULT_PROVIDER_KEY,
        created_by_user_id=principal.user_id,
    )
    _audit("scim_token_issued", principal, row, request)
    return ScimTokenIssuedResponse(token=_response(row), secret=secret)


@router.post("/{token_id}/rotate", response_model=ScimTokenIssuedResponse)
async def rotate_scim_token(
    token_id: str,
    request: Request,
    context: ScimTokensContext = Depends(get_scim_tokens_context),
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> ScimTokenIssuedResponse:
    _require_admin_delegation(principal)
    secret = generate_token()
    # In place on the same row: the id, the label and the sync history survive,
    # and the previous secret stops working the moment this commits, because
    # the row is the only verifier there is.
    row = await context.repository.rotate(token_id, token_hash=hash_token(secret), token_prefix=token_prefix(secret))
    if row is None:
        raise DashboardNotFoundError("No such token", code="scim_token_not_found")
    _audit("scim_token_rotated", principal, row, request)
    return ScimTokenIssuedResponse(token=_response(row), secret=secret)


@router.delete("/{token_id}", status_code=204)
async def revoke_scim_token(
    token_id: str,
    request: Request,
    context: ScimTokensContext = Depends(get_scim_tokens_context),
    principal: DashboardPrincipal = Depends(require_dashboard_permission(Permission.SECURITY_WRITE)),
) -> None:
    row = await context.repository.get(token_id)
    if row is None or not await context.repository.delete(token_id):
        raise DashboardNotFoundError("No such token", code="scim_token_not_found")
    _audit("scim_token_revoked", principal, row, request)
