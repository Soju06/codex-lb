"""The SCIM 2.0 wire shapes this surface reads and writes (RFC 7643 / RFC 7644).

Attributes this product does not model are ignored rather than refused. Every
identity provider sends more than a core user — ``meta``, ``title``,
``locale``, phone numbers, an enterprise extension — and refusing those would
turn a routine Okta or Entra push into a ``400`` while protecting nothing: the
bound on a request is the declared body size (checked before the body is read)
plus the length and cardinality caps below, not the attribute vocabulary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.shared.schemas import DashboardModel

USER_SCHEMA = "urn:ietf:params:scim:schemas:core:2.0:User"
LIST_RESPONSE_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:ListResponse"
PATCH_OP_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"

#: 64 KiB. A core user resource is a few hundred bytes and the largest patch
#: this surface accepts is a few kilobytes; the global ingress budget of 32 MiB
#: is not a bound for this endpoint in any useful sense.
MAX_BODY_BYTES = 64 * 1024
#: Cardinality caps. A multi-valued attribute an identity provider fills with
#: thousands of entries is a denial of service, not a user.
MAX_MULTI_VALUED = 8
MAX_PATCH_OPERATIONS = 32

#: ``active`` arrives as a JSON boolean from most identity providers and as the
#: strings ``"True"``/``"False"`` from Entra ID's SCIM client. Both mean the
#: same thing, and guessing wrong deprovisions the wrong people.
_TRUE_STRINGS = frozenset({"true", "1"})
_FALSE_STRINGS = frozenset({"false", "0"})


def coerce_active(value: Any) -> bool | None:
    """A SCIM ``active`` value as a boolean, or ``None`` when it is not one."""

    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in _TRUE_STRINGS:
            return True
        if folded in _FALSE_STRINGS:
            return False
    return None


class ScimModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class ScimMultiValue(ScimModel):
    value: str | None = Field(default=None, max_length=320)
    type: str | None = Field(default=None, max_length=32)
    primary: bool = False


class ScimName(ScimModel):
    formatted: str | None = Field(default=None, max_length=128)
    given_name: str | None = Field(default=None, alias="givenName", max_length=64)
    family_name: str | None = Field(default=None, alias="familyName", max_length=64)


class ScimUserRequest(ScimModel):
    """A ``POST`` or ``PUT`` body.

    Every attribute is optional at the model level; which of them a given
    operation requires is the service's rule, so the refusal names the
    attribute in RFC 7644's envelope instead of FastAPI's.
    """

    schemas: list[str] = Field(default_factory=list, max_length=MAX_MULTI_VALUED)
    user_name: str | None = Field(default=None, alias="userName", max_length=256)
    external_id: str | None = Field(default=None, alias="externalId", max_length=512)
    display_name: str | None = Field(default=None, alias="displayName", max_length=128)
    active: Any = None
    name: ScimName | None = None
    emails: list[ScimMultiValue] = Field(default_factory=list, max_length=MAX_MULTI_VALUED)
    roles: list[ScimMultiValue] = Field(default_factory=list, max_length=MAX_MULTI_VALUED)


#: A patch ``path`` is looked up and, when this resource does not model it,
#: dropped — so its length bounds nothing that gets stored, and a cap tight
#: enough to refuse a real one only turns a routine push into a ``400``. The
#: schema-qualified spellings identity providers send are long:
#: ``urn:ietf:params:scim:schemas:extension:enterprise:2.0:User:manager.displayName``
#: is 78 characters, and vendors mint longer custom schema URNs. This matches
#: ``externalId``'s cap, which is the widest opaque string on the resource;
#: with ``MAX_PATCH_OPERATIONS`` it stays an order of magnitude inside the body
#: bound, which is the bound that actually protects this endpoint.
MAX_PATCH_PATH_LENGTH = 512


class ScimPatchOperation(ScimModel):
    op: str = Field(max_length=16)
    path: str | None = Field(default=None, max_length=MAX_PATCH_PATH_LENGTH)
    value: Any = None


class ScimPatchRequest(ScimModel):
    schemas: list[str] = Field(default_factory=list, max_length=MAX_MULTI_VALUED)
    operations: list[ScimPatchOperation] = Field(
        default_factory=list, alias="Operations", max_length=MAX_PATCH_OPERATIONS
    )


class ScimTokenResponse(DashboardModel):
    """A token row as the dashboard sees it: never the secret, only its head."""

    id: str
    label: str
    token_prefix: str
    created_at: datetime
    created_by_user_id: str | None = None
    last_used_at: datetime | None = None
    rotated_at: datetime | None = None


class ScimTokenIssuedResponse(DashboardModel):
    """The one and only response that carries a plaintext token."""

    token: ScimTokenResponse
    #: Shown once. Never listed, never logged, never audited, never returned again.
    secret: str


class ScimTokenListResponse(DashboardModel):
    tokens: list[ScimTokenResponse]
    #: Where the identity provider points its SCIM connector.
    base_path: str


class ScimTokenCreateRequest(DashboardModel):
    #: ``str_strip_whitespace`` so the value the length bounds are checked
    #: against is the value that gets stored: trimming after validation would
    #: let ``"   "`` past ``min_length`` and store an unnamed credential.
    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    label: str = Field(min_length=1, max_length=64)
