# API Key Dashboard Context

## Purpose

`/key-dashboard` is a self-service surface for an API key holder, not an alternate administrator dashboard. It lets a key holder inspect that key's lifetime usage and recent request activity, plus shared aggregate usage when an administrator assigns a usage group, without receiving a dashboard password or learning anything about the backing account pool.

## Usage groups

An optional Usage group field in the administrator key creation/edit forms enables mutual aggregate visibility. Set Alice's and Bob's keys to `Team A`, then either holder can open Group keys to compare both keys' request counts, tokens, cached tokens, and cost for the preceding 30 days. This shares the full recent window, including activity before joining. Clear the field to remove membership; exact case-sensitive names are compared after trimming surrounding whitespace. Empty groups have no independent lifecycle.

Membership is read from persisted key rows on each group request, independent of cached authentication metadata. Only names, masked prefixes, a caller indicator, and totals leave the dedicated allowlist response. Inactive and expired members still contribute historical usage; deleted or reassigned members disappear on refresh. Group membership does not change quotas, routing, or access to peer request logs.

The group view also includes a dense daily series for every member across the rolling 30-day UTC window. The chart can switch between tokens and USD cost, hide or show member lines, and expose the same values in a scrollable data table. The server supplies zero-valued days so lines and tables stay aligned; the summary totals are calculated from that same series. Tooltips show the exact date and value without another request.

The nullable indexed field leaves existing installs ungrouped and needs no runtime setting. Retained hourly rollups and their complementary raw windows provide historical statistics without double counting; warmup and limit-warmup traffic are excluded. Preserved usage remains countable after account deletion. As with existing rollup readers, raw retention may remove a partial-hour boundary that cannot be reconstructed more precisely from an hourly total.

Group data loads only while its tab is mounted, uses cookie-free Bearer requests, and is discarded on disconnect, unauthorized responses, or tab unmount. Refresh replaces the prior group state so a removed membership does not leave an obsolete table visible during reload.

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

Install configures already-installed Codex App, CLI, and IDE extension clients with the currently logged-in API key. macOS and Linux use Bash with Python 3 (standard library only); Windows uses PowerShell. The same script can be copied, downloaded as `.sh`/`.ps1`, or fetched by a curl command carrying the key in its Authorization header, never its URL. The server validates this header even when proxy authentication is globally optional and returns a private, no-store text response. Leaving Install or disconnecting cancels pending requests and discards the installer state; exported clipboard/file copies cannot be revoked.

Scripts target `CODEX_HOME` when set, otherwise the user's `.codex` directory. They preserve other files and create a unique private `backup-codex-lb-*` directory before replacing `config.toml`, `auth.json`, and `codex-lb-models.json`. This deliberately replaces, rather than merges, user configuration: robust arbitrary TOML merging would introduce another runtime/parser requirement. Backups allow recovery by copying the old files back after closing clients. Never share backups or downloaded installers because they contain credentials. File permissions are user-only (Unix mode 600; Windows explicit ACLs), and scripts do not print the key.

The generated provider uses `model_provider = "codex-lb"`, provider `name = "openai"`, the dashboard origin plus `/backend-api/codex`, Responses wire format, and `requires_openai_auth = true`. `cli_auth_credentials_store = "file"` and `auth.json` containing `OPENAI_API_KEY` avoid requiring environment variables in desktop GUI processes. Enforced or first allowed models are configured after checking that they are present in the fetched catalog; unrestricted keys select the first visible API model. If an exported default was removed, setup stops and asks the user to export a new installer.

Each execution fetches the native catalog from `GET /api/key-dashboard/models` with the exported codex-lb key, keeps list-visible API entries, and writes their complete metadata to `codex-lb-models.json`. The absolute path is written as `model_catalog_json`. Public aliases, base instructions, tool capabilities and agent metadata stay intact. The server continues to own upstream credentials and alias mappings. This endpoint reuses the native catalog builder but always authenticates the key, even when ordinary proxy authentication is optional; it does not reserve inference limits and sends private, no-store headers.

Provider WebSockets remain enabled only if every installed entry advertises `prefer_websockets=true`. A catalog containing custom HTTP models uses `supports_websockets=false`, allowing users to switch between installed models through one provider without a WebSocket retry. Ordinary proxy discovery behavior remains unchanged.

Rerun the downloaded script or the Install command after adding aliases or changing capability metadata, then restart clients. For example, an allowed alias `cd/gpt-6-astra=ch/linxaq` installs as `cd/gpt-6-astra`; clients use that public name and the server applies the upstream mapping. Refresh reads the current catalog even from an older exported script. There is no background synchronization.

This follows the repository's [client setup guide](../../../docs/client-setup.md) and OpenAI's official [authentication](https://developers.openai.com/codex/auth/), [advanced configuration](https://developers.openai.com/codex/config-advanced/), and [configuration reference](https://developers.openai.com/codex/config-reference/) documentation. API-key mode is for local workflows, not ChatGPT-only cloud features.

### Constraints and recovery

- Setup does not install Codex binaries or desktop applications. Restart the App, CLI, or editor after applying it. Client installers are available separately from OpenAI's [desktop documentation](https://developers.openai.com/codex/app/).
- WSL, SSH and remote extensions use the home directory in their execution environment. Run the Linux script inside WSL rather than applying Windows settings to an unrelated home.
- Project-level configuration and managed login requirements can override the generated user-level settings. The installer does not rewrite project configuration or administrator policy.
- Copying a direct command can retain the key in shell history or clipboard managers. Prefer downloading a file when history retention is a concern, keep exported files private, and remove exported copies when no longer needed.
- Failed downloads, redirects, empty or malformed catalogs, unavailable exported defaults, symlink/non-file targets, or backup failures stop setup before replacement. If a later write fails, the previous configuration, authentication and catalog remain in the printed/named backup directory for recovery. No changes are made on the operator's server by viewing or downloading a script.

### Setup example

The Install presentation separates platform selection and the terminal command into two numbered steps, with file export and a collapsed masked preview in their own card. Setup guidance appears beside the actions on desktop and below them on mobile. This keeps the primary command easy to scan without concealing replacement, restart, or security caveats. Native radio selection, a check indicator, and visible keyboard focus remain available in either theme. Long commands wrap; expanded script previews scroll within a bounded area. The terminal command authenticates with a Bearer header rather than a URL parameter.

An authenticated key holder opens Install, chooses macOS, and clicks Download script. They run `bash codex-lb-macos.sh`; the script backs up any existing configuration, downloads the current allowed native catalog, writes the endpoint, key and catalog path, and prints the backup location without the key. Restarting Codex App and the IDE extension picks up the same file-backed credentials when both use that Codex home. On Windows the equivalent file is `codex-lb-windows.ps1`; the shown command bypasses execution policy only for that process, without changing persistent machine policy.

## Example

Administrator route recovery remains inside `AdministratorApp` and its authentication boundary. Unknown administrator paths show the recovery view, but subsequently navigating to `/key-dashboard` exits that boundary and loads the standalone lazy route. The key entry screen does not request administrator session, settings, status or account data; route errors must not turn key authentication into administrator login. See [the route recovery contract](spec.md).

A user opens `/key-dashboard`, optionally enables “remember on this browser,” enters `sk-clb-…`, and the browser concurrently requests `/api/key-dashboard/profile`, `/v1/usage`, and `/api/key-dashboard/request-logs?limit=25&offset=0` with that value in the Bearer header. After all three succeed, Overview may show the key name and masked prefix, lifecycle and models, limit consumption, 42 requests, 12K total tokens, 3K cached tokens, $0.42 cost, and recent rows with model/status/token/latency values. It cannot show which account handled a row, the API key's database ID/hash, a client IP, or any upstream proxy route.
