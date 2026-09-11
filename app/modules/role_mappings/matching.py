"""Which rule wins for an identity (pure; no database, no session).

Rules are ordered by descending priority and the FIRST match wins. There is no
"highest role" rule — custom roles have no total order — and no tie is
possible: ``UNIQUE(provider, provider_key, priority)`` makes the order total.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.db.models import DashboardRoleMapping, DashboardRoleMappingClaim

#: Claim names a rule may match on (a plain string column, validated here).
SUPPORTED_CLAIMS: frozenset[str] = frozenset(claim.value for claim in DashboardRoleMappingClaim)


def normalize_claim_value(claim_name: str, value: str) -> str:
    """Trim and case-fold; an ``email_domain`` value is stored without its leading ``@``."""

    normalized = value.strip().casefold()
    if claim_name == DashboardRoleMappingClaim.EMAIL_DOMAIN.value:
        normalized = normalized.removeprefix("@")
    return normalized


def email_domain(email: str | None) -> str | None:
    """The domain part of an e-mail, case-folded; ``None`` when there is none to compare."""

    if email is None:
        return None
    _, _, domain = email.strip().casefold().rpartition("@")
    return domain or None


def _matches(row: DashboardRoleMapping, groups: Sequence[str], domain: str | None) -> bool:
    if row.claim_name == DashboardRoleMappingClaim.GROUPS.value:
        return normalize_claim_value(row.claim_name, row.claim_value) in {group.casefold() for group in groups}
    if row.claim_name == DashboardRoleMappingClaim.EMAIL_DOMAIN.value:
        return domain is not None and normalize_claim_value(row.claim_name, row.claim_value) == domain
    # A claim this replica's vocabulary does not know (a newer peer wrote it):
    # it matches nothing rather than breaking every sign-in.
    return False


def match_role_id(
    rows: Iterable[DashboardRoleMapping], *, groups: Sequence[str] = (), email: str | None = None
) -> str | None:
    """The role of the highest-priority matching rule, or ``None`` when none matches."""

    domain = email_domain(email)
    for row in sorted(rows, key=lambda row: row.priority, reverse=True):
        if _matches(row, groups, domain):
            return row.role_id
    return None
