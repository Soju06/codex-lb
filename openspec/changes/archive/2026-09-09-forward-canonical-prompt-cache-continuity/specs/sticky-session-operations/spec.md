## ADDED Requirements

### Requirement: Canonical prompt-cache bridges preserve hard replica continuity

When durable lookup resolves an incoming turn-state or previous-response
reference to a live bridge whose canonical key is `prompt_cache`, the origin
replica MUST treat that request as hard bridge continuity for replica-owner
routing. If the live owner is another reachable replica, the origin MUST use
the authenticated internal owner-forward transport and MUST NOT attempt a soft
local prompt-cache rebind. Preserving the canonical prompt-cache key MUST NOT
weaken the hard continuation evidence or expose `bridge_instance_mismatch` for
an ordinary cross-replica continuation. A request carrying only prompt-cache
locality and no hard continuation evidence MUST retain the existing soft local
rebind behavior. Explicit recovery paths that have already established that
owner forwarding is unavailable MAY retain their bounded local-rebind
behavior.

#### Scenario: Turn-state continuation forwards to the canonical prompt-cache owner

- **GIVEN** a turn-state alias resolves to a live bridge canonically keyed by prompt cache on replica A
- **WHEN** the continuation arrives on replica B
- **THEN** replica B forwards the request internally to replica A
- **AND** it does not attempt to claim the canonical bridge locally
- **AND** replica B leaves no local inflight creation reservation for the forwarded bridge key

#### Scenario: Previous-response continuation forwards to the canonical prompt-cache owner

- **GIVEN** a previous-response reference resolves to a live bridge canonically keyed by prompt cache on replica A
- **WHEN** the continuation arrives on replica B
- **THEN** replica B forwards the request internally to replica A
- **AND** the client does not receive `bridge_instance_mismatch`

#### Scenario: Prompt-cache-only locality remains soft

- **GIVEN** a request has prompt-cache locality but no turn-state, previous-response, or other hard continuity evidence
- **WHEN** its locality owner is another replica
- **THEN** the receiving replica may use the existing soft local-rebind path
