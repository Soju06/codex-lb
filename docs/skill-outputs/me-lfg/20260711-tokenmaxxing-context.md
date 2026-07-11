---
run_id: 20260711-tokenmaxxing
objective: Finish, deploy, and independently verify Tokenmaxxing: the unchanged Codex-LB dashboard at tokenmaxxing.onda.systems behind Cloudflare Access for onda.lol users, the four-account single-writer gateway and API-key path, and the WARP-authenticated signed macOS capacity client where release prerequisites exist.
initiator: human
provenance:
  kind: direct_current_user
  source: current conversation; explicit "$malaysian-engineering:me-lfg" invocation on 2026-07-11 after the Tokenmaxxing grill and execution contract
session_kind: autonomous
execution_status: running
authorized_actions:
  - action: repo_edit
    targets: [/code/codex-lb/.worktrees/me-setup-repo, codex/me-setup-repo, /code/tokenmaxxing-macos]
  - action: commit
    targets: [codex/me-setup-repo, local tokenmaxxing-macos repository]
  - action: publish_pr
    targets: [Soju06/codex-lb, codex/me-setup-repo]
  - action: merge
    targets: [Soju06/codex-lb, pull request created by this run]
  - action: release
    targets: [reviewed immutable Codex-LB image, signed/notarized Tokenmaxxing macOS DMG and update feed]
  - action: deploy
    targets: [dedicated Hetzner cpx21 in ash for Codex-LB, tokenmaxxing.onda.systems Cloudflare Tunnel route]
    limits: one application server; no public host ports; new billable infrastructure remains readiness-blocked unless the prior exact price/target grant is independently revalidated
  - action: promote
    targets: [four current Prodex-managed Codex accounts, Tokenmaxxing gateway, current Codex client configurations, existing Prodex-launched Codex sessions, macOS employee release]
  - action: credential_change
    targets: [four current Prodex auth profiles, Codex-LB encrypted credential store, per-person/device Codex-LB API keys, local Codex client configuration]
    limits: migrate/repoint/rotate/revoke only within the declared single-writer and per-device-key contract; never print values
  - action: access_policy_change
    targets: [Cloudflare Access application tokenmaxxing.onda.systems, its Google and One-Time PIN login methods, onda.lol email-domain allow policy, Cloudflare Tunnel route, Onda WARP application authentication, dedicated Hetzner firewall]
    limits: one user-visible Access layer; all admitted onda.lol users are full Codex-LB administrators; no non-onda.lol admission; no public origin listener
  - action: smoke
    targets: [Tokenmaxxing browser dashboard, both Access login methods, denial control, four imported accounts, API-key create/use/revoke flow, real Codex request, WARP macOS capacity path, resumed sessions]
  - action: observe
    targets: [Tokenmaxxing deployment, Access/Tunnel, four-account gateway, request-metadata retention, migrated clients, macOS release]
  - action: rollback_on_declared_tripwire
    targets: [Cloudflare route/policy, Codex-LB deployment, four-account credential store, local client configuration, Prodex sessions, macOS update channel]
    limits: only the predeclared rollback after its tripwire fires
parent_run_id: 20260710-codex-lb-hetzner
repository: /code/codex-lb
worktree: /code/codex-lb/.worktrees/me-setup-repo
branch: codex/me-setup-repo
base_revision: 65dc4b75be2de837968dcdf86ec233f5d5f0ad72
created_at: 2026-07-11T11:43:00Z
limits:
  retry_cap: 2
  max_iterations: 7
  deployment_count: 1
  observation_window_minutes: 15
exclusions:
  - no force push, review bypass, CI bypass, or self-declared passing criteria
  - no destructive production-data mutation or deletion except the contract-required 30-day metadata expiry after fresh review and backup-lifecycle proof
  - no unvalidated billing, payment, refund, or unrelated provider mutation
  - no customer/public messages or public unauthenticated release
  - no prompt or response body persistence
  - no public inbound application, SSH, or HTTPS origin ports
  - no shared Cloudflare service token or company-wide Codex-LB API key embedded in a client
  - no concurrent Prodex and Codex-LB refresh-token writers
  - do not terminate unrelated Claude, OpenCode, Herdr, tmux, or repository processes
  - do not expose or commit OAuth grants, cookies, API keys, encryption keys, Apple keys, notarization credentials, HARs, or secret-bearing environment files
---

This receipt is the immutable authority and scope record for the Tokenmaxxing
LFG lifecycle. Only `execution_status` may be updated by the coordinator.
Readiness gates, fresh independent review, rollback, and proof remain mandatory.
