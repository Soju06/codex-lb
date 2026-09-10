# Incident and verification

On 2026-09-10, a production account was paused at 11:42:14 UTC but new
WebSocket turns continued on its existing connection after 11:45. Connection
selection had happened before the pause. The ordinary reuse path retained a
detached ACTIVE account and did not consult the routing availability snapshot.
This is distinct from response completion after pause for already accepted work.

The patch checks the existing shared availability snapshot before reuse and
after admission waits. Idle sockets re-enter existing owner-aware selection;
pending siblings remain accepted and only the new unsent turn is rejected.
No health counter, database schema, configuration, or retry policy changes.
Stable behavior and operational context are synced into responses-api-compat.

## Verification evidence

- 11 dedicated integration cases passed across both direct WebSocket routes:
  paused/reauthentication/deactivated/deleted account snapshots, a real dashboard
  pause followed by a response-owned turn, a still-streaming sibling, and a pause
  during create-lease admission. Late rejection releases the reservation,
  admission, lease, and gate; the next movable request completes on B.
- 16 model-source guard tests passed, including account-scoped file coverage.
- 179 existing WebSocket response and shared-cache invalidation tests passed.
  Together with the focused cases, 206 tests passed. Two additional route tests
  verify cancellation during local rejection logging and terminal delivery:
  scope cleanup retains the unsent request after it leaves the pending queue,
  so cancellation cannot orphan its admission or create lease (208 total).
  The suites reported a
  Starlette deprecation and an aiosqlite event-loop teardown warning; neither
  produced a test failure, and the teardown warning also occurred in the
  existing response suite.
- Focused Ruff lint/format, type checking of the modified mixin, proxy
  architecture, cancellation safety, timing seam, and settings tier checks passed.
- The change passes strict OpenSpec validation. All 65 main specs were checked:
  49 pass and 16 fail on pre-existing issues. Running the same validation against
  HEAD in an isolated temporary directory produces identical errors/warnings;
  this change adds none. Unrelated normative wording is left untouched.

## Operational mitigation

Repeated overload/server-error responses clustered on individual accounts.
Three failing accounts were paused at 11:40:36–37 UTC; two also had burn_first
priority removed. An additional burn_first account produced 14 overload errors
in 27 requests after 11:40, and was paused with normal routing policy at
11:57:30 UTC. Changes use AccountsService, publish shared invalidations, and
record audit entries. Previously operator-paused accounts remain paused.

Another normal-priority account produced four consecutive overload responses
between 12:08:00 and 12:09:47 UTC and was paused through the same audited service
path at 12:10:41 UTC. This follow-up is evidence that upstream overload can still
occur independently of the corrected pause/reuse bug.

A further burn_first account returned four overloads in its last eight requests
between 12:17 and 12:19 UTC. It was paused and changed to normal routing policy
at 12:20:32 UTC using the audited service path. Mitigation remains scoped to
observed failing accounts; no blanket pool or model-policy changes were applied.

The patched image is deployed from the working tree through the existing HA
surge workflow. Deployment and post-rollout observations are recorded below
after completion; validation of the patch does not establish zero upstream errors.

The first HA rollout completed at 12:12:51 UTC with all three base backends
healthy and surge stopped. All backends had the pause dispatch guard by
12:06:50 UTC. The reported account's last completion was 12:06:34 UTC, before
its old backend was replaced; no new request-log starts on paused accounts
were observed from 12:06:50 onward in the initial checks. The cleanup-ownership
addition was then built for a second HA rollout after 31 relevant tests passed
again. The script owns both rollouts and retains the default 300-second drains.

At 12:29:55 UTC, all three base backends were healthy on the final image
`sha256:b841c84ee2bcce1090c34577d8827fe8769a964e7ebd387cb6e6bd9a7fb59aaf`.
Their modified mixin hashes all matched the working tree
(`366518ab13bfc3e677bfcbd3095c91514305b1b30926e1cc514ca91ec1f9c1f2`).
No container restart or OOM event was recorded. Surge retirement was still in
progress. No new log starts through the five initially tracked paused accounts
were recorded after 12:06:50 UTC. From 12:20:34 to 12:29:55 there were 265
successes, 3 overload errors, 7 upstream-unavailable errors, 6 owner-unavailable
errors, 5 usage-limit errors, and 6 client disconnects. These observations show
the pause guard holding while other upstream/continuity failures remain.

The second HA command completed successfully by 12:35:21 UTC. Final status:
blue, green, and amber UP at weight 1; exactly three eligible backends; surge
stopped at weight 0; no rollout phase. Public readiness returned HTTP 200 with
database OK and the steady three-instance bridge ring. The image was built from
the dirty working tree; no commit or push was made.

At 12:35:33 UTC, the reported account remained paused/normal with last completion
12:06:34.392939 UTC. There were no new recorded request starts through it since
12:06:50, and none through any currently paused account since 12:20:34. Since
all base backends were on the final image (12:29:45 cutoff), 129 requests
succeeded, 8 were client disconnects, 1 failed on an unavailable response owner,
2 returned upstream_unavailable, and 1 reached a usage limit. No overload or
server_error was recorded in this final-image window. This is bounded runtime
evidence, not a guarantee that upstream errors cannot recur.

The explicit post-command window from 12:35:21 to 12:36:00 UTC contained 15
successful completed requests and one previous_response_owner_unavailable
failure, with no new recorded request starts through paused accounts. All
implementation, regression, deployment, and monitoring tasks are verified.
