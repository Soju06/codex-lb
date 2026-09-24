## Why

Account-neutral full-resend certification currently rejects a safe shape where
the stored prefix already contains the pending tool call and the fresh suffix
only supplies its matching output. That can force owner-bound recovery even
when the resent input is otherwise self-contained and exactly settles the
durable pending-tool manifest.

## What Changes

- Allow a suffix made only of outputs that exactly settle pending direct tool
  calls already present in the verified stored prefix.
- Keep replay certification fail-closed for orphan outputs, duplicate output
  call IDs, missing call IDs, response-owned output fields, or suffix tool
  calls when the pending calls live in the prefix.
- Preserve the existing exact-manifest behavior for fresh suffixes that contain
  both the direct tool calls and their outputs.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: account-neutral replay certification can prove
  prefix-settled direct tool calls without weakening malformed-output checks.
