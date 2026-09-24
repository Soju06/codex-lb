## Implementation
- [x] Remove eager Astra payload snapshots and verify retained configuration.
- [x] Lazily instrument steering-sensitive aiohttp sends and safely reject unsupported dispatch tracking.
- [x] Read reservation items once after claiming settlement, preserving concurrency.

## Verification
- [x] Run route/transport and PostgreSQL concurrency regressions.
- [ ] Validate specs, independent review, and required local CI.
