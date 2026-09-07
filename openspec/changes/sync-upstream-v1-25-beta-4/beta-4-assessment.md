# Beta.4 reassessment during beta.3 integration

## Candidate and evidence

Upstream released [v1.25.0-beta.4](https://github.com/Soju06/codex-lb/releases/tag/v1.25.0-beta.4) on 2026-09-07 at 15:51 UTC. The annotated tag peels to `15ccd901bf013daa11c67934cc019a9b33f2f72b`. Its [CI Required check](https://github.com/Soju06/codex-lb/actions/runs/34140385173/job/101803394884) subsequently completed successfully; Docker image publication was still running at the 16:03 UTC check. Upstream CI does not validate this fork's integration.

Compared with the selected beta.3 commit, beta.4 has four commits and changes 48 files (+7,179 / -121 lines):

| Commit | Change | Additional fork validation |
| --- | --- | --- |
| `7410ebe7` | Reject direct task awaits that can defer cancellation | Run the updated cancellation-safety gate against retained fork task cleanup |
| `dafc1a21` | Retry accepted, output-free capacity failures with one visible response lifecycle | Verify response identity, no duplicate created/terminal events, account/file ownership, reservation settlement, partial output and cancellation on both bridge and websocket paths |
| `a8339c8f` | Permit credential-bearing HTTP/SOCKS proxies with a dashboard/log warning | Verify connector-only proxy credentials, encrypted upstream target enforcement, warning/API contract and no credential disclosure |
| `15ccd901` | Release version bump | Verify version metadata and frozen builds |

At assessment time, the beta.3 merge was uncommitted with `MERGE_HEAD=b9f598cec9b2cf14b4351173764b051ad3ff553b`. A beta.4 merge preview adds a websocket import conflict to the three beta.3 textual conflicts. The shared failure-metadata helper fix must survive the retarget; it prevents a circular import introduced by combining fork diagnostics with upstream bridge imports.

## Production compatibility, read-only check

A SELECT-only transaction through the blue backend found 101 active `http` endpoints, none with a configured username or encrypted password, with strict upstream routing enabled and 918 active account bindings. Only aggregated counts and credential-presence booleans were selected; no endpoint addresses, usernames, passwords or encrypted credential values were read or printed.

Therefore the beta.3 plaintext-credential rejection does not block the currently stored endpoints. Beta.4's policy relaxation is not an emergency compatibility requirement for this deployment. If credential-bearing HTTP/SOCKS endpoints are added later, beta.4 permits their proxy-hop credentials to travel unencrypted; the warning does not encrypt that hop. An HTTPS proxy or credential-free IP allowlist avoids that exposure.

## Recommendation and target decision

With upstream required CI green, beta.4 is the approved next candidate, subject to the extra retry and proxy-policy validation above. The operator confirmed the retarget with "ok" after the beta.4 recommendation. Do not treat the completed beta.3 checks as beta.4 verification.

Preserve the existing fork and integration fixes, update the pinned target and OpenSpec tasks before further implementation, integrate the additional upstream-owned deltas, then rerun affected tests and the integrated verification gates. The existing mixed-version `abandoned` writer/rollback limitation applies to either release and remains an explicit production rollout prerequisite. Neither this assessment nor local integration authorizes commit, push, PR publication or production deployment.
