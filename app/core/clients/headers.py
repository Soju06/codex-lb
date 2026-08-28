from __future__ import annotations

from app.core.utils.request_id import get_request_id


def build_chatgpt_auth_headers(
    access_token: str,
    account_id: str | None,
    *,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the headers required to call ChatGPT ``backend-api`` endpoints.

    Includes the OAuth bearer token and the ``chatgpt-account-id`` header. The
    account-id header is omitted when the id is a synthetic ``email_``/``local_``
    prefix, matching upstream behavior. An active request id (if any) is attached.
    """
    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    request_id = get_request_id()
    if request_id:
        headers["x-request-id"] = request_id
    if account_id and not account_id.startswith(("email_", "local_")):
        headers["chatgpt-account-id"] = account_id
    if extra:
        headers.update(extra)
    return headers
