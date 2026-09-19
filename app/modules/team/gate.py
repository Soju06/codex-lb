from __future__ import annotations

from app.db.session import get_background_session
from app.modules.api_keys.service import ApiKeyData
from app.modules.team.repository import TeamRepository
from app.modules.team.service import TeamService


async def check_member_gate(api_key: ApiKeyData | None, *, model: str | None) -> None:
    """Apply the team-member gate for a proxied request.

    No-ops when the request carries no API key, or when the key is not attached
    to a team member, so keyless and pre-team-mode deployments never take the
    extra database round trip.
    """

    if api_key is None or getattr(api_key, "member_id", None) is None:
        return

    async with get_background_session() as session:
        await TeamService(TeamRepository(session)).check_member_gate(api_key, model)
