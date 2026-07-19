"""Shared account-owner consistency checks for proxy continuity sources."""

from __future__ import annotations

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error


def resolve_required_account_id(*owners: tuple[str, str | None]) -> str | None:
    """Return one proven owner or fail closed when hard sources disagree."""
    resolved = [(source, account_id) for source, account_id in owners if account_id is not None]
    if not resolved:
        return None
    owner_account_id = resolved[0][1]
    conflicting_sources = [source for source, account_id in resolved if account_id != owner_account_id]
    if conflicting_sources:
        # Hard sources identify account-scoped upstream state. Choosing either
        # side would silently abandon the other, so conflicts are never ordered
        # by caller precedence or softened into ordinary affinity fallback.
        sources = ", ".join(source for source, _account_id in resolved)
        raise ProxyResponseError(
            502,
            openai_error(
                "continuity_owner_conflict",
                f"Account-owned continuity sources conflict ({sources}); retry the logical turn.",
                error_type="server_error",
            ),
        )
    return owner_account_id
