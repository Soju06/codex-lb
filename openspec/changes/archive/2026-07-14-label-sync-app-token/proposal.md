# Change: label-sync-app-token

## Why

The Codex review label sync workflow currently authenticates with a personal
access token (`CODEX_LABEL_SYNC_TOKEN`), which shares the token owner's global
5,000/hr REST quota with every other consumer of that PAT. Quota exhaustion by
unrelated automation repeatedly broke label sync until the `github.token`
fallback (change `label-sync-token-fallback`) papered over it at 1,000/hr.
A dedicated GitHub App installation token gets its own isolated 5,000/hr
quota, expires hourly, needs no machine account or collaborator seat, and can
be scoped to exactly the permissions the sync script uses.

## What Changes

- The `Codex review labels` workflow mints a short-lived GitHub App
  installation token (via `actions/create-github-app-token`, SHA-pinned) when
  the repository defines the `CODEX_LABEL_SYNC_APP_ID` variable and the
  `CODEX_LABEL_SYNC_APP_PRIVATE_KEY` secret, and prefers that token ahead of
  the existing `CODEX_LABEL_SYNC_TOKEN` → `RELEASE_PLEASE_TOKEN` →
  `github.token` chain.
- A failed or skipped mint never fails the job: the sync step falls back to
  the next token in the chain, and `GH_FALLBACK_TOKEN` (`github.token`)
  remains the script-level rate-limit escape hatch.
- No script changes: `sync_codex_ok_labels.py` already tolerates
  App-token write denials (`--tolerate-write-permission-errors`).

## Impact

- Affected specs: `github-automation` (write-token fallback requirement)
- Affected code: `.github/workflows/codex-review-labels.yml` (both jobs)
- Operator action: create the App (permissions: Actions RW, Checks R,
  Contents R, Issues RW, Pull requests R, Commit statuses R), install it on
  the repository, set the `CODEX_LABEL_SYNC_APP_ID` repository variable and
  `CODEX_LABEL_SYNC_APP_PRIVATE_KEY` secret. Until then the workflow behaves
  exactly as today.
