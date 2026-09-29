from __future__ import annotations

import pytest
from starlette.requests import Request
from starlette.types import Message

from app.core.exceptions import ScimError
from app.modules.scim.api import _read
from app.modules.scim.schemas import MAX_BODY_BYTES, ScimUserRequest

pytestmark = pytest.mark.unit


def _request(chunks: list[bytes], consumed: list[int]) -> Request:
    index = 0

    async def receive() -> Message:
        nonlocal index
        chunk = chunks[index]
        consumed.append(index)
        index += 1
        return {"type": "http.request", "body": chunk, "more_body": index < len(chunks)}

    return Request({"type": "http", "headers": []}, receive=receive)


@pytest.mark.asyncio
async def test_scim_body_limit_stops_before_receiving_the_tail() -> None:
    consumed: list[int] = []
    request = _request([b" " * MAX_BODY_BYTES, b" ", b"unread tail"], consumed)

    with pytest.raises(ScimError) as raised:
        await _read(request, ScimUserRequest)

    assert raised.value.status_code == 413
    assert consumed == [0, 1]


@pytest.mark.asyncio
@pytest.mark.parametrize("at_limit", [False, True])
async def test_scim_body_limit_accepts_a_fragmented_valid_resource(at_limit: bool) -> None:
    consumed: list[int] = []
    raw = b'{"userName":"alice","externalId":"subject"}'
    if at_limit:
        raw += b" " * (MAX_BODY_BYTES - len(raw))
    request = _request([raw[:10], raw[10:]], consumed)

    resource = await _read(request, ScimUserRequest)

    assert resource.user_name == "alice"
    assert resource.external_id == "subject"
    assert consumed == [0, 1]
