# Context — Fix OAuth link endpoints

Operational notes for change `fix-claude-oauth-link-endpoints`. The normative
spec lives at `specs/claude-oauth-pool/spec.md` and
`specs/clipboard-copy-fallback/spec.md`.

## Empirical evidence that the original `console.anthropic.com/oauth/code` redirect was wrong

The Claude Code CLI's authorize URL (the URL it prints to the terminal after
the operator runs `claude` and is prompted to authenticate) is:

    https://claude.com/cai/oauth/authorize
        ?code=true
        &client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e
        &response_type=code
        &redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback
        &scope=org%3Acreate_api_key+user%3Aprofile+user%3Ainference+user%3Asessions%3Aclaude_code+user%3Amcp_servers+user%3Afile_upload
        &code_challenge=…
        &code_challenge_method=S256
        &state=…

The exact same `redirect_uri` is confirmed by multiple public bug reports
against Anthropic's own `anthropics/claude-code` repository, all showing
`platform.claude.com/oauth/code/callback` (with trailing `/callback`) as the
working value:

- `anthropics/claude-code#37831` (Mar 2026) — error message captured
  includes "Redirect URI https:/platform.claude.com/oauth/code/callback is
  not supported by client" when the URL is malformed.
- `anthropics/claude-code#39445` (Mar 2026) — Claude Code itself uses
  `platform.claude.com` for its redirect URI.
- `anthropics/claude-code#44719` (Apr 2026) — operator reaches the
  `platform.claude.com/oauth/code/success` page with the auth code displayed.
- `anthropics/claude-code#57985` (May 2026) — "OAuth URL always contains
  client_id=9d1c250a-... and redirect_uri pointing to platform.claude.com".

The `code=true` query parameter selects Anthropic's OOB code-display flow
(instead of attempting a normal browser-redirect flow that requires a
loopback redirect). Omitting it causes Anthropic to render a normal consent
flow that we cannot complete without a local HTTP callback server.

## Why the original verification missed this

`add-claude-oauth-link/proposal.md` stated:

> "The Anthropic OAuth endpoint URLs and `id_token` claim names are verified
> against public sources; the redirect URI `https://console.anthropic.com/oauth/code`
> is verified against the Claude Code client's behavior. Live verification of
> the exchange against a real Anthropic account is out of scope and stays an
> operator-side step."

The verification was paper-only — no automated test was written to assert the
URL Anthropic actually accepts, and no end-to-end test exercised the real
authorize endpoint. Operators (this reporter included) hit Anthropic's
real-world reject on the very first click.

## Frontend Copy button — pattern drift

The `clipboard-copy-fallback` spec already mandates a shared clipboard
utility (`frontend/src/utils/clipboard.ts`) with a `document.execCommand`
fallback and a `<CopyButton>` component (`frontend/src/components/copy-button.tsx`)
that wraps it. The OAuth account-add dialog's "Copy URL" button bypasses both
and calls `navigator.clipboard.writeText` directly with `void`, swallowing
rejection and offering no user feedback. This change closes that drift.

## Operator upgrade notes

- Existing operators who had not overridden
  `CODEX_LB_CLAUDE_OAUTH_REDIRECT_URI` get the corrected URL automatically.
- Operators who had hand-overridden it to a non-default value keep their
  override (env vars still take precedence).
- Operators who set the override to `https://console.anthropic.com/oauth/code`
  manually (matching the buggy default) will need to update their override to
  `https://platform.claude.com/oauth/code/callback` to make the flow work.

## What this change does NOT do

- Does not modify the manual paste endpoint (`POST /api/claude/accounts`).
- Does not modify `app/modules/oauth/*` (Codex flow).
- Does not modify `ClaudeOAuthClient.refresh` (token refresh path).
- Does not modify proxy routing, refresh logic, or rate-limit handling.
- Does not change the scope default (`user:profile user:inference`) — the
  redirect URI fix is sufficient to unblock the flow; scope widening is a
  separate change.
