# V1 Chat Completions Bridge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/v1/chat/completions` and `/v1/models` with OpenAI-compatible shapes, bridged to the existing `/backend-api/codex/responses` flow, without changing current behavior.

**Architecture:** Introduce a chat-compat request model and conversion layer that maps Chat Completions → Responses, then translate Responses SSE events back into Chat Completions stream/non-stream payloads. Add a static, typed model catalog for `/v1/models` with rich metadata.

**Tech Stack:** FastAPI, Pydantic v2, aiohttp, pytest, uv.

---

### Task 1: Add model catalog + `/v1/models` endpoint

**Files:**
- Create: `app/core/openai/models_catalog.py`
- Modify: `app/modules/proxy/api.py`
- Test: `tests/integration/test_v1_models.py`

**Step 1: Write the failing test**

```python
# tests/integration/test_v1_models.py
import pytest

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_v1_models_list(async_client):
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    data = payload["data"]
    assert isinstance(data, list)
    ids = {item["id"] for item in data}
    assert "gpt-5.2" in ids
    for item in data:
        assert item["object"] == "model"
        assert item["owned_by"] == "codex-lb"
        assert "metadata" in item
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_v1_models.py::test_v1_models_list -v`  
Expected: 404 or failing assertions.

**Step 3: Write minimal implementation**

```python
# app/core/openai/models_catalog.py
from __future__ import annotations
from pydantic import BaseModel, ConfigDict

class ModelLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")
    context: int
    output: int

class ModelModalities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    input: list[str]
    output: list[str]

class ModelVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reasoningEffort: str
    reasoningSummary: str
    textVerbosity: str

class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    limit: ModelLimits
    modalities: ModelModalities
    variants: dict[str, ModelVariant]

MODEL_CATALOG: dict[str, ModelEntry] = {
    "gpt-5.2": ModelEntry(...),
    "gpt-5.2-codex": ModelEntry(...),
    "gpt-5.1-codex-max": ModelEntry(...),
}
```

```python
# app/modules/proxy/api.py
@v1_router.get("/models")
async def v1_models() -> JSONResponse:
    items = []
    created = int(time.time())
    for model_id, entry in MODEL_CATALOG.items():
        items.append(
            {
                "id": model_id,
                "object": "model",
                "created": created,
                "owned_by": "codex-lb",
                "metadata": entry.model_dump(mode="json"),
            }
        )
    return JSONResponse({"object": "list", "data": items})
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/integration/test_v1_models.py::test_v1_models_list -v`  
Expected: PASS

**Step 5: Commit (if requested)**

```bash
git add app/core/openai/models_catalog.py app/modules/proxy/api.py tests/integration/test_v1_models.py
git commit -m "feat(api): add v1 models catalog"
```

---

### Task 2: Create chat request model + request mapping

**Files:**
- Create: `app/core/openai/chat_requests.py`
- Create: `app/core/openai/message_coercion.py` (shared helper)
- Modify: `app/core/openai/v1_requests.py`
- Test: `tests/unit/test_chat_request_mapping.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_chat_request_mapping.py
from app.core.openai.chat_requests import ChatCompletionsRequest

def test_chat_messages_to_responses_mapping():
    payload = {
        "model": "gpt-5.2",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ],
    }
    req = ChatCompletionsRequest.model_validate(payload)
    responses = req.to_responses_request()
    assert responses.instructions == "sys"
    assert responses.input == [{"role": "user", "content": "hi"}]
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_chat_request_mapping.py::test_chat_messages_to_responses_mapping -v`  
Expected: FAIL (missing classes/functions)

**Step 3: Write minimal implementation**

```python
# app/core/openai/message_coercion.py
from __future__ import annotations
from typing import cast
from app.core.types import JsonValue

def coerce_messages(existing: str, messages: list[JsonValue]) -> tuple[str, list[JsonValue]]:
    # same logic as v1_requests._coerce_messages
    ...
```

```python
# app/core/openai/chat_requests.py
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field, model_validator
from app.core.types import JsonValue
from app.core.openai.requests import ResponsesRequest
from app.core.openai.message_coercion import coerce_messages

class ChatCompletionsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = Field(min_length=1)
    messages: list[JsonValue]
    tools: list[JsonValue] = Field(default_factory=list)
    tool_choice: str | dict[str, JsonValue] | None = None
    parallel_tool_calls: bool | None = None
    stream: bool | None = None
    # other optional fields...

    @model_validator(mode="after")
    def _validate_messages(self) -> "ChatCompletionsRequest":
        if not self.messages:
            raise ValueError("'messages' must be a non-empty list.")
        return self

    def to_responses_request(self) -> ResponsesRequest:
        data = self.model_dump(mode="json", exclude_none=True)
        messages = data.pop("messages")
        instructions, input_items = coerce_messages("", messages)
        data["instructions"] = instructions
        data["input"] = input_items
        return ResponsesRequest.model_validate(data)
```

```python
# app/core/openai/v1_requests.py
from app.core.openai.message_coercion import coerce_messages
# replace internal _coerce_messages with shared helper
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_chat_request_mapping.py::test_chat_messages_to_responses_mapping -v`  
Expected: PASS

**Step 5: Commit (if requested)**

```bash
git add app/core/openai/chat_requests.py app/core/openai/message_coercion.py app/core/openai/v1_requests.py tests/unit/test_chat_request_mapping.py
git commit -m "feat(openai): add chat request mapping"
```

---

### Task 3: Implement chat response conversion (stream + non-stream)

**Files:**
- Create: `app/core/openai/chat_responses.py`
- Test: `tests/unit/test_chat_response_mapping.py`

**Step 1: Write the failing test**

```python
# tests/unit/test_chat_response_mapping.py
from app.core.openai.chat_responses import iter_chat_chunks

def test_output_text_delta_to_chat_chunk():
    lines = [
        'data: {"type":"response.output_text.delta","delta":"hi"}\\n\\n',
        'data: {"type":"response.completed","response":{"id":"r1"}}\\n\\n',
    ]
    chunks = list(iter_chat_chunks(lines, model="gpt-5.2"))
    assert any("chat.completion.chunk" in c for c in chunks)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_chat_response_mapping.py::test_output_text_delta_to_chat_chunk -v`  
Expected: FAIL (missing function)

**Step 3: Write minimal implementation**

```python
# app/core/openai/chat_responses.py
from __future__ import annotations
import json
import time
from typing import Iterable, Iterator
from app.core.errors import openai_error

def _parse_data(line: str) -> dict | None:
    if line.startswith("data:"):
        data = line[5:].strip()
        if not data or data == "[DONE]":
            return None
        try:
            obj = json.loads(data)
        except json.JSONDecodeError:
            return None
        return obj if isinstance(obj, dict) else None
    return None

def iter_chat_chunks(lines: Iterable[str], model: str, *, created: int | None = None) -> Iterator[str]:
    created = created or int(time.time())
    for line in lines:
        payload = _parse_data(line)
        if not payload:
            continue
        if payload.get("type") == "response.output_text.delta":
            delta = payload.get("delta")
            chunk = {
                "id": "chatcmpl_temp",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\\n\\n"
        if payload.get("type") == "response.completed":
            done = {
                "id": "chatcmpl_temp",
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(done)}\\n\\n"
            yield "data: [DONE]\\n\\n"
            return
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_chat_response_mapping.py::test_output_text_delta_to_chat_chunk -v`  
Expected: PASS

**Step 5: Commit (if requested)**

```bash
git add app/core/openai/chat_responses.py tests/unit/test_chat_response_mapping.py
git commit -m "feat(openai): add chat response mapping"
```

---

### Task 4: Add `/v1/chat/completions` endpoint

**Files:**
- Modify: `app/modules/proxy/api.py`
- Modify: `app/modules/proxy/service.py` (if needed for streaming conversion)
- Test: `tests/integration/test_proxy_chat_completions.py`

**Step 1: Write the failing test**

```python
# tests/integration/test_proxy_chat_completions.py
import pytest

pytestmark = pytest.mark.integration

@pytest.mark.asyncio
async def test_v1_chat_completions_stream(async_client):
    payload = {"model": "gpt-5.2", "messages": [{"role": "user", "content": "hi"}], "stream": True}
    async with async_client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        lines = [line async for line in resp.aiter_lines() if line]
    assert any("chat.completion.chunk" in line for line in lines)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/integration/test_proxy_chat_completions.py::test_v1_chat_completions_stream -v`  
Expected: 404 or FAIL

**Step 3: Write minimal implementation**

```python
# app/modules/proxy/api.py
@v1_router.post("/chat/completions")
async def v1_chat_completions(
    request: Request,
    payload: ChatCompletionsRequest = Body(...),
    context: ProxyContext = Depends(get_proxy_context),
) -> Response:
    responses_payload = payload.to_responses_request()
    stream = context.service.stream_responses(
        responses_payload,
        request.headers,
        propagate_http_errors=True,
    )
    if payload.stream:
        return StreamingResponse(
            chat_stream(stream, model=payload.model),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )
    result = await chat_compact(stream, model=payload.model)
    return JSONResponse(result)
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_proxy_chat_completions.py -v`  
Expected: PASS

**Step 5: Commit (if requested)**

```bash
git add app/modules/proxy/api.py app/modules/proxy/service.py tests/integration/test_proxy_chat_completions.py
git commit -m "feat(api): add v1 chat completions bridge"
```

---

### Task 5: Full regression

**Step 1: Run full test suite**

Run: `uv run pytest`  
Expected: PASS

**Step 2: Commit (if requested)**

```bash
git commit -m "test: verify chat bridge"
```

