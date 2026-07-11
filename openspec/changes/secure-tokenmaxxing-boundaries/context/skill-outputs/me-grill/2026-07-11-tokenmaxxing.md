# Tokenmaxxing decision record

## Status

Resolved in an interactive `me-grill` session on 2026-07-11. This record is
the product/auth source for the successor execution contract; it authorizes no
deployment or credential mutation.

## Chosen direction

Tokenmaxxing is the company-facing name and hostname for the existing
Codex-LB product. The upstream dashboard remains visually and functionally
unchanged and is served at `tokenmaxxing.onda.systems`. Codex-LB becomes the
single gateway and OAuth refresh-token writer for the four current Codex
accounts, replacing Prodex for active routing.

## Resolved decisions

1. Keep the upstream Codex-LB dashboard rather than build a separate web UI.
2. Preserve the original Codex-LB visual design, navigation, and displayed
   product name. `Tokenmaxxing` is the service/hostname identity only.
3. Protect the hostname with one Cloudflare Access layer. Users may choose
   Google OAuth or Cloudflare One-Time PIN as alternative login methods.
4. Allow every authenticated address ending in `@onda.lol`.
5. Every admitted `@onda.lol` user intentionally receives the dashboard's full
   administrative powers. Codex-LB currently has no read-only role.
6. Enroll employee Macs in the Onda Cloudflare One Client/WARP organization;
   the menu-bar app uses that user/device identity and contains no shared
   Cloudflare service token.
7. The macOS menu-bar headline is normalized five-hour pool utilization on a
   `0%` to `100%` scale, not the summed remaining percentages. For `N` included
   accounts: `used = 100 * (1 - sum(remaining_percent) / (100 * N))`.
   Disabled accounts are excluded; exhausted/rate-limited accounts count as
   fully used. Weekly, Spark, per-account, reset, and stale/error detail live in
   the popover.
8. All `@onda.lol` users may send Codex requests through the shared pool. Issue
   one Codex-LB API key per person or device; never distribute one company-wide
   key. Do not impose initial per-user token or cost caps.
9. Store client API keys in macOS Keychain and user Codex configuration, with
   individual attribution and revocation.
10. Distribute the menu-bar app as a signed and notarized DMG with automatic
    updates. Apple Developer ID and notarization credentials are a release
    prerequisite, not a reason to weaken Gatekeeper behavior.
11. Persist no prompt or response bodies. Automatically delete request-level
    metadata after 30 days; longer-lived records may contain aggregated usage
    only and must not permit reconstruction of an employee's request history.

## Decided without asking

- Keep Codex-LB on a dedicated host with loopback-only application listeners;
  use Cloudflare Tunnel for the hostname instead of opening inbound ports.
- Configure Codex-LB trusted-header authentication only for the loopback tunnel
  proxy and validate Cloudflare Access identity at the boundary. This remains a
  single user-visible authentication layer.
- Keep the macOS app in a separate private Onda repository so upstream
  Codex-LB updates do not inherit native-client release machinery.
- Refresh capacity on the server's native usage cadence (currently 60 seconds),
  show an explicit stale timestamp, and avoid alerts/notifications in v1.
- Treat the existing single-writer cutover and latest-grant rollback rules as
  mandatory; do not run Prodex and Codex-LB as concurrent refresh writers.

## Assumptions and prerequisites

- Material: Onda controls the `onda.systems` Cloudflare zone and a Zero Trust
  organization that can configure Google, OTP, Access, Tunnel, and WARP.
- Material: all intended users can receive mail at `@onda.lol`; control of that
  mailbox is intentionally sufficient for OTP admission.
- Blocking for macOS release: an active Apple Developer Program membership,
  Developer ID Application certificate/private key, notarization credentials,
  and an approved update-signing path must be available without printing or
  committing secrets.
- Blocking for cutover: all four source identities must be checkpointed and
  old refresh writers fenced before the first import.

## Deferred branches

- Per-user hard usage caps and notification thresholds are deferred until real
  usage provides a defensible baseline.
- Dashboard RBAC/read-only roles and visual rebranding are explicitly excluded.
- iOS, Windows, and Linux native clients are excluded.

## ADR decision

Skip: no separate ADR. The hard-to-reverse auth, ingress, single-writer, and
retention decisions belong in the execution contract and active rollout plan,
which are the operational sources of truth.

## Glossary decision

Skip: no durable product glossary is needed. `Tokenmaxxing`, `Codex-LB`,
five-hour utilization, and single refresh writer are defined directly in this
decision record and its successor contract.

## Next route

`me-define-done` using this record plus the existing Hetzner rollout contract.
