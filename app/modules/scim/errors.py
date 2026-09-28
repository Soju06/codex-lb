"""The refusals this surface can produce, each already RFC 7644-shaped.

``scimType`` is present exactly where RFC 7644 §3.12 defines a keyword for the
case — the ``400`` family and the duplicate-resource ``409`` — and absent from
the ``409``\\ s that carry this product's account invariants, because the
specification defines no keyword that means "the last emergency account" and
inventing one would be off-spec for a machine reader that does not exist. The
administrator-facing reason for those lives in the audit row.
"""

from __future__ import annotations

from app.core.exceptions import ScimError

#: One sentence for a missing, malformed, unknown, revoked or rotated-away
#: bearer. The five cases are never distinguished: which one it was is exactly
#: the fact an attacker probing the surface would like to learn.
UNAUTHENTICATED_DETAIL = "Authentication failed."
_WWW_AUTHENTICATE = {"WWW-Authenticate": "Bearer"}


def unauthenticated() -> ScimError:
    return ScimError(UNAUTHENTICATED_DETAIL, status_code=401, headers=_WWW_AUTHENTICATE)


def invalid_value(detail: str) -> ScimError:
    return ScimError(detail, status_code=400, scim_type="invalidValue")


def invalid_syntax(detail: str) -> ScimError:
    return ScimError(detail, status_code=400, scim_type="invalidSyntax")


def invalid_path(detail: str) -> ScimError:
    return ScimError(detail, status_code=400, scim_type="invalidPath")


def invalid_filter(detail: str) -> ScimError:
    return ScimError(detail, status_code=400, scim_type="invalidFilter")


def immutable(detail: str) -> ScimError:
    return ScimError(detail, status_code=400, scim_type="mutability")


def already_exists(resource_id: str) -> ScimError:
    return ScimError(
        f"A user with that externalId is already provisioned as {resource_id}.",
        status_code=409,
        scim_type="uniqueness",
    )


def conflict(detail: str) -> ScimError:
    """An account invariant refused the change; no RFC keyword covers these."""

    return ScimError(detail, status_code=409)


def not_found() -> ScimError:
    return ScimError("No such user.", status_code=404)


def too_large(limit: int) -> ScimError:
    return ScimError(f"The request body may not exceed {limit} bytes.", status_code=413)


def rate_limited(retry_after: int) -> ScimError:
    return ScimError(
        "Too many requests.",
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )


def method_not_allowed(detail: str, *, allow: str) -> ScimError:
    return ScimError(detail, status_code=405, headers={"Allow": allow})
