# Design

- Run required teardown in an owned asyncio task inside an anyio shield and tolerate repeated cancellation delivery until that task completes.
- Use the same cancellation-deferring primitive for source-chat upstream closure, API-key reservation release, and request-log persistence.
- Track whether a Responses terminal event was observed; only classify cancellation as `client_disconnected` when no terminal event has been observed.
