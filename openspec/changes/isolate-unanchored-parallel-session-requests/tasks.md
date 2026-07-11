# Tasks

- [x] 1. Detect unanchored session-header collisions during bridge lookup.
- [x] 2. Create a server request-scoped bridge lane without replacing the canonical session.
- [x] 3. Reserve canonical sessions across lookup-to-submit visibility and clear reservations atomically.
- [x] 4. Preserve hard owner and account continuity for fork-derived durable aliases.
- [x] 5. Cover duplicate client request IDs, reservation races, and model metadata isolation.
- [x] 6. Preserve unanchored status with a v2-bound forwarding signature and fail closed across mixed versions.
- [x] 7. Own the complete pre-submit reservation handoff through every cancellation and early error.
- [x] 8. Run focused, full-suite, lint, type, and strict OpenSpec validation.
- [x] 9. Preserve legacy primary HMAC compatibility for explicitly anchored forwards during rolling upgrades.
- [x] 10. Keep owner-side forks local, normalize blank anchors, preserve forwarded reservations, and reject ambiguous legacy fields.
- [x] 11. Bind v2 client-IP presence in the primary HMAC and cover removal, blanking, mutation, and no-IP paths.
- [x] 12. Release request-scope reservations on pre-submit failures, keep prompt-cache forwards legacy-compatible, and roll back locally fenced aliases.
