from __future__ import annotations

import json
from app.core.types import JsonObject


def format_sse_event(payload: JsonObject) -> str:
    data = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
    event_type = payload.get("type")
    if isinstance(event_type, str) and event_type:
        return f"event: {event_type}\ndata: {data}\n\n"
    return f"data: {data}\n\n"
