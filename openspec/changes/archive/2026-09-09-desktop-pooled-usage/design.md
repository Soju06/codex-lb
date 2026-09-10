## Context

See [proposal.md](proposal.md). Desktop inference, Rust account services, and Electron backend requests use independent routing controls. The inspected Desktop build accepts authenticated HTTP at literal `localhost:8000`. Its backend override also covers non-usage HTTP, cookies, integrity and some WebSocket operations.

Existing `/api/codex/usage` aggregates pool plan and credits and can expire rows optimistically. It cannot be used unchanged as the original account's Desktop envelope. The upstream usage parser currently discards unknown fields, including some Desktop restrictions.

## Goals / Non-Goals

Add a conservative quota projection and an optional local relay without replacing the established inference stack. Keep all account-owned authority with the original ChatGPT account. A pool summary is evidence of pool quota, not a guarantee that every model, sticky thread or account feature can run.

Application patching, certificate interception, account switching, automatic Desktop mutation are outside this change. Users explicitly opt in to the relay and launch environment.

## Decisions

1. Add `/api/codex/desktop/usage` in a small module. Reuse ChatGPT identity validation and preserve the raw validated upstream JSON privately on the parsed usage object. Existing serialized schemas stay unchanged. This avoids a second caller usage fetch and prevents reconstruction from lossy parsed fields.
2. Refresh using the current owned-session updater, then read fresh account and quota rows sequentially. Reuse capacity mappings and weighting. Reject incomplete, stale, expired or malformed evidence, including uncertain historical windows, instead of changing the existing usage endpoint's reset policy. Reuse the current 180-second freshness horizon rather than add a tuning setting.
3. Compose the original envelope with strict main and model quota. Preserve account-owned and unknown fields. Clear only understood superseded quota-exhaustion markers; unknown restrictions remain effective.
4. Ship the relay as an optional CLI subcommand using the established aiohttp transport. Fixed ChatGPT upstream, loopback LB origin, no cookie jar, no redirect following, no payload logging. Streaming connections and WebSocket pumps have explicit lifetime ownership. The standalone command remains supported. The normal server can own the aiohttp listener in its lifespan through the default-off off/loopback/container mode. Container ingress binds internally on 8000, with host publication restricted to both loopback families. Startup failure must run cleanup; shutdown closes the relay before shared resources. Embedded mode derives the HTTP loopback quota origin from the normal listener and rejects TLS, invalid ports and unsupported bind hosts.
5. Keep the Rust account base untouched. A broad Rust override changes hosted MCP auth classification before transport. The independent Desktop environment hook is sufficient for the proposed usage request path.

## Risks / Trade-offs

- Conservative freshness can temporarily return 503 when a historical window is no longer reported. A proven complete snapshot can justify a future relaxation; current timestamps alone do not establish omission.
- Account-owned credit or spend restrictions can still prevent model selection despite available pool quota. The implementation keeps those restrictions truthful.
- Remote enrollment/refresh compares signed challenge origins against the configured Desktop base. Cookie domain rules also differ at loopback. Passthrough preserves messages but cannot promise these client-side checks succeed. Report observed regressions; never rewrite signed challenges.
- The new quota endpoint requires a candidate LB build. A real trial therefore needs a coordinated temporary service change or isolated real-account instance as well as the Desktop launch override. The user authorized integration into the existing LB container after the successful real trial. The local runtime candidate must retain the exact deployed parent and migration graph.

## Migration Plan

Existing clients require no changes. Desktop users opt in after importing their original account into their authorized LB pool. Start the candidate LB, then the relay, then set the launch environment and fully restart Desktop. Record identity, native quota and actual model success separately. To roll back, restore the previous launch environment, restart Desktop, stop the relay and restore the previous LB candidate if it was temporarily changed.

## Cookie scope correction after the first trial

The first live trial exposed the difference between preserving cookie bytes and preserving cookie behavior. Chromium rejects a Domain=chatgpt.com cookie received from localhost. The relay now removes only an explicit official-host Domain attribute on non-usage responses, preserving cookie values and all other attributes. Host-only, foreign-domain and invalid __Host-prefixed cookies remain unchanged. The original app's cookie-manager code passed an isolated Electron 42.3.0 test with this adaptation. No cookie values are imported from the installed app, no server cookie jar is shared, and no signed challenge or verification result is modified. This fixes the proven domain mismatch; any registration 403 before cookie issuance still needs separate live diagnosis.
