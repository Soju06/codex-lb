# Proposal: add-oauth-account-proxy

## Why

When a new account is added via the OAuth dialog (browser, manual-callback, or
device-code flow), it is persisted as `ACTIVE` with no proxy configured. The
token refresh scheduler and usage fetcher then immediately reach OpenAI from
the server's real IP, which defeats the purpose of running multiple accounts
behind per-account egress proxies. Operators currently have only two
workarounds, both bad:

1. Add the account via OAuth, then quickly attach a proxy via
   `PUT /api/accounts/{id}/proxy` — but the refresh scheduler may race the
   attach and leak one or more unproxied refreshes.
2. Use the import flow, which already supports atomic proxy probe + persist,
   and skip OAuth entirely — but that requires obtaining `auth.json` out of
   band.

The import flow (`POST /api/accounts/import`) solves this atomically: it
accepts proxy fields in the same request, probes the proxy with a real OAuth
refresh through it, and persists the account, proxy fields, and rotated
tokens in a single transaction. The OAuth flow needs the same atomicity.

## What Changes

### Backend

- Extract the shared probe + upsert + invalidate sequence from
  `import_account` into a new `AccountsService.persist_account_with_optional_proxy`
  helper so OAuth and import never drift in proxy-probe semantics.
- Extend `OauthStartRequest` with `expect_proxy: bool = False` and the proxy
  fields needed for server-side OAuth bootstrap/token-exchange. When `True`,
  those OAuth calls use the submitted SOCKS5 proxy and the OAuth flow defers
  persistence to `complete_oauth` (see below). When `False` (the default),
  persistence happens at token arrival as today — preserving the existing spec
  scenario for non-dashboard callers.
- Add `pending_tokens`, `pending_expires_at`, and `expect_proxy` fields to
  `OAuthState`. When `expect_proxy=True`, the three token-arrival sites
  (`_handle_callback`, `manual_callback`, `_poll_device_tokens`) store the
  acquired tokens in `pending_tokens`, set status to `tokens_ready`, set a
  10-minute TTL, and do NOT persist.
- Extend `OauthCompleteRequest` with proxy fields mirroring
  `AccountProxyInput` (host, port, username, password, remote_dns, label).
  `complete_oauth` becomes the persistence entrypoint: it builds an
  Account from `pending_tokens`, calls the shared helper with the
  required proxy payload, and atomically probes + persists. On
  `ProxyProbeError`, it keeps `pending_tokens` so the user can retry with
  corrected proxy fields without redoing the upstream sign-in.
- Add `ProxyProbeError` and `ValidationError` exception handlers to the
  `POST /api/oauth/complete` endpoint, returning the same 422 envelope
  shape used by `POST /api/accounts/import`. Emit an `account_proxy_set`
  audit log entry on successful OAuth+proxy persistence, matching the
  import flow.
- Inject `AccountsService` into `OauthContext` so `OauthService` can call
  the shared helper.

### Frontend

- Extract the proxy form JSX from `import-dialog.tsx` (~200 lines) into a
  reusable `ProxyFormSection` component. Both dialogs render it.
- Update `oauth-dialog.tsx`: show the collapsible
  "Configure egress proxy (optional)" section on the intro stage so the user
  commits the start-time proxy before clicking Browser/Device. Continue
  rendering the section on browser/device stages so the selected proxy remains
  visible during the auth wait. Toggle state at the moment of method-click
  locks `expect_proxy` for that OAuth attempt. If the final proxy probe fails,
  the operator can correct the completion-time proxy fields and retry without
  redoing upstream sign-in; all server-side OAuth calls before `tokens_ready`
  used the start-time proxy.
- Add a new `tokens_ready` stage to the dialog state machine. When the
  status poll returns `tokens_ready`, the dialog renders a "Finish setup"
  button. Click → frontend calls `complete_oauth` with the current proxy
  fields. When `expect_proxy=False`, behavior is unchanged (status polls to
  `success` automatically as today).
- Update `OauthStartRequestSchema`, `OauthCompleteRequestSchema`, and
  `OAuthStateSchema` to carry the new fields and the `tokens_ready` state.
- `use-oauth.ts`: extend `start()` to accept `expectProxy`; extend
  `complete()` to accept a proxy payload for deferred OAuth attempts; surface the new
  `tokens_ready` poll status.
- Surface probe failures via the existing `formatProbeError` helper,
  preserving the typed-reason UX from the import dialog.

### OpenSpec

- New scenario in `account-egress-proxy/spec.md`: "OAuth add-account with
  proxy validates before account activation" — mirrors the existing
  "Import with proxy validates before account activation" scenario.
- Modified scenario in `frontend-architecture/spec.md`: qualify "Device
  OAuth start begins polling" with "when `expect_proxy=false`."
- New scenario in `frontend-architecture/spec.md`: "OAuth add-account with
  proxy defers persistence to `/api/oauth/complete`."

## Impact

- **Behavior**: New code paths only fire when `expect_proxy=true`. Existing
  no-proxy OAuth callers see no behavioral change. Dashboard users who do
  not toggle the proxy section also see no change. Dashboard users who do
  configure proxy now atomically probe + persist, and the account never
  exists in the database in an unproxied-active state. This guarantee covers
  codex-lb server-side OAuth calls and persisted account egress; the
  operator's own browser/device navigation to upstream sign-in pages remains
  outside backend egress control.
- **Database**: Adds nullable per-account proxy columns to `accounts` plus a
  `proxy_remote_dns` boolean defaulting to true. The migration is idempotent
  so deployments that already have the columns can safely advance.
- **Specs**: One new scenario in `account-egress-proxy`, one modified and
  one new scenario in `frontend-architecture`. The original "Backend starts
  polling without requiring separate /api/oauth/complete call" guarantee
  remains intact for the `expect_proxy=false` path.
- **Tests**: New tests cover (a) the shared `persist_account_with_optional_proxy`
  helper, (b) deferred persistence on `expect_proxy=true`, (c) probe-failure
  retry preserving `pending_tokens`, (d) TTL expiry, (e) concurrent
  token-arrival idempotence, (f) auto-persist preserved on
  `expect_proxy=false`, (g) the OauthDialog renders the proxy section,
  surfaces `tokens_ready`, and finalizes with proxy fields.

## Capabilities

### Modified Capabilities
- `account-egress-proxy`
- `frontend-architecture`
