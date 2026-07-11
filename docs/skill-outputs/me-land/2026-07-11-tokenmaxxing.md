# me-land decision packet: Tokenmaxxing

## Summary

- status: running
- target: reviewed commit `dac6a80c3dd22586e92118a95879e2ee749355c9`
- environment: one Hetzner cpx21 in `ash`, `tokenmaxxing.onda.systems`
- requested rungs: deploy, access policy, smoke, credential canary/promote,
  observe; land/merge remains governed by upstream PR #1213

## Authorization

- Receipt: `docs/skill-outputs/me-lfg/20260711-tokenmaxxing-context.md`
- Deploy target: one dedicated Hetzner cpx21 in Ashburn.
- Billing approval: direct user approval on 2026-07-11 after disclosure of
  USD 0.0601/hour and USD 37.49/month.
- Access/credential/smoke/observe scopes remain bounded by the receipt.

## Readiness

- Independent delivery review: PASS for `dac6a80c`.
- Independent security review: PASS source gate for `dac6a80c`.
- Native proof: 3,444 unit tests passed, 3 skipped; frontend 827 passed;
  Ruff, ty, package and wheel asset checks passed.
- PR: https://github.com/Soju06/codex-lb/pull/1213, open; deploy uses the
  reviewed immutable SHA and does not claim upstream landing.
- Credential files: four Prodex mode-0600 auth files present; values unprinted.

## Ladder and rollback

1. Create one server and source-restricted bootstrap firewall.
2. Install Docker, private administration, and named cloudflared tunnel.
3. Build/deploy `dac6a80c`; bind only `127.0.0.1:2455`.
4. Create exact-host Access app: Google OR OTP for `@onda.lol`, with WARP
   authentication enabled for native clients.
5. Smoke health/auth/origin-denial before importing credentials.
6. Fence old writers; import one account canary; prove refresh/request twice.
7. Promote remaining accounts or restore latest grant to exactly one writer.

Rollback before credential cutover: remove exact DNS/tunnel/Access route and
stop the container. Rollback after canary: stop Codex-LB, restore only the
latest canary grant to one Prodex writer, then remove ingress. Expected
time-to-restore: 15 minutes.

## Observation bands

- Advance: readiness 200; direct origin unreachable; Access denies missing,
  forged/non-domain and keyless native paths; live Codex canary succeeds twice;
  request error rate 0% during smoke; oldest log within 30 days.
- Hold: transient upstream failure with healthy local readiness, or CI/review
  pending with deployed SHA unchanged.
- Rollback: readiness remains non-200 after two repair attempts, origin becomes
  publicly reachable, auth bypass succeeds, credential refresh invalidates a
  grant, or canary request fails twice.
