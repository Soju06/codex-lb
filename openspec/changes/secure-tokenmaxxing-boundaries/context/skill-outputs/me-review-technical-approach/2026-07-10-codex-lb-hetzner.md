# Technical Approach Review: codex-lb private deployment and cutover

Verdict: proceed after the revisions below are frozen in the execution contract.

Scope posture: hold.

Upstream artifacts: `docs/exec-plans/active/codex-lb-hetzner.md`, `openspec/changes/secure-tokenmaxxing-boundaries/context/skill-outputs/me-setup-repo/2026-07-10-codex-lb-inventory.md`.

## Restatement

- WHAT: commission the repository, deploy one four-account codex-lb, and resume the existing Codex goals through it.
- HOW: reviewed immutable source on a dedicated Hetzner `cpx21` in `ash`; a single SQLite instance and Fernet key in a root-owned persistent directory; container bound to loopback; Tailscale Serve HTTPS as the only client ingress; API-key and dashboard authentication; a fenced credential migration; canary-first client promotion; rollback that preserves the latest rotated grants.
- Non-goals: public ingress, multiple replicas, PostgreSQL, unrelated application behavior, destructive data work, or keeping Prodex as a second credential writer.
- Success constraints: no public steady-state listeners, no secret output, all four accounts pass repeated real requests, every stopped session has a deterministic checkpoint and resume path, and the exact reviewed SHA/digest is observed within declared bands.

## Findings and resolutions

- High — use `tailscale serve` because a loopback-bound container is otherwise unreachable from tailnet clients. Evidence: upstream production Compose publishes `2455` and `1455` on every interface; `app/cli.py` itself safely defaults to `127.0.0.1`. Flip condition: a direct tailnet-IP bind with equally strong listener proof.
- High — fence refresh ownership before import because both codex-lb and Prodex persist rotated grants. Stop and verify all old writers, then import. Resume raw Codex clients against codex-lb; do not restart an auth-refreshing Prodex mode. Evidence: account import encrypts tokens and the auth manager persists refreshed token material. Flip condition: executable proof that a Prodex external-provider mode never reads or refreshes profile grants.
- High — rollback must export the latest grant from codex-lb before restoring Prodex because a pre-cutover refresh token may be stale after rotation. Flip condition: upstream guarantees non-rotating reusable refresh grants, which is not established.
- High — pin the deployed commit and image digest because neither a moving beta branch nor a mutable tag is a rollback identity. Flip condition: a signed immutable release artifact with equivalent provenance.
- High — back up SQLite and its matching Fernet key as one recovery unit because either alone is unusable. Evidence: `app/core/crypto.py` and data-dir defaults in settings. Flip condition: migration to a managed external secret and database system, which is out of scope.
- Medium — require application authentication over the private network because tailnet access is not proxy authorization. Missing and invalid bearer tokens must return 401; one generated fleet key must succeed.
- Medium — `/health/ready` is insufficient because it does not prove upstream authentication, routing, or terminal streams. Require repeated per-account probes plus real Codex streaming/WebSocket smoke.
- Medium — session retagging must start with a dry-run ledger because the built-in command rewrites every matching provider tag, not only the currently visible panes.

## Stress test

Claims: 4 broke, 3 held, 0 untestable before revision.

- Broke: loopback service was assumed tailnet-reachable; fixed by pinning Tailscale Serve.
- Broke: import-before-stop admitted two refresh writers; fixed by fencing before import.
- Broke: restore-old-auth was assumed safe; fixed by latest-grant export before fallback.
- Broke: a branch name was treated as deploy identity; fixed by exact SHA and digest.
- Held: `cpx21` capacity is proportionate for one four-account SQLite instance, subject to declared memory/disk tripwires.
- Held: the repository already provides encrypted account import/export and Codex session retagging surfaces.
- Held: Tailscale-only ingress plus application authentication provides a coherent private boundary.

## Next route

`me-define-done` with the revisions above as mandatory criteria and stop conditions.
