# Execution Contract: codex-lb repository commissioning, private deployment, and session cutover

## Run context

| Field | Value |
|---|---|
| Context / receipt | `docs/skill-outputs/me-lfg/20260710-codex-lb-hetzner-context.md` |
| Session | autonomous |
| Status | running |
| Authority | receipt-scoped repo edit/commit/publication/merge/deploy/promotion/credential and access changes/smoke/observe/declared rollback |

## Source context

- Chosen direction: one dedicated Hetzner `cpx21` in `ash`, Ubuntu 24.04, Tailscale Serve HTTPS to loopback-only codex-lb, four migrated accounts, existing Codex sessions resumed without Prodex remaining a credential writer.
- Sources: current user request and target choice; setup inventory; technical approach review; active ExecPlan.
- Actor impact: the user receives a private OpenAI-compatible codex-lb endpoint and existing agent goals continue through it.
- Constraints: repository OpenSpec remains the behavioral source of truth; root checkout stays clean; no public steady-state ports; no secret output; one refresh writer; exact reviewed artifact; rollback must preserve current grants.
- Upstream artifact check: used `docs/skill-outputs/me-review-technical-approach/2026-07-10-codex-lb-hetzner.md`; no brainstorm, grill, UI, or use-case artifact was needed because target and actors are explicit.

## Outcome

The repository has a truthful, executable operability path; the reviewed revision is running privately on the named server; four accounts and a real Codex client work through it; every affected live goal is checkpointed and resumed; the deployment remains healthy through the observation window or is rolled back through the declared single-writer procedure.

## Scope

In scope:

- concise architecture/readiness documentation and generic setup, test, start/log, worktree, and operability-proof adapters;
- report-only ledger entries for standards that require upstream GitHub policy or a real production environment rather than unsafe local invention;
- one immutable single-node deployment with loopback Docker publishing and Tailscale Serve;
- API/dashboard authentication, persistent SQLite/key recovery pair, four account imports, a canary Codex configuration, session checkpoint/retag/resume, and 15-minute observation.

Out of scope:

- application feature changes, public ingress, Funnel, multi-replica/PostgreSQL architecture, CI/ruleset bypass, unrelated branch cleanup, customer communication, or restarting Prodex as an OAuth refresh writer.

Likely touched repository surfaces: `ARCHITECTURE.md`, `AGENTS.md`, `.github/CONTRIBUTING.md`, `.gitignore`, `Makefile`, `README.md`, `bin/`, `scripts/`, operational docs, and LFG artifacts. Product modules and OpenSpec behavior are read-only unless an executable guard proves a documentation claim requires correction.

## Acceptance criteria

| ID | Criterion | Depends on | Proof capability | Native surface / replay | State |
|---|---|---|---|---|---|
| AC1 | Every Malaysian Repo Standard has a current verdict, evidence, classification, and residual owner. | — | observe/assert/capture | standards report plus referenced live commands | not_started |
| AC2 | A fresh checkout/worktree has one idempotent setup command with actionable prerequisite failures and no secret creation. | — | prepare/execute/assert/reproduce | `bin/setup`; run twice; isolated missing-tool probe | not_started |
| AC3 | Architecture, agent routing, and worktree policy match the live repository and link canonical commands. | — | observe/assert/reproduce | doc guard and direct tree checks | not_started |
| AC4 | One aggregate proof command supports full and merge-base-aware diff modes without weakening native checks. | AC2 | execute/assert/reproduce | `bin/test` and `bin/test --diff <base>` plus deliberate invalid-argument probe | not_started |
| AC5 | One safe local start/log path is loopback-only, idempotent, checkout-local, and produces one aggregate log file. | AC2 | execute/observe/assert/cleanup | `bin/dev`, `bin/logs`, listener/health/log proof, stop cleanup | not_started |
| AC6 | Repository operability invariants are machine-enforced and fail on a deliberate fixture violation. | AC3, AC4, AC5 | execute/assert/reproduce | repo operability check, happy path and temporary failing fixture | not_started |
| AC7 | The exact reviewed commit and immutable image digest are recorded before deployment. | AC1–AC6 | capture/assert | git SHA, clean diff, image inspect/digest evidence, fresh review verdict | not_started |
| AC8 | The new host survives reboot with Tailscale SSH and Tailscale Serve access while public 22/2455/443 and all other application ports are closed. | AC7 | execute/observe/assert | provider firewall, listener, Tailscale status/serve, independent SSH, reboot, external negative probes | not_started |
| AC9 | Persistent data, Fernet key, API key, dashboard auth, and backup/restore boundaries pass permissions, restart, 401/200, and paired recovery proof without secret output. | AC8 | execute/observe/assert/cleanup | mode/owner checks, redacted container inspection, restart, auth probes, paired backup restore rehearsal | not_started |
| AC10 | All old refresh-capable Prodex/Codex writers are checkpointed and stopped before the first real account import. | AC9 | observe/assert/capture/resume | per-pane cutover ledger, process inventory, source auth digests/mtimes, stop proof | not_started |
| AC11 | Each of four accounts passes identity/quota/model and two consecutive authenticated request probes through codex-lb, including terminal streaming behavior. | AC10 | execute/observe/assert | authenticated account API plus per-account bounded probes and redacted logs | not_started |
| AC12 | A disposable Codex canary uses the Tailscale endpoint and fleet API key, then exact checkpointed sessions retag and resume their declared next action without reactivating an old writer. | AC11 | execute/observe/assert/resume | isolated config canary, retag dry-run/apply counts, Herdr/session ledger, through-proxy requests | not_started |
| AC13 | Fifteen minutes of platform, account, and session signals stay within bands, or the declared rollback restores a single authoritative writer and resumed goals. | AC12 | observe/assert/cleanup/resume | timestamped observation ledger or rollback evidence | not_started |

Only a fresh `me-review` grader may mark criteria `passing`.

## Evidence and observation bands

- Platform advance: healthy container, zero restarts/OOMs, disk below 80%, memory below 85%, no public listeners, Tailscale SSH/Serve reachable after reboot.
- Account advance: all four identities present, none `reauth_required`, quota/model query succeeds, two consecutive requests per account succeed, terminal stream events observed.
- Session advance: canary and every migrated pane resume the recorded session and perform the recorded next action; no Prodex credential writer exists.
- Hold: one clearly transient upstream failure within a predeclared single retry, with no auth-state mutation and stable platform signals.
- Rollback: any old writer active during import; source auth mutation during import; key/database mismatch; any auth failure after retry; service restart/OOM; public listener; canary mismatch; missing session checkpoint/resume path; or any resumed pane cannot perform its next action.
- Evidence records must name run/criterion, invocation, target and SHA/digest, result, artifact, limits, external effects, cleanup, and exact resume gate. Secret values are never evidence.

## Ordered units

1. Commissioning record and architecture/readiness truth.
2. Generic setup/worktree/proof adapters.
3. Safe dev/log and operability enforcement adapters.
4. Fresh independent review and immutable artifact build.
5. Infrastructure bootstrap, Tailscale enrollment, reboot proof, and public-firewall closure.
6. Secret boundary, authentication, persistence, and recovery proof without real grants.
7. Per-pane checkpoint ledger and zero-writer fence.
8. Sequential four-account import and repeated runtime proof.
9. Disposable client canary, scoped retag, session resume, observe or declared rollback.

The seams are intentionally cut at independently gradeable boundaries. Credential migration and session cutover remain one atomic outage unit because separating them would create either a dual writer or an uncheckpointed client fleet.

## Decisions

- Use Tailscale Serve to bridge HTTPS tailnet traffic to loopback; reject all-interface Compose publishing and public Cloudflare ingress.
- Use one SQLite instance on `cpx21`; reject PostgreSQL/multi-replica complexity for four accounts.
- Stop old writers before import; reject import-first smoke because token rotation makes dual writers unsafe.
- Resume raw Codex clients against codex-lb; reject restarting Prodex unless a proven external-provider mode cannot read or refresh profile grants.
- Rollback exports the latest grants from codex-lb before old-writer restoration; reject blindly restoring pre-cutover files.
- Build or transfer the immutable image before promotion; reject compiling under cutover pressure on the 4 GB host.

## Stop conditions

- Any secret appears in output, logs, git diff, cloud-init, labels, or a file broader than `0600`.
- Any public steady-state listener or provider firewall ingress remains before credential import.
- Any affected pane lacks a durable checkpoint and tested resume path.
- Any old refresh-capable writer remains at first import.
- Review/CI/OpenSpec required evidence is unresolved, deployment source is dirty, or artifact identity is mutable.
- The same criterion fails twice for the same reason.

## Rollback procedure contract

Before promotion, preserve the original client config and per-pane ledger with mode `0600`. On a declared tripwire: stop new clients; stop codex-lb before allowing another writer; if any refresh may have occurred, export the current matching account grants over loopback into a root-only transient directory and atomically restore them to their matching Prodex profiles; otherwise restore the untouched pre-cutover files; restore client config; restart only checkpointed sessions; prove one writer, account identity, and resumed next actions. Never run old and new writers concurrently during rollback.

## Residual risks

- Database and Fernet key on one host do not protect against root compromise.
- Tailscale account compromise remains a network-boundary risk, mitigated by application auth.
- Refresh rotation can make a fallback dependent on current export or reauthentication.
- Upstream GitHub ruleset/merge-queue changes may remain report-only if permissions or project policy do not authorize them.

## Next route

Implementation-ready: `me-build-and-prove`, then fresh `me-review`, publication/landing if applicable, and `me-land` deployment ladder.
