# Unsafe partial HTTP bridge replay

## Why

The HTTP Responses bridge persists complete terminal turns, but a websocket can
close after upstream has emitted part of the current response. The existing
recovery path intentionally refuses to replay that request because it cannot
prove whether model output, billing, or a tool side effect already happened.
That leaves long-running client sessions unable to continue even when the
completed transcript needed to start a new response is durable.

## What changes

- Add an explicit, opt-in setting for unsafe partial-turn replay. It defaults to
  `False` and does not change the existing safe recovery behavior.
- When enabled, after an abnormal transport close the bridge may rebuild one
  account-neutral `response.create` from the last durable completed turns and
  the interrupted request input, dropping the stale upstream anchor.
- Permit the replay only when no function/tool call or other side-effect
  boundary is ambiguous, the bounded transcript validates, and the durable
  operation can be atomically rebound as a one-shot attempt.
- Fail closed when the transcript is missing, malformed, oversized, contains
  unfinished tool work, or the durable rebind/fence cannot be written.
- Consume the authorization after one send; subsequent disconnects use the
  existing recovery rules and never loop indefinitely.
- Emit distinct telemetry so operators can measure unsafe replays and their
  rejection reasons.

## Impact

This is an at-least-once model-output strategy. A regenerated response can
repeat already-visible text, and the model may not resume at the same token
boundary. The setting is therefore disabled by default and must be enabled
explicitly by an operator. Tool-call and side-effect ambiguity remains
fail-closed. Normal streaming, request payloads, and the existing complete
transcript recovery flag are unchanged when the new setting is disabled.
