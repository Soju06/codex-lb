## Decision

Use an asyncio timeout around the existing refresh await, with a fixed five-second budget. The shared updater keeps ownership of its singleflight work and database sessions; the request does not create or cancel shared refresh tasks directly. Read the latest persisted rows after timeout and reject stale or incomplete observations through the existing projector. Preserve external cancellation.
