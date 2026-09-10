# Desktop pooled usage context

[Contract](spec.md). [Setup and rollback](../../../docs/desktop-pooled-usage.md).

## Purpose

The intended outcome is to keep the original ChatGPT account and connected capabilities while inference uses codex-lb's imported account pool. Desktop's own usage poll follows a different base URL from inference. The optional relay joins those paths for usage and forwards the caller's credentials on other backend requests. The real trial below verified identity, account settings, native pooled quota and Astra execution on the inspected build.

## Routing decisions

The standalone `desktop-relay` command binds loopback port 8000, matching the unchanged auth allowlist observed in Desktop `26.903.61454`. `CODEX_API_BASE_URL` changes Electron-owned backend routing. The inference provider base and Rust account base remain independent. Changing the Rust account base can alter hosted MCP authentication before a request reaches any relay.

The relay uses the existing aiohttp dependency, fixed ChatGPT origin, verified TLS and a loopback LB origin. It retains no shared cookies, follows no redirects and logs no payloads or credentials. It forwards HTTP and WebSocket traffic with explicit connection ownership. The normal LB listener and setup defaults are unchanged. `CODEX_LB_DESKTOP_RELAY_MODE` defaults to `off`; `loopback` lets the normal server own both listeners on a native host, and `container` accepts Docker ingress while Docker publishes port 8000 only on host loopback. A separate process is unnecessary. The fixed port and mode avoid independently configurable listener/upstream combinations. Embedded mode requires a supported HTTP main listener; standalone mode supports a separately configured HTTPS LB with certificate verification.

The separate strict endpoint avoids changing the older `/api/codex/usage` contract. The upstream usage parser retains a private original JSON envelope without changing its existing serialized model. Quota composition uses that envelope to retain unknown account-owned fields instead of reconstructing them from a lossy parser.

## Quota meaning

Main window percentages reuse LB's existing plan-capacity weights. A Plus account at 100% weekly usage and a Pro account at 20% produce 30% used after truncation, using capacities 7,560 and 50,400. Those weights are LB estimates, not a measured token or currency entitlement.

Additional limits use the arithmetic mean for equal-plan contributors with equal window durations. Across different plans, only an equal percentage on every contributor is independent of unknown capacity weights. Other mixed-plan results and differing durations are unavailable. There is no model-specific capacity table from which to derive different weights. A model is available only when the same eligible account has both main and model quota. The original caller's reserve bucket remains account-owned.

Freshness uses the shared horizon, currently 180 seconds. The projection refreshes without holding its initial database session, then reads sequentially. It does not infer resets from elapsed time or quietly discard missing accounts. Weekly primary/secondary selection reuses the [existing tiebreak](../usage-refresh-policy/spec.md), followed by strict validation of the selected effective row. Thus a real weekly-primary sample can beat a no-data secondary placeholder without treating the placeholder as unused quota.

## Limits and recovery

Some historical missing-window cases remain conservatively unavailable. Original account credits, billing and spending restrictions can still block Desktop despite available main pool quota. Unknown restriction markers remain intact. The native UI has no per-account breakdown.

The Desktop override also affects signed remote-control challenges, cookie registration and some dictation routing. Exact-message passthrough does not establish that client-side origin or cookie-domain checks will accept the result. No signed challenges, application files or auth guards are rewritten. The corrected candidate removes an explicit chatgpt.com Domain attribute from non-usage Set-Cookie fields so they remain usable through localhost; cookie values and other attributes remain unchanged.

Real acceptance is separate from synthetic proof. Record the retained displayed identity, a genuine native pooled-usage observation and an actual request with the intended model. Until that trial completes, this is a locally tested candidate. Rollback restores the prior Desktop environment and restarts the app; the separate relay can then be stopped.

## Observed Desktop trial

The 2026-09-09 trial used candidate `1aa75e14f2cdcc6f5223a64b68c573f7b207a630` against base `c0beaaadd96a89f0240582b5449bf4dd50647c7d`, Desktop `26.903.61454` and bundled CLI `0.153.4`. A private candidate database and separate relay left the existing inference service intact. Initial temporary jobs stopped when Desktop quit, causing connection-refused failures. A later restart also removed the GUI-domain jobs. Docker gave the trial an independent lifecycle.

A genuine Desktop request to the strict pooled endpoint returned 200. The user confirmed that the name and usage control returned, but account settings still failed with `DeviceCheck registration failed (403)`, and several account routes received upstream HTML challenges. Restoring the original Desktop launch environment restored the user's purple accent and normal behavior. The trial services and copied credentials were then removed. These observations disprove treating byte-preserving HTTP forwarding as sufficient account-feature acceptance; they do not identify every cause of the upstream challenges.

The exact displayed pooled percentage and a successful intended-model request were not verified. The zero remaining and Luna-only restriction reported after rollback were from the original exhausted account, not an observed zero-valued pooled response.

Installed-source inspection separates three paths: inference follows the model provider URL, Rust account quota follows `chatgpt_base_url`, and Desktop's native quota and reserve restriction follow its direct `/wham/usage` query. The inspected reserve gate requires a matching identity and plan, a `luna_reserve` banner, main `allowed=false`, and an allowed `gpt-reserve` bucket. The existing composition targets that gate when pool evidence establishes main availability. A visible usage control alone does not prove the picker used the intended quota or that an intended-model request succeeded.

No narrow Desktop quota URL override was found. DeviceCheck registration and its cookie lookup also use the broad Desktop base, so redirecting non-usage calls to the official origin is not an established fix. The later trial established account/settings behavior and compared the actual quota and model execution. No application patch, trust change, signed-challenge rewrite or authentication bypass is part of this candidate.

Candidate `b1f1e234d7b5c1e6897f3c9127b77122398c7a7e` corrected the demonstrated cookie-domain mismatch. A Chromium cookie-store check and the exact installed CookieManager running in an isolated Electron 42.3.0 process rejected the original foreign-domain cookie and accepted the host-only cookie while retaining Secure and HttpOnly. Neither test modified the installed application.

The second live trial ran the candidate in an independently managed Docker container, with private quota-only account data and the existing inference service unchanged. The original inference provider retained `requires_openai_auth = true`. Genuine DeviceCheck, profile, account and settings requests returned 200, and the client reused the official response cookies through localhost. The user confirmed the original name, purple accent and 38% remaining. Sanitized native usage evidence recorded 62% used in the weekly window, main `allowed=true`, `limit_reached=false` and no `luna_reserve` banner. The original caller had main `allowed=false`. The existing LB recorded successful `gpt-6-astra` WebSocket requests for the active task after this observation. These are real acceptance results for this build; remote-control enrollment and every other connected feature were not exhaustively tested.

The user then authorized integrating the relay into the existing LB container. Local deployment uses the exact existing runtime revision as its parent so unrelated local fixes and its database graph remain intact. The upstream contribution contains only this capability. No schema or credential migration is needed for the relay.

The one-container cutover subsequently passed using local runtime `effc2a944313fbf3325330785ba3a3d3e95a1076`, with the existing volume and database graph retained. The user reconfirmed the account name, accent and quota; the new container received authenticated native usage requests and recorded successful Astra requests. The temporary quota container and copied account data were removed. The local runtime integration passed 278 affected tests, including existing context behavior. This local acceptance is separate from upstream PR #2286 review and merge.

The pool-refresh wait has a fixed five-second aggregate deadline. If upstream accounts take longer, the request reads persisted observations and applies the same strict freshness checks. Shared singleflight refreshes keep their own session ownership; caller cancellation still propagates. The deadline does not bypass original-account authentication.
