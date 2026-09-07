## 1. HAProxy Compose topology

- [x] 1.1 Add the opt-in blue/green Compose file and HAProxy configuration; verify rendered Compose exposes only HAProxy on port 2455 and `haproxy -c` accepts the configuration.
- [x] 1.2 Configure stable per-slot bridge identities, shared application state, strict readiness checks, restart-safe HAProxy server state, and bounded container stop grace; verify structural tests cover every multi-replica invariant.

## 2. Deployment workflow

- [x] 2.1 Implement the serialized deployment/status/rollback script with PostgreSQL and leader-election preflight checks; verify command tests reject unsafe configuration before container mutation.
- [x] 2.2 Implement candidate build/start/readiness, gap-free runtime cutover, public verification, predecessor drain, state persistence, and failure rollback; verify fake-Docker workflow tests cover success and each fail-closed stage.
- [x] 2.3 Run an isolated live HAProxy smoke deployment and verify HTTP readiness plus WebSocket-capable configuration while alternating blue and green.
- [x] 2.4 Activate a statically zero-weight candidate with a positive absolute
  runtime weight before draining the predecessor; add a regression assertion
  that rejects percentage-based activation from the zero baseline.

## 3. Documentation and specification

- [x] 3.1 Document bootstrap, normal deploy, status, rollback window, PostgreSQL/encryption-key prerequisites, OAuth callback handling, migration compatibility, and bounded availability claims; verify docs link to the owning OpenSpec capabilities.
- [x] 3.2 Sync normative requirements and operational context into the main deployment and replica specs; verify both capabilities and the change pass strict OpenSpec validation.

## 4. Final validation

- [x] 4.1 Run deployment artifact tests, relevant existing Compose/launcher tests, formatting checks, and `git diff --check`; resolve all change-related failures.

## 5. Repeatable agent deployment

- [x] 5.1 Add an implicitly discoverable repository skill and AGENTS workflow rule that route later production Compose deployment requests through the HA script, require confirmation for first bootstrap, and verify the completed rollout; validate the skill and update operator documentation.
