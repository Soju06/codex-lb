# Codex-lb Hetzner rollout context

## Status

Active. Current pickup point: implement AC1–AC6 from the original frozen
contract before any external write, then follow the Tokenmaxxing successor
contract for ingress, authentication, cutover, retention, and the macOS client.

Canonical lifecycle receipt: `docs/skill-outputs/me-lfg/20260710-codex-lb-hetzner-context.md`

Current renewed Tokenmaxxing lifecycle receipt:
`docs/skill-outputs/me-lfg/20260711-tokenmaxxing-context.md`

Successor product/auth contract:
`docs/skill-outputs/me-define-done/2026-07-11-tokenmaxxing.md`

Run context:

- Run ID: `20260710-codex-lb-hetzner`
- Current run ID: `20260711-tokenmaxxing` (successor; parent run retained for
  chain-of-custody)
- Initiator/provenance: direct current user
- Session kind: autonomous
- Repository: `/code/codex-lb/.worktrees/me-setup-repo`
- Branch: `codex/me-setup-repo`
- Base: `origin/main` at `65dc4b75be2de837968dcdf86ec233f5d5f0ad72`
- Authorized local actions: clone, worktree-local setup edits, verification, local commits
- Authorized external action classes: provision/deploy codex-lb on Hetzner; import the four current Codex credentials; checkpoint, stop, reconnect, restart, and resume existing Prodex-launched Codex sessions
- External target: dedicated Hetzner `cpx21` in `ash`; loopback-only origin
  reached through Cloudflare Tunnel and Access at
  `tokenmaxxing.onda.systems`; no public host ports
- Exclusions: no force-push, upstream PR publication, merge, destructive production data work, unrelated agent termination, or public inbound ports

## Acceptance criteria

| Criterion | State |
|---|---|
| Every Malaysian Repo Standard is assessed with live evidence | active |
| Reversible setup gaps are fixed and independently reviewed | not_started |
| Deployment target, rollback, smoke, and observation bands are pinned | active |
| codex-lb is deployed with secrets sealed, no public host ports, and Access/Tunnel ingress | not_started |
| All four Codex accounts import and pass quota/model probes | not_started |
| Existing Codex sessions are checkpointed before termination | not_started |
| Codex clients reconnect through codex-lb and resume their goals | not_started |

## Progress

Done:

1. Cloned `Soju06/codex-lb` with GitHub CLI.
2. Created the isolated `codex/me-setup-repo` worktree.
3. Read repository instructions and the full Malaysian Repo Standards checklist.
4. Inventoried GitHub rulesets, CI, local toolchain, Hetzner resources, and live Herdr agents.

In progress:

1. Implement AC1–AC6 from `docs/skill-outputs/me-define-done/2026-07-10-codex-lb-hetzner.md`.

Next:

1. Complete repository-operability units and their evidence manifest.
2. Run fresh-context review and exact-SHA proof.
3. Publish the reviewed repository change if the upstream delivery substrate permits it.
4. Assemble the deploy decision packet from AC7–AC13.
5. Follow the Tokenmaxxing successor contract: deploy, configure Access/Tunnel,
   smoke, import accounts, and verify routing/retention.
6. Checkpoint and migrate all live Codex sessions.
7. Build and release the separately reviewed signed macOS client only after its
   Apple credential prerequisite is verified.

## Interruption state

- Active resources: one local git worktree; no deployed resources created.
- Cleanup: `git -C /code/codex-lb worktree remove .worktrees/me-setup-repo` only after the branch is no longer needed.
- Resume gate: reread this plan, verify branch/base and user authority, refresh `origin/main`, inspect Hetzner inventory, then continue at the first non-passing criterion.
- Stale-sensitive gate: rerun `gh api repos/Soju06/codex-lb/rulesets/11732824` and the Hetzner server inventory before external writes.

## Decision log

- Use a task worktree and keep the root checkout on `main`; rejected editing the root checkout because machine policy forbids it.
- Keep codex-lb private-by-default and expose no public host ports; Cloudflare
  Tunnel is the sole hostname ingress and Access is the identity boundary.
  Rejected copying the upstream Docker examples because they publish on all
  interfaces. This supersedes the earlier Tailscale Serve-only ingress choice.
- Do not terminate Prodex processes until the isolated Codex-LB deployment,
  persistence, Access boundary, and synthetic no-grant smoke checks pass. At
  cutover, checkpoint sessions and current grants, then stop/fence every old
  refresh writer before the first import. Perform a bounded one-account canary
  while the old writers remain stopped; promote all four or stop Codex-LB and
  restore the latest grants to exactly one Prodex writer. Rejected import-first
  smoke because it creates dual writers.
- Treat copied OAuth refresh grants as a migration input, not a durable dual-writer arrangement; rejected leaving Prodex and codex-lb refreshing the same grants indefinitely.
- Deploy `cpx21` in Ashburn; rejected `cpx22` in Europe because the user selected the closest available Ashburn option after live availability and price evidence was presented.
- Run the remaining work under the direct-human LFG lifecycle receipt; rejected treating setup, deployment, credential migration, and session resumption as disconnected handoffs.
- Keep the original Codex-LB dashboard and grant all `@onda.lol` users full
  administration through one Cloudflare Access layer offering Google or OTP;
  decision record: `docs/skill-outputs/me-grill/2026-07-11-tokenmaxxing.md`.

## Blockers

- Credential import remains fenced behind AC8–AC10. No blocker prevents local
  AC1–AC6 implementation. Employee macOS release is blocked until Apple
  Developer ID/notarization/update-signing credentials are verified.
- Deployment is additionally gated by the 2026-07-11 technical/security
  findings: Cloudflare Access JWT validation, exact whole-host WARP policy,
  thirty-day primary/backup expiry, and the corrected zero-writer cutover must
  be implemented and reviewed first.

## Validation

- `bin/test` or the documented equivalent after standards remediation
- `docker compose config --quiet`
- local runtime request plus log inspection and loopback binding proof
- deploy preflight, smoke, rollback rehearsal, and observation-band checks
- live Codex request through codex-lb before session migration
- Herdr agent/session inventory before and after cutover

## LFG shaping dispatch

Topology: fan-out-and-synthesize for approach risk, followed by a single fresh reviewer after implementation.

Why this topology: repository operability, OAuth migration, private infrastructure, and live-session continuity are separable high-risk lenses; independent read-only attacks are cheaper than discovering a coupled failure during cutover.

Caps: `retry_cap=2`, `max_iterations=7`.

| Stage | Lens | Mode | Scope | Output |
|---|---|---|---|---|
| shape | premortem | subagent | failure narratives, early warnings, mitigations, and cutover tripwires | structured risks returned to coordinator |
| shape | technical approach | subagent | challenge the smallest complete repo/deploy/migration design against live code | proceed/revise/block findings returned to coordinator |
| shape | security | subagent | OAuth grants, encryption, API access, Tailscale boundary, bootstrap SSH, secret handling | no-fire findings and required controls returned to coordinator |
| build | implementation | coordinator | worktree-local repository changes only | diff plus executable evidence |
| review | correctness/operability | fresh subagent | current diff and evidence, read-only | pass/fail delivery verdict |

Skipped heavier topology: no tournament or arena; the target and network boundary are already chosen, so competing designs would add cost without resolving an open product decision.

### 2026-07-11 successor dispatch plan

Topology: fan-out-and-synthesize shaping, coordinator-only writes, followed by
adversarial verification and a fresh delivery review.

Why: Cloudflare identity, OAuth single-writer migration, native WARP auth,
metadata retention, API-key authorization, and client signing are independent
high-risk boundaries. Read-only reviewers can attack them concurrently without
write collisions.

Caps: `retry_cap=2`, `max_iterations=7`.

| Stage | Topology | Scope signal | Lens | Run? | Reason / skip reason | Mode | Required context | Output |
|---|---|---|---|---|---|---|---|---|
| shape | fan-out-and-synthesize | ingress/auth/client architecture changed | technical approach | yes | Challenge deploy, auth, API, retention, and macOS seams before modification | subagent | successor contract, live repo, prior review | structured proceed/revise/block findings |
| shape | fan-out-and-synthesize | OAuth grants, domain-wide admin, keys, privacy | security | yes | Produce no-fire list and required controls | subagent | successor contract, auth/storage code, receipt | structured security findings |
| shape | skipped | product and visual direction | brainstorm/design UI | no | Grill froze the unchanged upstream dashboard and menu semantics | skipped | decision record | skip recorded |
| review | adversarial verification | built diff and external readiness | delivery reviewer | yes, after build | Doer cannot grade current SHA/diff | fresh subagent | contract, diff, evidence manifest | pass/fail/blocked verdict |

Only the coordinator may edit files, mutate providers, import credentials,
publish, land, deploy, promote, roll back, or retire this plan.
