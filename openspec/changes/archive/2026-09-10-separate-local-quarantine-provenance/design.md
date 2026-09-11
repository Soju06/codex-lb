## Context

Completion loads durable poison before capturing its cleanup fence. A concurrent local failure during that load becomes part of the captured entry, so cleanup wrongly treats it as disproved. An early fence alone changes durable-only cleanup timing.

## Decisions

Capture a service-lifetime local-failure cutoff before the pending lock. First strikes also advance the sequence. Durable adoption advances entry identity without changing local failure provenance; local poison retains its own deadline. Successful cleanup removes the adopted contribution while preserving newer local evidence and existing suppressed weaker evidence.

Pass the cutoff explicitly through completion loads and their revocations. Keep the existing late durable fence and failed-load recapture. Verified replay carries an origin cutoff; same-key completion loads use that older authority too. Outside completion, durable disproof keeps its existing behavior.

Fence primary cleanup by the canonical session or its weak owner reference. A planning-only durable probe owns no session reference. The sequence survives entry eviction, preventing a recreated entry from matching an old fence. Revocation clears local poison metadata so a later arm cannot reuse its disproved deadline.

## Scope and verification

The existing poison-preserving soft cap, TTLs and unknown-key behavior remain unchanged. This resolves the cleanup concern without choosing PR2276's broader overflow policy. The quarantine module remains one cohesive responsibility; callers pass only the cleanup authorities it needs.

Public-route regressions cover pre-lock/pre-load/registration interleavings, retry and disproof, exact deadlines/counts, failed settlement/registration, replacement identity and replay-origin propagation. Transport and race placement are controlled; the same-key completion case seeds admitted replay metadata. These prove deterministic behavior, not production incidence.
