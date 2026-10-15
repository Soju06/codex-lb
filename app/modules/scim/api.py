"""``/scim/v2/Users`` — the surface an identity provider pushes joiners and leavers to.

Its own router, its own envelope, its own credential. Three properties are
deliberate and each is pinned by a test rather than by review:

* No route carries ``validate_dashboard_session`` or a dashboard permission
  dependency, and none sets a cookie. A SCIM token is not a session and grants
  no dashboard permission; the absence of a principal on this path is what makes
  that structurally true instead of a promise.
* Bodies are read by hand rather than through a ``Body`` parameter, so the
  declared ``Content-Length`` is refused before a byte is consumed and a
  malformed payload answers RFC 7644's envelope rather than FastAPI's.
* ``DELETE`` is answered, not omitted: identity providers soft-disable, and a
  deprovisioned account has to stay rediscoverable by ``userName eq``, so the
  refusal names the operation that does work.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response, Security
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError

from app.core.auth.dependencies import set_scim_error_format
from app.core.errors import SCIM_CONTENT_TYPE
from app.dependencies import ScimContext, get_scim_context
from app.modules.scim import errors
from app.modules.scim.dependencies import ScimTokenData, validate_scim_token
from app.modules.scim.schemas import MAX_BODY_BYTES, ScimPatchRequest, ScimUserRequest

#: The path an identity provider configures as its SCIM base URL.
SCIM_BASE_PATH = "/scim/v2"
_ALLOWED_ON_RESOURCE = "GET, PUT, PATCH"
#: RFC 7644 §3.4.2.4 lets a server return fewer resources than asked for.
_DEFAULT_COUNT = 100

router = APIRouter(prefix=SCIM_BASE_PATH, tags=["scim"], dependencies=[Depends(set_scim_error_format)])


def _client_host(request: Request) -> str | None:
    return request.client.host if request.client else None


async def _read[ModelT: BaseModel](request: Request, model: type[ModelT]) -> ModelT:
    """The body, bounded at the route and validated into RFC 7644's envelope.

    The declared length is checked first so an oversized push is refused
    without being read, then the received length, because ``Content-Length``
    is the caller's claim and not a fact. The global ingress budget of 32 MiB
    is not a bound for a user resource in any useful sense.
    """

    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise errors.too_large(MAX_BODY_BYTES)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise errors.too_large(MAX_BODY_BYTES)
    try:
        payload = json.loads(raw or b"{}")
    except ValueError as exc:
        raise errors.invalid_syntax("The request body is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise errors.invalid_syntax("The request body must be a JSON object.")
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise errors.invalid_syntax("The request body is not a valid SCIM resource.") from exc


def _scim_json(
    content: dict[str, object], *, status_code: int = 200, headers: dict[str, str] | None = None
) -> Response:
    return JSONResponse(status_code=status_code, content=content, media_type=SCIM_CONTENT_TYPE, headers=headers)


def _positive_int(raw: str | None, *, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise errors.invalid_value(f"'{raw}' is not a number.") from exc
    return value


@router.post("/Users")
async def create_user(
    request: Request,
    context: ScimContext = Depends(get_scim_context),
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    payload = await _read(request, ScimUserRequest)
    identity = await context.service.create_user(
        payload,
        provider_key=token.provider_key,
        token_id=token.id,
        actor_ip=_client_host(request),
    )
    resource = context.service.resource(identity, base_path=SCIM_BASE_PATH)
    location = str(resource["meta"]["location"])  # type: ignore[index]
    return _scim_json(resource, status_code=201, headers={"Location": location})


@router.get("/Users")
async def list_users(
    request: Request,
    context: ScimContext = Depends(get_scim_context),
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    user_name = context.service.parse_filter(request.query_params.get("filter"))
    start_index = _positive_int(request.query_params.get("startIndex"), default=1)
    count = _positive_int(request.query_params.get("count"), default=_DEFAULT_COUNT)
    identities, total = await context.service.list_users(
        token.provider_key, user_name=user_name, start_index=start_index, count=count
    )
    return _scim_json(
        context.service.list_response(
            identities, total=total, start_index=max(start_index, 1), base_path=SCIM_BASE_PATH
        )
    )


@router.get("/Users/{user_id}")
async def read_user(
    user_id: str,
    context: ScimContext = Depends(get_scim_context),
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    identity = await context.service.require_identity(token.provider_key, user_id)
    return _scim_json(context.service.resource(identity, base_path=SCIM_BASE_PATH))


@router.put("/Users/{user_id}")
async def replace_user(
    user_id: str,
    request: Request,
    context: ScimContext = Depends(get_scim_context),
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    identity = await context.service.require_identity(token.provider_key, user_id)
    payload = await _read(request, ScimUserRequest)
    changes = context.service.changes_from_replace(identity, payload)
    updated = await context.service.apply(
        identity,
        changes,
        provider_key=token.provider_key,
        token_id=token.id,
        actor_ip=_client_host(request),
    )
    return _scim_json(context.service.resource(updated, base_path=SCIM_BASE_PATH))


@router.patch("/Users/{user_id}")
async def patch_user(
    user_id: str,
    request: Request,
    context: ScimContext = Depends(get_scim_context),
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    identity = await context.service.require_identity(token.provider_key, user_id)
    payload = await _read(request, ScimPatchRequest)
    changes = context.service.changes_from_patch(identity, payload)
    updated = await context.service.apply(
        identity,
        changes,
        provider_key=token.provider_key,
        token_id=token.id,
        actor_ip=_client_host(request),
    )
    return _scim_json(context.service.resource(updated, base_path=SCIM_BASE_PATH))


@router.delete("/Users/{user_id}")
async def delete_user(
    user_id: str,
    token: ScimTokenData = Security(validate_scim_token),
) -> Response:
    """Deprovisioning is a soft disable, and the route says so rather than 404ing.

    A deleted row could not answer ``userName eq`` afterwards, and an identity
    provider that cannot rediscover the person it deprovisioned re-creates them
    on the next full sync.
    """

    del user_id
    raise errors.method_not_allowed(
        'Users are deprovisioned with PATCH {"active": false}; this surface does not delete accounts.',
        allow=_ALLOWED_ON_RESOURCE,
    )
