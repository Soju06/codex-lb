# Codex Desktop pooled usage

This opt-in integration keeps the original ChatGPT login while showing the codex-lb pool in Desktop's native usage display. The ordinary [client setup](client-setup.md) still routes inference without it.

Source of truth: [Desktop pooled usage specification](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/desktop-pooled-usage).

## What changes

Desktop has its own usage request. Setting the model provider URL routes inference through LB, but does not redirect that request. The optional relay routes Desktop's `/backend-api/wham/usage` to LB's strict `/api/codex/desktop/usage` endpoint. It forwards other Desktop backend requests to ChatGPT with the original caller's credentials.

The original ChatGPT account must be imported into the LB instance. Its token and account header authorize the whole eligible pool, matching the existing ChatGPT-authenticated native usage contract. An LB API key is not accepted for this endpoint, and key-specific budgets or account assignments do not redefine this pool. Use this setup only when the whole imported pool is the pool you intend Desktop to show.

The response keeps the original account's identity, plan, credits, spend controls, billing and saved reset credits. It replaces the quota windows and additional model limits supported by current pool evidence. It does not grant a subscription, fabricate credit balances or guarantee that a particular model or existing sticky thread can run.

## Availability and limits

- Main quota uses LB's existing plan-capacity weights. Percentages are not added together. Exhausted accounts remain in the totals; paused, deactivated and reauthentication-required accounts are excluded.
- Additional model quota averages accounts on the same plan with equal window durations. Mixed-plan buckets are accepted only when every reported percentage is equal, making the result independent of unknown weights. Otherwise the strict endpoint returns unavailable. Main plan weights do not establish model-specific credit capacity. Model availability requires main and additional capacity on the same eligible account.
- A window's reset is the earliest reported reset among its contributors. It is not the time the entire pool resets.
- Pool refresh waits at most five seconds in aggregate, then evaluates persisted observations with the same freshness rules. Missing, stale, elapsed or malformed applicable evidence returns HTTP 503 with `pooled_usage_unavailable`. Current freshness is three minutes. An unsuccessful refresh cannot turn an elapsed window into new capacity. The conservative projection can also reject historical windows whose omission has not been established.
- Reserves, account credits and spend restrictions remain owned by the original account. They can still restrict Desktop even when some pool quota is available.
- Desktop has no native per-account breakdown. Use LB's dashboard to see which account is exhausted.

## Compatibility status

Static routing and schema checks were made against Codex Desktop `26.903.61454` with bundled CLI `0.153.4` on macOS. The inspected build's existing authentication allowlist accepts literal `localhost:8000`; substituting `127.0.0.1:2455` or another port does not establish the same authenticated path.

Deterministic relay and quota tests pass. The accepted 2026-09-09 trial preserved the original account name and purple accent, completed real account/settings requests, displayed 38% pool quota remaining and completed `gpt-6-astra` requests through LB. An earlier trial failed because process lifetime and cookie scope were not sufficient. The accepted candidate uses an independent service lifetime and adapts official response-cookie domains for localhost. This evidence applies to the inspected build; repeat compatibility checks after Desktop updates.

The inspected Desktop uses its direct `/wham/usage` response for the Luna reserve model restriction as well as quota display. Changing Rust `account/rateLimits/read` routing alone does not redirect that Desktop query. No quota-only Desktop URL override was found in the inspected build. See the [compatibility findings](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/desktop-pooled-usage/context.md#observed-desktop-trial) for the observed failures and successful follow-up.

`CODEX_API_BASE_URL` is a Desktop launch-environment override, not a TOML setting or a documented stable OpenAI API. Other Desktop backend requests use it too. Signed remote-control enrollment and refresh compare challenge origins, and cookie registration can depend on the destination domain. The relay preserves signed messages and cookie values. It removes only an explicit chatgpt.com Domain attribute from non-usage response cookies, retaining Secure, HttpOnly, SameSite, Path and expiry so Chromium can use them through localhost. It does not rewrite signed challenges. These features require separate compatibility observation. A correct account name alone does not prove every connected feature works.

## Run in the existing LB container

Set `CODEX_LB_DESKTOP_RELAY_MODE=container` on the existing LB service and add these port publications alongside its normal ports:

```yaml
environment:
  CODEX_LB_DESKTOP_RELAY_MODE: container
ports:
  - "127.0.0.1:8000:8000"
  - "[::1]:8000:8000"
```

The normal LB process owns the relay, including startup and shutdown. The relay listens on the container interface and sends quota requests to the same LB's configured HTTP port. Publish 8000 only on host loopback. A bare `8000:8000` exposes authenticated Desktop backend forwarding to the network.

For a native LB process, `CODEX_LB_DESKTOP_RELAY_MODE=loopback codex-lb` starts both listeners on the same machine. The default is `off`. Embedded mode requires an HTTP listener reachable over loopback with a nonzero port other than 8000. Unsupported hosts and TLS settings fail startup. Use the standalone command for an HTTPS LB.

## Start the standalone relay

Use a candidate or release containing `/api/codex/desktop/usage`. An older server that only has `/api/codex/usage` cannot provide the strict projection. Start LB normally, then run the relay on the same machine as Desktop:

```bash
codex-lb desktop-relay
```

It connects to `http://127.0.0.1:2455` by default. For a separate local LB port:

```bash
codex-lb desktop-relay --lb-url http://127.0.0.1:2456
```

The relay binds IPv4 and IPv6 loopback on port 8000. Its local LB destination must be an HTTP(S) loopback origin without credentials, a path, query or fragment. It always forwards non-usage backend traffic to `https://chatgpt.com`, validates upstream TLS, and does not follow redirects. It honors configured HTTP/WebSocket/SOCKS outbound proxies for ChatGPT traffic, including the existing explicit WebSocket direct-connect override. Local LB requests always stay direct. It has no shared cookie jar and emits no access or payload logs. The normal server's `--host`, `--port`, `HOST` and `PORT` settings do not change the standalone listener. Do not enable embedded mode and the standalone command on the same host at the same time.

A local unauthenticated check should reach LB and return 401:

```bash
curl -i http://localhost:8000/backend-api/wham/usage
```

This verifies routing only. It is not pooled-usage or login proof. Do not paste ChatGPT tokens into shell commands or diagnostic logs.

## Configure Desktop on macOS

Keep your existing inference provider URL and `requires_openai_auth = true`. Keep the original ChatGPT login. Leave `chatgpt_base_url` and `CODEX_APP_SERVER_CHATGPT_BASE_URL` unchanged. Repointing those Rust account controls also changes hosted account/MCP behavior.

Record whether `CODEX_API_BASE_URL` was already set and its previous value:

```bash
launchctl getenv CODEX_API_BASE_URL
```

Coordinate a full Desktop quit before changing the launch environment. A running app does not reload `launchctl` values. After the candidate LB and relay are ready:

```bash
launchctl setenv CODEX_API_BASE_URL http://localhost:8000/backend-api
```

Launch Desktop normally. This does not modify the installed app or its certificate trust. No app patch, injection, re-signing or authentication-guard change is part of this setup.

If the LB is already running for inference, a separate candidate can handle quota during a trial. It needs genuine authorized account data and current upstream observations. A copied database must not independently rotate the live instance's refresh tokens or run copied automations. Keep its storage and lifecycle separate. Never point test schema setup or candidate migrations at a live/shared database.

## Check the result

1. Confirm the expected original account name in Desktop. User observation is sufficient for this check.
2. Confirm a real Desktop usage request reaches the candidate and the native windows match its genuine pooled values. An unauthenticated curl or fixture response is insufficient.
3. Select the intended model and complete a small request through the existing LB inference route. Check the actual model recorded for the request; a selected menu label alone is insufficient.

Record the app/build identity, candidate revision, observed quota, model evidence and any feature regressions. Keep tokens and account identifiers out of public reports.

## Recovery and rollback

If the relay cannot bind port 8000, identify the existing listener before proceeding. Do not replace another process or change Desktop to an arbitrary port. If quota returns 404, the LB probably lacks the strict endpoint. A 401 indicates caller validation failed; check that the original account is imported and eligible. A 503 means the strict projection lacks current evidence or upstream validation failed. Check account health and refresh results in LB.

If login, quota, dictation or another backend feature regresses, restore the previous launch environment and fully restart Desktop. If no previous override existed:

```bash
launchctl unsetenv CODEX_API_BASE_URL
```

If there was a previous value, restore it with `launchctl setenv` instead. After Desktop restarts, stop a standalone relay with Ctrl-C, or disable the embedded mode and recreate the LB service. Stop and remove the temporary candidate separately if one was used. Keep the original LB service and its volume intact. This rollback does not require changing ChatGPT login or inference configuration.

The `launchctl` setting lasts for the current login session. An embedded relay follows the existing container restart policy; a standalone relay follows its process lifetime. For use after login, arrange user-managed startup that sets the Desktop environment and starts LB before Desktop. Repeat compatibility checks after Desktop updates.
