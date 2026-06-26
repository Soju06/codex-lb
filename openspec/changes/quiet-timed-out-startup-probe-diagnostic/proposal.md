# Quiet the timed-out startup-probe shielded-future diagnostic

## Problem

The streaming proxy probes the first upstream event before committing to a
streamed response (`_probe_stream_startup_error` and
`_probe_chat_stream_startup_error`). The probe raced the first item against a
short timeout using `asyncio.wait_for(asyncio.shield(first_task), timeout)`. When
the first item took longer than the probe window -- for example while the
upstream was still blocked on the response-create admission gate -- `wait_for`
cancelled the shield's outer future. If `first_task` then finished with a
`ProxyResponseError` (such as a 429 from the admission gate), Python 3.14's
`asyncio.shield` reported the inner exception through the loop exception handler
as `ProxyResponseError exception in shielded future`. This logged a spurious
ERROR even though the same error was already delivered to the caller through the
streamed response.

## Solution

Race the probe task with `asyncio.wait({first_task}, timeout=...)` instead of
`wait_for` + `shield`. `asyncio.wait` never cancels the task on timeout and does
not wrap it in a shield, so the timed-out task is simply handed to the streamed
response for consumption without emitting the shielded-future diagnostic. A
done-callback retrieves the task's result if the wrapping stream is dropped
before the task is awaited (the request is torn down mid-probe), so an abandoned
task does not trip the "exception was never retrieved" warning either. Consumers
that await the task still observe the upstream error unchanged.

## Changes

- Replace `wait_for` + `shield` with `asyncio.wait` in both startup probes
- Retrieve the probe task's exception via a done-callback for the abandoned case
- Cancel the still-running probe task when the wrapping stream is closed early
- Add regression coverage asserting no shielded-future diagnostic on the
  timeout-then-upstream-error path

## Out of scope

- Changing the probe timeout windows
- Changing how startup errors are classified or surfaced to clients
- Changing the admission gate behavior itself
