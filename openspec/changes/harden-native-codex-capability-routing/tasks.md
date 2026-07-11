# Tasks

- [x] Track model and service-tier support per refreshed account catalog.
- [x] Route only to accounts whose authoritative catalog supports the request.
- [x] Drop stale capability entries for paused/inactive accounts and publish an
      authoritative empty snapshot when the active pool is empty.
- [x] Normalize the selected installation identity across canonical Codex
      metadata carriers.
- [x] Revalidate direct WebSocket account/model/tier/owner eligibility before
      every unsent `response.create` frame.
- [x] Preserve safe fresh-turn and verified full-resend reconnect while failing
      closed for hard continuity that lacks replayable state.
- [x] Add focused regression coverage for pause-after-open, model/tier changes,
      safe reconnect, hard continuity, and installation metadata.
- [x] Run focused and broad Python test suites.
- [x] Run strict OpenSpec validation for the change and all main specs.
- [x] Deploy only after backup and verify the live native catalog and requests.
