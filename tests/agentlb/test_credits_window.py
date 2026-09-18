from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from _harness import app_client, run  # noqa: E402


async def _main() -> None:
    async with app_client() as client:
        exhausted = await client.post(
            "/api/accounts/import/api-key",
            json={
                "provider": "openrouter",
                "apiKey": "sk-or-zero",
                "accountId": "or-zero",
                "creditsBalance": 0,
                "creditsCap": 10,
                "creditsSpent": 10,
            },
        )
        assert exhausted.status_code == 200, exhausted.text
        funded = await client.post(
            "/api/accounts/import/api-key",
            json={
                "provider": "openrouter",
                "apiKey": "sk-or-funded",
                "accountId": "or-funded",
                "creditsBalance": 8,
                "creditsCap": 10,
                "creditsSpent": 2,
            },
        )
        assert funded.status_code == 200, funded.text

        listed = await client.get("/api/accounts")
        assert listed.status_code == 200, listed.text
        by_id = {row["accountId"]: row for row in listed.json()["accounts"]}
        zero = by_id[exhausted.json()["accountId"]]
        live = by_id[funded.json()["accountId"]]

        credits = zero["credits"]
        assert credits is not None
        assert credits["balance"] == 0
        assert credits["cap"] == 10
        assert "usedPercent" not in credits
        assert zero["usage"]["primaryRemainingPercent"] == 100
        assert zero["status"] == "quota_exceeded"

        assert live["credits"]["balance"] == 8
        assert live["credits"]["cap"] == 10
        assert live["status"] != "quota_exceeded"


if __name__ == "__main__":
    run(_main())
