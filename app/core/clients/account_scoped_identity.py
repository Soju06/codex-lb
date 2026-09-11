"""Deterministic per-account scoping for outbound thread identifiers.

One logical downstream thread can be served by more than one pooled ChatGPT
account. Today the identifiers that name that thread -- ``prompt_cache_key``
and the session/conversation headers -- travel upstream verbatim, so account A
and account B present the *same* session value for the same thread. Those are
account-scoped resources upstream; sending one account's identifier on another
account's request is simply wrong, and it is the same defect
``apply_codex_installation_metadata`` already fixes for the Codex installation
id.

This module is the pure mapping used to fix it. It is deliberately:

* **Deterministic** -- no randomness, no clock, no process state. The same
  ``(installation id, original value)`` pair always maps to the same output,
  in every worker and across restarts, so a thread keeps one cache anchor.
* **Collision-free across accounts** -- the salt is length-framed with the
  original value before hashing, so two different pairs cannot produce the
  same material.
* **Shape preserving** -- a UUID-shaped input maps to a UUID-shaped output, so
  an upstream field that validates the shape keeps validating.
* **Not idempotent** -- scoping a scoped value yields a third value. Apply it
  exactly once, at the final dispatch boundary, never inside a helper that a
  caller may invoke twice on the same request.

The salt is ``accounts.codex_installation_id`` (per-seat, non-null, server
owned), never ``chatgpt_account_id`` -- the latter is the *workspace* identity
shared by every seat of a Team/Business org, so salting with it would leave
same-workspace seats presenting identical values and defeat the whole point.

Two values are never scoped, and this module refuses to name them:

* ``x-codex-turn-state`` -- an opaque upstream-issued continuity token that
  codex-lb never decodes and must round-trip verbatim. It is already treated
  as account-local state and *deleted* when a thread moves accounts.
* ``previous_response_id`` -- an upstream-issued stored-object handle that is
  hard owner-bound; rewriting it breaks continuity outright.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, MutableMapping
from hashlib import sha256
from typing import Any, Final

# Frozen namespace. Changing it re-keys every scoped thread on the next turn,
# so it is a constant, never a setting.
ACCOUNT_SCOPED_IDENTITY_NAMESPACE: Final = uuid.UUID("6f3a4f52-0f59-5b3d-9d8f-0f2f5c9d4a71")

# Marks a scoped value whose input was not UUID-shaped, so an operator reading
# an upstream trace can tell a rewritten identifier from a client-authored one.
SCOPED_OPAQUE_PREFIX: Final = "cxlb-"
_SCOPED_OPAQUE_DIGEST_LEN: Final = 32

# Thread identifiers that reach upstream inside the request *body*, as
# ``client_metadata`` entries copied from the same-named inbound headers
# (``_RESPONSE_CREATE_COMPATIBILITY_METADATA_HEADERS``). They are scoped with
# the same mapping as the headers, otherwise the two surfaces disagree and one
# of them still crosses accounts.
SCOPED_BODY_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "x-codex-parent-thread-id",
        "x-codex-window-id",
    }
)

# Session/thread headers that name one logical thread and are forwarded to
# upstream verbatim today. ``_CODEX_PROCESS_SESSION_HEADERS`` and the opencode
# set in ``_CONVERSATION_HEADERS_BY_USERAGENT_PREFIX`` are the live vocabulary;
# this is their union with the body-mirrored names, lower-cased.
SCOPED_SESSION_HEADER_NAMES: Final[frozenset[str]] = (
    frozenset(
        {
            "session_id",
            "session-id",
            "x-codex-session-id",
            "x-codex-conversation-id",
            "thread-id",
            "x-parent-session-id",
            "x-opencode-session",
            "x-session-id",
            "x-session-affinity",
        }
    )
    | SCOPED_BODY_METADATA_KEYS
)

# Identifiers that MUST reach upstream byte-for-byte as the client (or a prior
# upstream turn) produced them. Asserted by the module's own invariant test.
NEVER_SCOPED_NAMES: Final[frozenset[str]] = frozenset(
    {
        "x-codex-turn-state",
        "previous_response_id",
    }
)

_UUID_SHAPE = re.compile(r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z")


def _material(installation_id: str, value: str) -> str:
    """Length-framed ``(salt, value)`` material.

    Framing each part with its length makes the encoding injective: no two
    distinct pairs can produce the same string, so two accounts cannot be
    given the same scoped value by a crafted identifier.
    """
    return f"{len(installation_id)}:{installation_id}\x00{len(value)}:{value}"


def scope_thread_value(value: str | None, installation_id: str | None) -> str | None:
    """Map ``value`` into ``installation_id``'s namespace.

    Returns ``value`` unchanged when there is no salt or nothing to scope, so a
    disabled flag (which passes ``installation_id=None``) is a byte-for-byte
    no-op on every caller.
    """
    if not installation_id or not isinstance(value, str):
        return value
    if not value.strip():
        return value
    material = _material(installation_id, value)
    if _UUID_SHAPE.match(value):
        return str(uuid.uuid5(ACCOUNT_SCOPED_IDENTITY_NAMESPACE, material))
    digest = sha256(f"{ACCOUNT_SCOPED_IDENTITY_NAMESPACE.hex}\x00{material}".encode()).hexdigest()
    return f"{SCOPED_OPAQUE_PREFIX}{digest[:_SCOPED_OPAQUE_DIGEST_LEN]}"


def scope_prompt_cache_key(value: str | None, installation_id: str | None) -> str | None:
    """Scope the outbound ``prompt_cache_key``.

    The upstream prompt cache is per account, so an unscoped key makes two
    accounts share one cache anchor for the same thread. This is applied to the
    *outbound* body only; the request model's own ``prompt_cache_key`` stays
    account-neutral because it is the sticky-selection input.
    """
    return scope_thread_value(value, installation_id)


def scope_session_headers(
    headers: Mapping[str, str],
    installation_id: str | None,
) -> dict[str, str]:
    """Return ``headers`` with every session/thread identifier account-scoped.

    Header names keep their original casing and position. ``x-codex-turn-state``
    and any other name outside :data:`SCOPED_SESSION_HEADER_NAMES` are copied
    through untouched.
    """
    updated = dict(headers)
    if not installation_id:
        return updated
    for key, value in list(updated.items()):
        if key.lower() not in SCOPED_SESSION_HEADER_NAMES:
            continue
        scoped = scope_thread_value(value, installation_id)
        if isinstance(scoped, str):
            updated[key] = scoped
    return updated


def scope_payload_thread_identity(
    payload: MutableMapping[str, Any],
    installation_id: str | None,
) -> bool:
    """Scope the thread identifiers carried in a Responses request body, in place.

    Rewrites ``prompt_cache_key`` and the ``client_metadata`` entries in
    :data:`SCOPED_BODY_METADATA_KEYS`. ``previous_response_id`` and
    ``x-codex-turn-metadata`` are left exactly as they are.

    Returns ``True`` when a value actually changed, so a caller that has to
    re-serialise a frame can skip the round trip when nothing did.
    """
    if not installation_id:
        return False
    changed = False
    raw_prompt_cache_key = payload.get("prompt_cache_key")
    if isinstance(raw_prompt_cache_key, str):
        scoped = scope_prompt_cache_key(raw_prompt_cache_key, installation_id)
        if isinstance(scoped, str) and scoped != raw_prompt_cache_key:
            payload["prompt_cache_key"] = scoped
            changed = True
    raw_metadata = payload.get("client_metadata")
    if isinstance(raw_metadata, Mapping):
        # Copy on write: ``to_payload()`` is a shallow dump, so the metadata
        # mapping can be the request model's own object. Mutating it would
        # leak this account's scoped value into a later attempt on a different
        # account.
        updated_metadata = dict(raw_metadata)
        metadata_changed = False
        for key, value in raw_metadata.items():
            if not isinstance(key, str) or key.lower() not in SCOPED_BODY_METADATA_KEYS:
                continue
            if not isinstance(value, str):
                continue
            scoped_value = scope_thread_value(value, installation_id)
            if isinstance(scoped_value, str) and scoped_value != value:
                updated_metadata[key] = scoped_value
                metadata_changed = True
        if metadata_changed:
            payload["client_metadata"] = updated_metadata
            changed = True
    return changed
