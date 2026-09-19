from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import app_client, run  # noqa: E402


async def _main() -> None:
    async with app_client() as client:
        door = await client.post(
            "/v1/messages",
            json={
                "model": "qwen/qwen3-coder-30b-a3b-instruct",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert door.status_code == 503, door.text
        payload = door.json()
        assert payload["error"]["type"] == "no_available_openrouter_accounts"
        assert "OpenRouter" in payload["error"]["message"]
        assert "Anthropic" not in payload["error"]["message"]

        glm = await client.post(
            "/v1/messages",
            json={
                "model": "glm-5.2",
                "max_tokens": 32,
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert glm.status_code == 503, glm.text
        glm_payload = glm.json()
        assert glm_payload["error"]["type"] == "no_available_glm_accounts"
        assert "GLM" in glm_payload["error"]["message"]


if __name__ == "__main__":
    run(_main())
