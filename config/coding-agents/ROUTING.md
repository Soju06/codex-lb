# Canonical coding-agent routing

Single source of truth for model routing on this computer. Host-neutral path:
`~/.agents/policy/coding-agents/ROUTING.md`. Host instructions and skills are
adapters; when they disagree with this file, this file wins. The history this
file used to carry (seat lineups from 2026-07 through 2026-09) is in git.

## The rule (owner, 2026-09-19)

Fable is the binding pool — two of five Anthropic accounts are Fable-eligible
and the general pool never runs out. So Fable is rationed and nothing else is.

1. **Fable is the driver and the judge.** Nothing else runs on it: no forks
   unless the context is the deliverable, no catch-all subagent inheriting it.
2. **Opus 5 is the default seat, and it is not rationed.** Dispatch it freely.
   Opus traffic prefers accounts already past the Fable threshold, so Fable
   headroom elsewhere is preserved.
3. **Read-only exploration goes to the cheapest live seat.**
4. **Verification is cross-vendor**: the verifier never shares a vendor with
   the author of the diff.
5. **Every dispatch and closeout is recorded** (`~/.claude/logs/dispatch.jsonl`).
   Defaults change from recorded outcomes via `route learn`, never from opinion.
6. **A seat that is down is routed around** by `route doctor`; a pool that is
   low is reported before it is empty.

## The seats

Which seat serves which class, on which model, out of which pool, is
`config/coding-agents/routing-table.json` and nothing else — this file does not
restate it, because a second copy is a copy that goes stale.

Read it through the router rather than from memory: `route pick <class>
[--author-vendor V]` returns the first seat whose pool is live plus its
fallback chain, and `route pools` shows what is left in each pool. Classes:
`plan` (the driver keeps it), `review`, `explore`, `implement`, `mechanical`,
`verify`, `computer`.

`cursor-seat` and `codex-verifier` are thin forwarders: the work runs on
Cursor's and OpenAI's quotas, not on ours. Never a `claude-fable-*` model on
Cursor (no ZDR agreement).

## Enforcement

- `hooks/seat-guard.py` (PreToolUse on Agent) denies exactly two shapes: a
  `claude-fable-*` model pinned on a subagent, and a catch-all `subagent_type`
  with no model. Opus and everything else pass. `fork` passes, logged.
- `hooks/subagent-closeout.py` (SubagentStop) closes the ledger line.
- `hooks/routing-pulse.py` (UserPromptSubmit) fires when a session spends 40+
  Fable requests in an hour, or 25+ in six hours with too few closeouts.
- `install-policy.py` installs the seats, the hooks, the settings entries and
  the `com.aneyman.route-doctor` launchd job. Run `verify-routing` to check.

Changing the lineup means editing this file and the routing table, not
overriding either in a session.
