# Experimental Codex Desktop auth bridge

Source of truth: [desktop-auth-bridge specification](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/desktop-auth-bridge/spec.md).

The optional `codex-lb desktop-bridge` command translates a local Codex
ChatGPT bearer token into a CodexLB API key. It lets a compatible client keep
the built-in `openai` provider while sending Codex API requests through a
separate loopback listener. The normal server and dashboard do not start or
enable this command automatically.

This is an experimental transport, related to
[issue #2262](https://github.com/Soju06/codex-lb/issues/2262). Synthetic HTTP and
WebSocket tests establish the relay contract. A local acceptance run with the
installed Desktop runtime `0.162.0-alpha.17.2` and CodexLB `1.24.0` also completed
model discovery, a normal WebSocket turn, and an ephemeral
`experimentalPredictionMode` fork and turn while retaining the `openai` provider.
A separate manual test on macOS with Desktop `26.1007.30912` confirmed that a
Composer suggestion appeared in the same thread after about 10 seconds and
could be accepted with Tab. This is one tested setup, not a latency guarantee
or proof of compatibility with every Desktop feature. Realtime, mobile,
Windows, and remote-host support remain unverified.

## Compatibility boundary

- Both the bridge and CodexLB must be reachable on the same machine's IPv4
  loopback. A containerized LB must expose its port on the host's loopback.
- The client must send the access token from its file-backed ChatGPT
  `<codex-home>/auth.json`. The bridge does not log in, refresh tokens, or read
  the OS keychain. API-key login and externally managed credentials are not
  supported.
- A client using an agent-identity bearer distinct from `auth.json` is rejected.
  Do not disable a client security feature to work around this limitation.
- Requests with an `Origin` are rejected; browser-based clients are unsupported.
  Cookies and redirects are intentionally unsupported in this first version.
- Only `/backend-api/codex` and its descendants are forwarded. The dashboard,
  `/v1`, and other backend namespaces are not exposed by the bridge.
- Keeping a provider ID does not grant an account a feature, change model
  context limits, or bypass a service's eligibility checks.
- POSIX file ownership and mode checks cannot validate Windows ACLs. On Windows,
  only regular-file, size, and content checks apply; Windows acceptance is untested.

## Start the bridge explicitly

1. Keep CodexLB running on `127.0.0.1:2455` with API-key authentication enabled.
   Create a dedicated, appropriately scoped LB API key in its dashboard.
2. Using a local editor or secret manager, put **only that LB key** in a file,
   for example `~/.config/codex-lb/desktop-bridge-token`. Use one line, with an
   optional final newline. Do not put it in the command line or commit it.
3. On POSIX, both this file and the existing Codex `auth.json` must be regular
   files owned by the current user with no group/other permissions (`chmod 600`).
   Final-component symlinks are rejected. Keep their parent directories private.
   The bridge validates these requirements without modifying the files.
4. Run the new command in a separate terminal:

   ```sh
   codex-lb desktop-bridge \
     --listen-port 2456 \
     --lb-port 2455 \
     --lb-token-file "$HOME/.config/codex-lb/desktop-bridge-token"
   ```

   `--listen-port` and `--lb-token-file` are required. `--lb-port` defaults to
   `2455`. Both ports must be between 1 and 65535 and differ. Both addresses
   are fixed to `127.0.0.1`; there is no remote-upstream option.
   Use `--codex-home PATH` if the client uses a different Codex data directory.
   Otherwise, the existing Codex home resolution (including `CODEX_HOME`) applies.
   Main-server `HOST`, `PORT`, TLS and Uvicorn options do not configure this command.

After backing up the client's config, a **compatible test client** can use
these top-level settings (before any TOML section):

```toml
model_provider = "openai"
openai_base_url = "http://127.0.0.1:2456/backend-api/codex"
```

Keep the chosen model and reasoning settings. Do not add the LB key to the
client config or replace `auth.json`. The bridge reads both credential files
again for each request, so atomic credential rotations take effect without a
bridge restart. Existing in-flight requests retain their authenticated session.
Codex may write its login file in place. During an incomplete write the bridge
returns `503`; it accepts the new token once the file is valid. A client still
holding the old token gets `403`. If this persists after another Codex process
signs in or refreshes the shared login, restart Desktop to reload its credentials.
The bridge does not keep accepting an old token or modify the login file.

For an initial acceptance test, use synthetic prompts in a separate test
session. Confirm ordinary HTTP/streaming responses, WebSocket reconnects,
model catalog and compaction first, then test any Desktop feature individually.
Do not infer Composer support from a successful model response alone.

## Updates

The bridge ships in the same Python package as CodexLB and uses its existing
dependencies. Update that package through the normal official release channel,
then restart the bridge process. The command does not download or install its
own code. CodexLB's version notification is not an automatic installer, and
updating a Docker LB does not update a bridge running on the host.

Forwarded model metadata and new routes within the allowed namespace do not
need a static catalog or per-route bridge update. Changes to client credentials,
headers or authentication formats can still require compatibility work.

For unattended deployments, keep update scheduling outside the relay. Stage
and verify a specific official stable package in a separate environment, test
HTTP and WebSocket compatibility, and restart between active sessions. Keep the
previous environment, including its dependencies, for rollback. This contribution
does not provide that installer or guarantee compatibility with future clients.

On a graceful process stop, the bridge closes WebSockets with code 1001 and
allows short HTTP responses to finish. Unfinished streams are disconnected
without replay. WebSocket cleanup has a concurrent 10-second deadline; HTTP
completion and cancellation each have a 5-second window afterward. Set a
supervisor's forced-stop timeout to at least 45 seconds to leave room for
transport cleanup. Schedule restarts between active turns: shutdown does not
transfer work to the replacement process.

The bridge can start before a local Docker LB is ready. Requests return 502
until the LB begins listening, and subsequent requests recover without a bridge
restart. The bridge does not retry the failed request automatically.

## Data handling

The Desktop bearer is compared locally and removed before forwarding. The LB
receives its own API key, never the original `Authorization`, actor credential,
account-ID, cookie or forwarding headers. Response authentication fields and
cookies are removed as well. Other client metadata and request/response bodies
are intentionally preserved: **this is not a prompt anonymizer**. CodexLB and
the upstream inference service still receive normal task content according to
their own logging and retention policies.

The bridge has no database, telemetry or access log. It does not print tokens,
request URLs, bodies, or credential-file paths in its own error messages. It
ignores proxy environment variables and does not follow redirects. Tests and
examples use synthetic credentials; no operator config, accounts, history,
database, or diagnostic exports are needed for contributing this feature.

## Errors and limits

| Result | Meaning |
| --- | --- |
| `400` before dispatch | Malformed target (non-origin form, dot segment, encoded escape, `//`, backslash or fragment), or a WebSocket subprotocol offer split across header fields |
| `404` before dispatch | Path outside `/backend-api/codex`, or an authority-form `CONNECT` rejected by routing |
| `405` before dispatch | `CONNECT` or `TRACE` reaching the request-policy check |
| `403` before dispatch | Missing/mismatched bearer, unsupported identity, Host or Origin policy failure |
| Persistent `403` after a shared login refresh | Desktop may still hold an old token; restart it, then recheck the file-backed login |
| `503` before dispatch | Credential file became unavailable/invalid, all 16 relay slots are occupied, or shutdown has begun |
| `502` | LB returned `401` or a redirect, or the upstream connection failed |
| `413` | Request exceeds the 128 MiB body budget |
| LB `403`, `426`, `429`, etc. | Passed through with sanitized headers; investigate the LB response |

The bridge never turns a local rejection into `401`, which could cause Codex
to refresh its ChatGPT login unnecessarily. It never retries a non-idempotent
request. HTTP bodies remain compressed if received compressed, with a 128 MiB
wire-byte limit in each direction. WebSocket messages allow up to 128 MiB.
Connections have a 10-second connect deadline, a 300-second idle deadline,
and a 2-hour lifetime. A stream that fails after starting is closed, not
reported as successfully completed. These limits are implementation defaults,
not new dashboard settings.

To revert a test, restore the client's previous provider/base-URL settings
and stop the foreground bridge with Ctrl-C. Normal CodexLB operation remains
independent. Never post auth files, LB keys, raw prompts, or unredacted logs
when reporting an issue.
