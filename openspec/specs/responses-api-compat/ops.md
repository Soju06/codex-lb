# Responses API Compatibility Ops

## Purpose

This runbook describes the fastest repeatable way to answer three questions for a specific ChatGPT account:

1. Does the upstream websocket path complete successfully for this account?
2. What `response.service_tier` does the upstream actually return for this account?
3. Does `codex-lb` preserve the same result when `Codex CLI` uses websocket transport through the proxy?

Use this runbook when investigating `fast` tier behavior for `Codex CLI`.

## Preconditions

- Repo path: `/home/egor/services/codex-lb-defin85`
- Python env: `.venv`
- DB connection is configured in `.env.local`
- The target account is already imported into `codex-lb`
- `codex` CLI is installed on the host

## Important Constraints

- Do not treat raw `"service_tier":"fast"` in the websocket JSON payload as the source of truth.
  The ChatGPT websocket backend can reject that field with `Unsupported service_tier: fast`.
- The closest reproducible probe to native `Codex CLI` behavior is:
  - websocket transport
  - `response.create`
  - no explicit `service_tier` field in the JSON payload
- A successful websocket connection does not imply that the final tier is `fast`.
  Always inspect `response.completed.response.service_tier`.

## Step 1: Confirm the Account in the DB

Check that the account exists and note its plan:

```bash
PGPASSWORD='p-123456' psql -h 127.0.0.1 -U root -d codex_lb -c "select email,plan_type,status,chatgpt_account_id from accounts where email='TARGET_EMAIL';"
```

Interpretation:

- `status != active` means the result is not useful for tier verification.
- `plan_type` is reference context only. It does not prove entitlement to `fast`.

## Step 2: Direct Upstream Websocket Probe

This probe bypasses `codex-lb` selection and measures what the upstream returns for one imported account.

Run:

```bash
cd /home/egor/services/codex-lb-defin85 && .venv/bin/python - <<'PY'
import asyncio, json, os
from dotenv import dotenv_values
from sqlalchemy import select

from app.core.clients.http import close_http_client, init_http_client
from app.core.clients.proxy_websocket import connect_responses_websocket
from app.core.crypto import TokenEncryptor
from app.db.models import Account
from app.db.session import SessionLocal

EMAIL = "TARGET_EMAIL"
os.environ.update({key: value for key, value in dotenv_values(".env.local").items() if value is not None})

async def main():
    await init_http_client()
    try:
        async with SessionLocal() as session:
            result = await session.execute(select(Account).where(Account.email == EMAIL))
            account = result.scalar_one()

        token = TokenEncryptor().decrypt(account.access_token_encrypted)
        ws = await connect_responses_websocket(
            {
                "openai-beta": "responses_websockets=2026-02-06",
                "session_id": f"ws-tier-check-{account.chatgpt_account_id[:8]}",
                "x-codex-turn-metadata": "{\"turn_id\":\"\",\"sandbox\":\"seccomp\"}",
                "originator": "codex_exec",
                "user-agent": "codex-cli/0.113.0",
            },
            token,
            account.chatgpt_account_id,
        )
        try:
            await ws.send_text(json.dumps({
                "type": "response.create",
                "model": "gpt-5.4",
                "instructions": "Reply with OK only.",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "Say OK"}]}],
                "stream": True,
            }, separators=(",", ":")))

            for _ in range(50):
                msg = await asyncio.wait_for(ws.receive(), timeout=30)
                if msg.kind != "text" or not msg.text:
                    print(json.dumps({"result": msg.kind, "detail": msg.error or msg.close_code}))
                    return
                try:
                    payload = json.loads(msg.text)
                except json.JSONDecodeError:
                    continue
                if payload.get("type") == "error":
                    error = payload.get("error") or {}
                    print(json.dumps({"result": "error", "code": error.get("code"), "detail": error.get("message")}))
                    return
                if payload.get("type") == "response.completed":
                    response = payload.get("response") or {}
                    print(json.dumps({
                        "result": "completed",
                        "service_tier": response.get("service_tier"),
                        "status": response.get("status"),
                        "response_id": response.get("id"),
                    }))
                    return

            print(json.dumps({"result": "timeout"}))
        finally:
            await ws.close()
    finally:
        await close_http_client()

asyncio.run(main())
PY
```

Do not `source` `.env.local` directly in shell when URLs may include characters like `&`.
Load it with `python-dotenv` or quote those values first.

Expected useful output:

```json
{"result": "completed", "service_tier": "default", "status": "completed", "response_id": "resp_..."}
```

Interpretation:

- `service_tier = "fast"`:
  upstream entitlement exists for this account; continue to Step 3.
- `service_tier = "default"`:
  upstream completed normally, but this account did not receive `fast`.
- `result = "error"` with `Unsupported service_tier: fast`:
  the probe is wrong; remove raw `service_tier` from the JSON payload.

## Step 3: Verify `Codex CLI` Through Local `codex-lb`

Start a local proxy instance on a spare port:

```bash
cd /home/egor/services/codex-lb-defin85 && env CODEX_LB_USAGE_REFRESH_ENABLED=false CODEX_LB_MODEL_REGISTRY_ENABLED=false .venv/bin/fastapi run app/main.py --host 127.0.0.1 --port 2460
```

Prepare an isolated `HOME` for the CLI:

```bash
tmp_home="$(mktemp -d /tmp/codex-ws-check.XXXXXX)"
mkdir -p "$tmp_home/.codex"
cp "$HOME/.codex/auth.json" "$tmp_home/.codex/auth.json"
cat > "$tmp_home/.codex/config.toml" <<'EOF'
model = "gpt-5.4"
model_reasoning_effort = "xhigh"
model_provider = "codex-lb-ws"
service_tier = "fast"

[model_providers.codex-lb-ws]
name = "OpenAI"
base_url = "http://127.0.0.1:2460/backend-api/codex"
wire_api = "responses"
supports_websockets = true
EOF
```

Run the CLI:

```bash
HOME="$tmp_home" RUST_LOG=debug codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -C /home/egor/services/codex-lb-defin85 "Reply with OK only." > /tmp/codex-ws-run.out 2> /tmp/codex-ws-run.err
```

Confirm that websocket transport was used:

```bash
rg -n "connecting to websocket|successfully connected to websocket|POST /backend-api/codex/responses|fallback|Unsupported service_tier" /tmp/codex-ws-run.err
```

Useful signals:

- `connecting to websocket` and `successfully connected to websocket` must appear.
- `POST /backend-api/codex/responses` must not appear in the local server log for that run.
- The command output file should contain the model result, for example:

```bash
sed -n '1,40p' /tmp/codex-ws-run.out
```

Check the latest request log written by `codex-lb`:

```bash
PGPASSWORD='p-123456' psql -h 127.0.0.1 -U root -d codex_lb -c "select requested_at,account_id,request_id,model,service_tier,status,error_code,error_message from request_logs order by requested_at desc limit 5;"
```

Dashboard shortcut:
- the recent requests table now shows a `Transport` column
- `WS` means websocket proxy traffic
- `HTTP` means HTTP proxy traffic
- `--` means a legacy row written before transport logging existed

## Result Matrix

- Direct upstream probe = `default`, `codex-lb` run = `default`:
  proxy is behaving correctly; the account/upstream path is not yielding `fast`.
- Direct upstream probe = `fast`, `codex-lb` run = `default`:
  this is a real proxy regression; inspect websocket proxying and account selection.
- Direct upstream probe = `fast`, `codex-lb` run = `fast`:
  end-to-end support is confirmed.
- CLI run falls back to HTTP/SSE:
  websocket transport regression in the proxy path.

## Optional Deep-Dive: Capture Native `Codex CLI` Websocket Payload

Use this only when the direct probe and the proxy disagree.

Goal:

- confirm the exact headers and first `response.create` frame emitted by native `Codex CLI`
- compare them with the local probe or `codex-lb`

What to inspect from the capture:

- request headers:
  - `Authorization`
  - `chatgpt-account-id`
  - `openai-beta`
  - `session_id`
  - `x-codex-turn-metadata`
  - `originator`
- first websocket frame:
  - presence or absence of `service_tier`
  - model
  - instructions
  - input shape

## Current Known Findings

As of 2026-03-10, the following findings were reproduced from this repo workspace:

- `Codex CLI` uses websocket transport when `supports_websockets = true`.
- Native `Codex CLI` websocket captures did not show raw `service_tier` in the first `response.create` frame.
- Manually forcing `"service_tier":"fast"` in the websocket JSON payload can produce `Unsupported service_tier: fast`.
- Several imported `plus` and `team` accounts completed successfully but returned `response.service_tier = "default"`.
