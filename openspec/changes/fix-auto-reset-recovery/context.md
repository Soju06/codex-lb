# Automatic reset recovery deployment

The tested repair was activated on the Mac Studio on 2026-09-09. The running
internal copy contains migration `20260909_120000_add_reset_credit_attempts`
and the `reset_credit_attempts` journal. Runtime health alone does not establish
that a source change was deployed.

## Required deployment checks while launchd source access fails

The configured launchd wrapper invokes `~/.agent-lb/bin/sync-runtime.sh`, then
starts the internal runtime. That helper explicitly supports manual invocation.
It also returns success after a failed source sync so the previous runtime can
remain available. Therefore neither its exit code nor a successful kickstart
proves that source files reached the runtime.

On 2026-09-09, interactive invocation of that exact helper succeeded. Invocation
from launchd failed opening the source with `Operation not permitted`, including
after the repair deployment. The deployment succeeded because the interactive
sync had already supplied the approved runtime files. Automatic source access
remains unresolved.

Until automatic source reads are independently verified, each deployment must:

1. Coordinate the service window and source ownership. Preserve a current
   nonrotating database backup and compare-before-write source/runtime rollback
   checkpoints. Review the helper's dry run, including unrelated changes and
   deletions, against an explicit candidate manifest.
2. Invoke the configured sync helper from the authorized interactive context.
   Check its new log entries and every runtime candidate hash. Stop if the
   helper cannot read the named source or any expected hash differs. Do not
   substitute a broad manual tree copy or another runtime.
3. Use the existing coordinated drain protocol and observe zero in-flight
   requests before the normal `launchctl kickstart -k
   gui/501/com.aneyman.agent-lb`. Bound the drain wait and release any drain state
   owned by the operation if it cannot proceed. Do not restart solely to prove
   that permissions changed.
4. Verify the new process, candidate hashes, health and readiness on both local
   listeners, migration revision and journal metadata through configured
   helpers. Do not redeem a real reset credit as a smoke test.

## Rollback constraints

Preserve pending and applied journal attempts. Old code cannot honor that
journal, so stop automatic credit consumption before returning to old code and
keep recovery code while an uncertain attempt remains unresolved. Restore only
reviewed owned changes with compare-before-write checks. Preserve unrelated
dirty work. Do not overwrite newer requests by restoring a whole database just
to undo this additive migration.

## Access investigation

The launchd job runs `/bin/bash`; macOS TCC attributed the denied access to
`/bin/bash`, with `/usr/bin/rsync` as the accessing process. No permission setting
was changed during activation. Full Disk Access for a general shell is broader
than this source directory. Any permanent permission or launcher change needs a
scoped review, a rollback path and current evidence of the intended access.
Never modify TCC databases to bypass consent.
