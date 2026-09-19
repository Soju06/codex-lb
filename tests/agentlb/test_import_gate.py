from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import app_client, run  # noqa: E402


async def _main() -> None:
    async with app_client() as client:
        response = await client.post(
            "/api/accounts/import/api-key",
            json={"provider": "openrouter", "apiKey": "sk-or-test-import"},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["status"] in {"active", "quota_exceeded"}
        account_id = payload["accountId"]
        exported = await client.post(f"/api/accounts/{account_id}/export/auth")
        assert exported.status_code == 200, exported.text
        tokens = exported.json()["tokens"]
        assert tokens["accessToken"] == "sk-or-test-import"
        assert tokens["refreshToken"] == "sk-or-test-import"


if __name__ == "__main__":
    run(_main())
