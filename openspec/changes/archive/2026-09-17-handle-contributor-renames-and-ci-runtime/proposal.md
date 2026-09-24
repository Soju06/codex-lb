# Handle contributor renames and CI runtime variation

## Why

An existing contributor renamed their GitHub account from `Lotfree618` to
`felixcake618` without changing user ID `91266981`. The coverage check compares
current API logins with old logins embedded in commit emails, so a rename can
make an unchanged repository fail attribution. Adding the same person twice
would hide the identity mismatch.

In CI run `35096552634`, integration-core shard 3 reported 1,021 passing tests
in 1,156.30 seconds, then reached the 20-minute job deadline during shutdown.
The same selection previously passed in 612.19 seconds. The job budget must
include setup and cleanup while retaining the existing individual-test limits.

## What Changes

- Resolve numeric GitHub noreply author IDs using the current repository and
  PR author identities already returned by GitHub. Retain checks for unknown
  IDs and legacy login-only addresses; API failures must still fail closed.
- Update the existing renamed contributor entry and regenerate the README.
  Include this PR author's contributor registration, moving it out of #2440.
- Give integration-core shard jobs 30 minutes, including setup and cleanup.
  Keep their three-way partition, required aggregate, 180-second test timeout,
  and 300-second diagnostic watchdog unchanged.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `github-automation`: contributor identity comparisons survive account
  renames, and integration-core jobs allow setup and cleanup headroom.

## Impact

- `.github/scripts/check_all_contributors.py` and its unit tests.
- `.all-contributorsrc`, generated README contributor block, and `ci.yml`.
- No application behavior, test exclusions, deployment, or branch-rule changes.
- This change follows #2446's existing Rust security update; #2440 follows it.
