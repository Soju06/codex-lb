# API Key Dashboard Context

## Purpose

`/key-dashboard` is a self-service surface for an API key holder, not an alternate administrator dashboard. It lets a key holder inspect only that key's lifetime usage and recent request activity without receiving a dashboard password or learning anything about the backing account pool.

## Decisions

- The route sits outside `AuthGate` and the administrator layout because those components initiate password-session, status, settings, and other operator-only requests.
- The existing `/v1/usage` endpoint remains the source for lifetime totals. A separate read-only endpoint supplies recent log rows because the administrator request-log response contains account, API key, client, source, routing, and failure metadata.
- A dedicated profile endpoint maps the already-validated API key through an explicit allowlist. It exposes display identity, lifecycle dates, and effective policy values without exposing the key ID, hash, assignments, usage-section configuration, or pooled account data.
- Configured limit consumption remains sourced from `/v1/usage`, avoiding a duplicate limit representation in the profile contract.
- The recent-log service derives the key ID from the validated Bearer credential and passes that ID directly to the repository filter. There is no client-controlled key selector.
- The response is constructed from a dedicated allowlist DTO. Redaction is not implemented by serializing the administrator DTO with selected values set to null, because field names alone reveal the operator data model and future fields could leak by default.
- The browser keeps the entered secret in component memory by default. An unchecked “remember on this browser” option may persist it in a namespaced local-storage entry only after a successful complete load; invalid authentication and Disconnect remove the entry. Requests omit cookies and bypass the global dashboard-session 401 handler so invalid keys cannot start or invalidate administrator auth flows.

## Constraints and failure modes

- API key authentication is mandatory for this surface even when proxy API key authentication is globally optional.
- A missing, unknown, inactive, or expired key receives an independent 401 response.
- If any initial request fails, partial results are not rendered. A 401 clears the in-memory and remembered credential and returns the user to key entry.
- Browser-local persistence is convenient but readable by scripts running in the same origin, so it is explicit and disabled by default rather than automatic.
- Soft-deleted logs and logs belonging to other keys are excluded by the server-side repository query.
- Account, plan, raw API key and database identity, client fingerprint, conversation/archive identity, model-source identity, upstream-proxy routing identity, and free-form failure details are intentionally unavailable in Overview. Only the key's display name and masked prefix are exposed as self-identification metadata. Install is an explicit credential-export action for the already-authenticated caller; its visible previews remain masked.

## Overview and client setup

Overview is the default tab and contains the existing profile, limits, colored lifetime summary, and recent-log grid. The profile policy presentation is now only Models: enforced model first, otherwise the allowed list or All models. Lifecycle dates use the browser timezone and the fixed 24-hour `HH:mm:ss  dd/MM/yyyy` format, independent of the administrator's date preference. Missing expiry and last-use dates retain their descriptive labels.

Install configures already-installed Codex App, CLI, and IDE extension clients with the currently logged-in API key. macOS and Linux use Bash; Windows uses PowerShell. The same script can be copied, downloaded as `.sh`/`.ps1`, or fetched by a curl command carrying the key in its Authorization header, never its URL. The server validates this header even when proxy authentication is globally optional and returns a private, no-store text response. Leaving Install or disconnecting cancels pending requests and discards the installer state; exported clipboard/file copies cannot be revoked.

Scripts target `CODEX_HOME` when set, otherwise the user's `.codex` directory. They preserve other files and create a unique private `backup-codex-lb-*` directory before replacing `config.toml` and `auth.json`. This deliberately replaces, rather than merges, user configuration: robust arbitrary TOML merging would introduce another runtime/parser requirement. Backups allow recovery by copying the old files back after closing clients. Never share backups or downloaded installers because they contain credentials. File permissions are user-only (Unix mode 600; Windows explicit ACLs), and scripts do not print the key.

The generated provider uses `model_provider = "codex-lb"`, provider `name = "openai"`, the dashboard origin plus `/backend-api/codex`, Responses wire format, websocket support, and `requires_openai_auth = true`. `cli_auth_credentials_store = "file"` and `auth.json` containing `OPENAI_API_KEY` avoid requiring environment variables in desktop GUI processes. Enforced or first allowed models are configured; unrestricted keys leave model selection to the client's own default.

This follows the repository's [client setup guide](../../../docs/client-setup.md) and OpenAI's official [authentication](https://developers.openai.com/codex/auth/), [advanced configuration](https://developers.openai.com/codex/config-advanced/), and [configuration reference](https://developers.openai.com/codex/config-reference/) documentation. API-key mode is for local workflows, not ChatGPT-only cloud features.

### Constraints and recovery

- Setup does not install Codex binaries or desktop applications. Restart the App, CLI, or editor after applying it. Client installers are available separately from OpenAI's [desktop documentation](https://developers.openai.com/codex/app/).
- WSL, SSH and remote extensions use the home directory in their execution environment. Run the Linux script inside WSL rather than applying Windows settings to an unrelated home.
- Project-level configuration and managed login requirements can override the generated user-level settings. The installer does not rewrite project configuration or administrator policy.
- Copying a direct command can retain the key in shell history or clipboard managers. Prefer downloading a file when history retention is a concern, keep exported files private, and remove exported copies when no longer needed.
- Symlink/non-file targets or backup failures stop setup before replacement. If a later write fails, the previous configuration and authentication remain in the printed/named backup directory for recovery. No changes are made on the operator's server by viewing or downloading a script.

### Setup example

The Install presentation separates platform selection and the terminal command into two numbered steps, with file export and a collapsed masked preview in their own card. Setup guidance appears beside the actions on desktop and below them on mobile. This keeps the primary command easy to scan without concealing replacement, restart, or security caveats. Native radio selection, a check indicator, and visible keyboard focus remain available in either theme. Long commands wrap; expanded script previews scroll within a bounded area. This is a presentation-only change, with no new endpoint or query-string authentication.

An authenticated key holder opens Install, chooses macOS, and clicks Download script. They run `bash codex-lb-macos.sh`; the script backs up any existing configuration, writes the current endpoint and key, and prints the backup location without the key. Restarting Codex App and the IDE extension picks up the same file-backed credentials when both use that Codex home. On Windows the equivalent file is `codex-lb-windows.ps1`; the shown command bypasses execution policy only for that process, without changing persistent machine policy.

## Example

A user opens `/key-dashboard`, optionally enables “remember on this browser,” enters `sk-clb-…`, and the browser concurrently requests `/api/key-dashboard/profile`, `/v1/usage`, and `/api/key-dashboard/request-logs?limit=25&offset=0` with that value in the Bearer header. After all three succeed, Overview may show the key name and masked prefix, lifecycle and models, limit consumption, 42 requests, 12K total tokens, 3K cached tokens, $0.42 cost, and recent rows with model/status/token/latency values. It cannot show which account handled a row, the API key's database ID/hash, a client IP, or any upstream proxy route.
