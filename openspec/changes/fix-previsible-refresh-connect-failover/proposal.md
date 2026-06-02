# Fix pre-visible refresh/connect failover

## Why

PR #822 tightens the compact retry path after the selected account cannot be
refreshed or connected before any compact output exists. Ordinary pre-visible
auth and transport failures can move to another eligible account, but a compact
request that references `input_file.file_id` can be account-bound by the upload
pin. Retrying that request on a different account would lose the file-owner
contract and can turn a real upstream availability problem into an incorrect
cross-account replay.

## What Changes

- Document that pre-visible refresh/connect failover remains allowed only when
  the request is replayable on another eligible account.
- Document that compact requests with live file-id routing pins fail closed on
  refresh/connect unavailability instead of excluding the pinned account and
  selecting a different account.
- Keep the existing repeated auth-401 failover contract for replayable compact,
  websocket connect, and HTTP bridge handshake paths.

## Issue Trace

- Refs #822

## Impact

- **Spec**: `responses-api-compat`
- **Behavior**: compact requests with pinned upload files preserve file-owner
  routing during pre-visible refresh/connect failures.
