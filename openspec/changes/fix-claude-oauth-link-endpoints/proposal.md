## Why

The `add-claude-oauth-link` change (shipped in PR #14) shipped with
`claude_oauth_redirect_uri` defaulted to `https://console.anthropic.com/oauth/code`,
verified against secondary sources but never against a live Anthropic account.
The proposal explicitly stated: *"Live verification of the exchange against a
real Anthropic account is out of scope and stays an operator-side step."*

Operators running the flow today hit Anthropic's reject response on the very
first authorize click:

    Authorization failed
    Redirect URI https://console.anthropic.com/oauth/code is not supported by client.

The redirect URI registered for the public Claude Code OAuth client
(`9d1c250a-e61b-44d9-88ed-5944d1962f5e`) is
`https://platform.claude.com/oauth/code/callback`, NOT
`console.anthropic.com/oauth/code`. The Claude Code CLI confirms this — the
URL it prints in the terminal after `claude` is invoked is:

    https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e
      &response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback
      &scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload
      &code_challenge=...&code_challenge_method=S256&state=...

Three operator-facing symptoms result:

1. **OAuth account-add flow fails on the first authorize click.** Operators
   cannot add Claude accounts through the dashboard until they hand-override
   `CODEX_LB_CLAUDE_OAUTH_REDIRECT_URI`. The "Add Claude account via OAuth"
   button is non-functional out of the box.
2. **Copy URL button does nothing.** The OAuth dialog bypasses the shared
   `copyToClipboard` utility (which falls back to `document.execCommand("copy")`
   and shows a toast on failure) and calls `navigator.clipboard.writeText`
   directly with `void`, swallowing any rejection. The `clipboard-copy-fallback`
   spec already mandates the shared utility; this dialog is non-compliant.
3. **Even if operators copy the URL by hand and submit, Anthropic rejects it
   on the `/authorize` page** because the configured `redirect_uri` is not in
   the whitelist for this client_id.

## What Changes

- **Backend defaults**: `claude_oauth_redirect_uri` →
  `https://platform.claude.com/oauth/code/callback`; `claude_oauth_authorize_endpoint`
  → `https://claude.com/cai/oauth/authorize`. Both in
  `app/core/config/settings.py`. `.env.example` updated to match.
- **URL builder**: `ClaudeOAuthService.start_oauth` adds `code=true` as the
  first query parameter so the URL exactly matches the Claude Code CLI
  handshake. The `code=true` flag selects the OOB code-display flow on
  Anthropic's authorize endpoint (without it, the page attempts a normal
  browser-redirect flow that we cannot use).
- **Frontend Copy button**: Replace the inline `navigator.clipboard.writeText`
  in `add-claude-account-oauth-dialog.tsx` with the shared `<CopyButton>`
  component, so the clipboard write has a working fallback path and surfaces
  failures with a toast instead of silently failing.

## Capabilities

### Modified Capabilities

- `claude-oauth-pool`: Pin the default authorize endpoint and redirect URI to
  values Anthropic actually accepts for the public Claude Code OAuth client.
  Add an explicit `code=true` query parameter that selects the OOB code-display
  flow.
- `clipboard-copy-fallback`: Reinforce that operator-facing "copy" controls
  MUST use the shared `copyToClipboard` utility (or a component that wraps it)
  so the fallback path and error feedback apply uniformly.

## Impact

- **Backend**: Defaults updated in `app/core/config/settings.py`; one-line
  change in `app/modules/claude/oauth/service.py::start_oauth` to add
  `code=true` to the params dict; `.env.example` updated.
- **Frontend**: `frontend/src/features/claude/components/add-claude-account-oauth-dialog.tsx`
  replaces the inline button with the shared `<CopyButton>` component.
- **Tests**: New unit test pins the production defaults; integration-shaped
  test pins the authorization URL produced by `start_oauth()`. Frontend
  vitest covers the dialog's Copy button path.
- **Operator behavior**: Operators who had NOT overridden
  `CODEX_LB_CLAUDE_OAUTH_REDIRECT_URI` get the corrected URL automatically
  after upgrading. Operators who had hand-overridden it to a different value
  keep their override (env vars still take precedence over the new defaults).
- **Compatibility**: Manual paste (`POST /api/claude/accounts`) and existing
  `POST /v1/oauth/token` refresh path are untouched.
