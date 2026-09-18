"""Two-mode thread cache identity (``shared`` / ``isolated``).

``shared`` is the default and is a strict no-op: every helper here returns
before touching anything, so the outbound request is byte-for-byte what it was
before this module existed. That property is the whole safety argument for the
default and is pinned by golden-snapshot tests.

``isolated`` scopes three legs of a request per upstream account so two
accounts never present the same cacheable prefix:

1. the outbound ``prompt_cache_key``,
2. the Codex process-session / conversation / thread headers,
3. a stable opaque line prepended to the prompt content.

Only leg 3 actually isolates anything. Measurements on this deployment show the
upstream prefix cache is *not* partitioned by ``prompt_cache_key`` and not
partitioned by account: content seeded by one account produced near-full cache
hits on four siblings. Scoping the key and the headers is therefore
cache-neutral bookkeeping — it makes the wire identity consistent with the
content identity — while the content prefix is the only lever that changes what
upstream can match. Nothing here is a countermeasure to provider-side
correlation; it exists for cache isolation and deterministic per-account cache
behaviour.

The scope token is derived from the **load-balancer account id alone**. A
session component is deliberately excluded: the most specific session value the
proxy holds is ``x-codex-turn-state``, which is minted fresh per turn when the
client does not supply one, so hashing it would rotate the prefix every turn and
destroy caching *inside* an account — strictly worse than either mode. An
account-only token is stable by construction on every request (including the
33-38% of traffic that carries no session identifier at all), isolates exactly
the thing that needs isolating, and keeps the shared instructions/tools prefix
warm between two sessions on the same account.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from typing import Final, Protocol

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_mapping


class HeaderReplacer(Protocol):
    """The caller's position-preserving header setter.

    ``app/core/clients/proxy.py`` keeps a native client's header order and
    spelling by replacing a key in place rather than reassigning it, so the
    scoping helper delegates the write instead of mutating the dict directly.
    """

    def __call__(self, headers: dict[str, str], name: str, value: str, *, fallback_name: str) -> None: ...


THREAD_CACHE_IDENTITY_MODE_SHARED: Final = "shared"
THREAD_CACHE_IDENTITY_MODE_ISOLATED: Final = "isolated"
THREAD_CACHE_IDENTITY_MODES: Final[frozenset[str]] = frozenset(
    {THREAD_CACHE_IDENTITY_MODE_SHARED, THREAD_CACHE_IDENTITY_MODE_ISOLATED}
)
THREAD_CACHE_IDENTITY_MODE_DEFAULT: Final = THREAD_CACHE_IDENTITY_MODE_SHARED

# Version salt rather than a deployment-derived one (the encryption-key
# fingerprint was the alternative): rotating the encryption key would otherwise
# flush every account's prefix at once. Bumping the literal is the explicit,
# reviewable way to invalidate every scope token.
_SCOPE_SALT: Final = b"codex-lb-cache-scope-v1"
_SCOPE_TOKEN_HEX_LEN: Final = 16
# Fixed v5 namespace so a UUID-shaped session header stays UUID-shaped.
_SCOPE_UUID_NAMESPACE: Final = uuid.UUID("6f2f0f3a-9f1b-5c0e-8d4a-1c7b2e5a9d31")

# Marker is namespaced metadata with no imperative verb, so there is nothing in
# it for a model to obey, and it is identical on every turn so it never reads as
# a salient change.
_SCOPE_LINE_PREFIX: Final = "codex-lb-cache-scope: "

# Upstream bounds ``prompt_cache_key`` length, so the scoped key cannot simply
# grow: a client key already at the limit plus a 17-character suffix would be
# rejected for a request that was previously valid. Past the bound the original
# key is folded into a digest that still encodes both the client key and the
# account, so uniqueness survives while the length does not.
_SCOPED_KEY_MAX_LEN: Final = 64
_SCOPED_KEY_DIGEST_PREFIX: Final = "clbcs1-"
_SCOPED_KEY_DIGEST_HEX_LEN: Final = 32

# Worst case of the two payload legs: the leading developer input item plus the
# key suffix, rounded up for JSON punctuation. Intentionally a constant upper
# bound rather than a measurement, because the consumer needs it before the
# payload exists.
_SCOPE_PAYLOAD_OVERHEAD_BYTES: Final = 160

# The outbound rewrite set. ``x-codex-turn-state`` and ``previous_response_id``
# are deliberately absent: both are upstream-issued values that must round-trip
# verbatim, and both are already owner-bound, so they carry nothing across
# accounts.
SCOPED_SESSION_HEADER_NAMES: Final[tuple[str, ...]] = (
    "session_id",
    "session-id",
    "x-codex-session-id",
    "x-codex-conversation-id",
    "thread-id",
)


def normalize_thread_cache_identity_mode(value: object) -> str | None:
    """Return the canonical mode name, or ``None`` when ``value`` names no mode."""

    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in THREAD_CACHE_IDENTITY_MODES else None


@dataclass(frozen=True, slots=True)
class ThreadCacheIdentity:
    """Resolved mode plus the load-balancer account the request is going out on."""

    mode: str
    account_id: str | None = None

    @property
    def isolated(self) -> bool:
        return self.mode == THREAD_CACHE_IDENTITY_MODE_ISOLATED and bool(self.account_id)

    @property
    def token(self) -> str:
        """Deterministic 16-hex scope token for this account. No clock, no randomness."""

        account_id = self.account_id or ""
        return sha256(_SCOPE_SALT + b"|" + account_id.encode()).hexdigest()[:_SCOPE_TOKEN_HEX_LEN]

    @property
    def scope_line(self) -> str:
        return f"{_SCOPE_LINE_PREFIX}{self.token}"


def _isolated(identity: ThreadCacheIdentity | None) -> ThreadCacheIdentity | None:
    """``identity`` when it is isolated and usable, otherwise ``None`` (shared no-op)."""

    if identity is None or not identity.isolated:
        return None
    return identity


def scope_prompt_cache_key(payload_dict: dict[str, JsonValue], identity: ThreadCacheIdentity | None) -> None:
    """Suffix an existing outbound ``prompt_cache_key`` with the account scope token.

    Egress only. The key is never written back onto the ``ResponsesRequest``
    model: the ingress affinity resolver uses it to pick an account, so an
    account-dependent key there would make routing circular, carry the previous
    account's key into a failover retry, and destroy conversation grouping in
    ``request_logs``.

    A request that carries no key is left without one — ``isolated`` mode does
    not invent wire fields.
    """

    isolated = _isolated(identity)
    if isolated is None:
        return
    for name in ("prompt_cache_key", "promptCacheKey"):
        value = payload_dict.get(name)
        if isinstance(value, str) and value:
            payload_dict[name] = _scoped_prompt_cache_key(value, isolated)


def _scoped_prompt_cache_key(value: str, identity: ThreadCacheIdentity) -> str:
    """Make ``value`` account-specific without growing past the upstream bound.

    Deliberately unconditional. An earlier version skipped values that looked
    already-scoped, but a client key may legitimately end with this account's
    token or begin with the digest prefix, and skipping such a key would send
    the *same* key on two accounts — exactly the isolation this is for. There is
    no double-application to defend against: the egress payload is rebuilt from
    the request model on every attempt, and this runs once per build.
    """

    scoped = f"{value}-{identity.token}"
    if len(scoped) <= _SCOPED_KEY_MAX_LEN:
        return scoped
    digest = sha256(f"{value}|{identity.token}".encode()).hexdigest()[:_SCOPED_KEY_DIGEST_HEX_LEN]
    return f"{_SCOPED_KEY_DIGEST_PREFIX}{digest}"


def _scoped_session_header_value(value: str, identity: ThreadCacheIdentity) -> str:
    try:
        uuid.UUID(value)
    except ValueError:
        return f"{value}-{identity.token}"
    # Preserve the value's shape so a UUID-shaped header stays UUID-shaped: the
    # upstream blast radius of rewriting these is unmeasured, and a shape change
    # is the one part of it we can rule out for free.
    return str(uuid.uuid5(_SCOPE_UUID_NAMESPACE, f"{identity.account_id}:{value}"))


def scope_session_headers(
    headers: dict[str, str],
    identity: ThreadCacheIdentity | None,
    *,
    replace: HeaderReplacer | None = None,
) -> None:
    """Rewrite the Codex process-session/thread headers in place, per account.

    ``replace`` is the caller's position-preserving header setter
    (``_replace_header_preserving_position``) so a native client's header order
    and spelling survive; when it is omitted the matching keys are assigned
    directly.
    """

    isolated = _isolated(identity)
    if isolated is None:
        return
    for name in SCOPED_SESSION_HEADER_NAMES:
        for key in [key for key in headers if key.lower() == name]:
            value = headers[key]
            if not isinstance(value, str) or not value.strip():
                continue
            scoped = _scoped_session_header_value(value.strip(), isolated)
            if replace is not None:
                replace(headers, key, scoped, fallback_name=key)
            else:
                headers[key] = scoped


def _scope_prefix_input_item(scope_line: str) -> dict[str, JsonValue]:
    return {
        "type": "message",
        "role": "developer",
        "content": [{"type": "input_text", "text": scope_line}],
    }


def _input_already_scoped(input_value: list[JsonValue], scope_line: str) -> bool:
    if not input_value:
        return False
    first = input_value[0]
    if not is_json_mapping(first):
        return False
    content = first.get("content")
    if not isinstance(content, list):
        return False
    return any(is_json_mapping(part) and part.get("text") == scope_line for part in content)


def inject_cache_scope_prefix(payload_dict: dict[str, JsonValue], identity: ThreadCacheIdentity | None) -> None:
    """Prepend the stable per-account scope line to the prompt content.

    Shape-aware by necessity. Current Codex traffic is "responses-lite": the
    base instructions and the tool bundle travel as the *first input items* and
    top-level ``instructions`` is deliberately the empty string. Writing into
    ``instructions`` on that traffic would invent a non-empty optional field
    where the client sent ``""`` — a wire-shape change on exactly the traffic
    that matters, and upstream is known to reject synthesized optional fields
    (issue #1184) — and would very likely miss the cached prefix anyway.

    So: prepend to ``instructions`` when it is a non-empty string, otherwise
    prepend a leading ``input`` item. The list is rebuilt rather than mutated in
    place because the caller shares it with the HTTP and websocket payload
    copies.
    """

    isolated = _isolated(identity)
    if isolated is None:
        return
    scope_line = isolated.scope_line
    instructions = payload_dict.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        if not instructions.startswith(scope_line):
            payload_dict["instructions"] = f"{scope_line}\n\n{instructions}"
        return
    input_value = payload_dict.get("input")
    if isinstance(input_value, list):
        if not _input_already_scoped(input_value, scope_line):
            payload_dict["input"] = [_scope_prefix_input_item(scope_line), *input_value]
        return
    if isinstance(input_value, str) and input_value:
        if not input_value.startswith(scope_line):
            payload_dict["input"] = f"{scope_line}\n\n{input_value}"


def cache_scope_payload_overhead_bytes(mode: object) -> int:
    """Upper bound on the bytes ``isolated`` mode adds to a serialized payload.

    Transport selection estimates the payload size from the request model
    before egress and then passes its choice down as an explicit override, so a
    request sitting just under the websocket budget must be measured including
    the injection. Zero for ``shared``, so the estimate is unchanged there.
    """

    if normalize_thread_cache_identity_mode(mode) != THREAD_CACHE_IDENTITY_MODE_ISOLATED:
        return 0
    return _SCOPE_PAYLOAD_OVERHEAD_BYTES


def apply_thread_cache_identity(
    payload_dict: dict[str, JsonValue],
    identity: ThreadCacheIdentity | None,
) -> None:
    """Apply both payload legs (key + content prefix). Headers are scoped separately."""

    if _isolated(identity) is None:
        return
    scope_prompt_cache_key(payload_dict, identity)
    inject_cache_scope_prefix(payload_dict, identity)


def thread_cache_identity_from(mode: object, account_id: str | None) -> ThreadCacheIdentity:
    """Build an identity from an unvalidated mode name, defaulting to ``shared``."""

    return ThreadCacheIdentity(
        mode=normalize_thread_cache_identity_mode(mode) or THREAD_CACHE_IDENTITY_MODE_DEFAULT,
        account_id=account_id,
    )


def effective_thread_cache_identity_mode(api_key: object, dashboard_settings: object) -> tuple[str, bool]:
    """Resolve the effective mode. Returns ``(mode, key_override_applied)``.

    Precedence, cloned from ``_effective_http_downstream_transport_policy``: a
    non-NULL per-key override wins, then the fleet value carried on the settings
    snapshot (``SettingsService`` has already folded the non-NULL dashboard
    column over the environment value over the code default), then ``shared``.
    Both reads are ``getattr`` so a stub snapshot in a test degrades to the
    default instead of raising.
    """

    override = normalize_thread_cache_identity_mode(getattr(api_key, "thread_cache_identity_override", None))
    if override is not None:
        return override, True
    fleet = normalize_thread_cache_identity_mode(getattr(dashboard_settings, "thread_cache_identity_mode", None))
    if fleet is not None:
        return fleet, False
    return THREAD_CACHE_IDENTITY_MODE_DEFAULT, False


def thread_cache_identity_log_fields(identity: ThreadCacheIdentity | None) -> Mapping[str, str]:
    """Small, log-safe projection for request-shape observability."""

    if identity is None:
        return {"thread_cache_identity_mode": THREAD_CACHE_IDENTITY_MODE_DEFAULT}
    fields = {"thread_cache_identity_mode": identity.mode}
    if identity.isolated:
        fields["thread_cache_scope"] = identity.token
    return fields
