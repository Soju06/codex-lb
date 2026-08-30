## 1. Regression Coverage

- [x] 1.1 Add shared policy tests for explicit `priority`/`fast`, non-priority preservation, disabled policy, alias-derived priority, API-key enforced priority precedence, and strip logging; run them against the current implementation and record the expected failures.
- [x] 1.2 Add native `/responses`, `/v1/responses`, native compact, `/v1` compact, and chat-conversion path tests proving explicit priority is absent from the effective upstream payload; run them red before implementation.
- [x] 1.3 Add a WebSocket `response.create` regression test proving explicit priority is absent from the upstream payload and effective request state; run it red before implementation.
- [x] 1.4 Add warmup characterization and owner-forward preparation regression coverage for the shared prohibition boundary. The owner-forward case ran red; warmup remained green because its input surface only derives priority from a Fast alias, which the previous alias guard already handled.

## 2. Policy Implementation

- [x] 2.1 Add the shared canonical priority-tier prohibition helper with request-ID/stripped-value info logging.
- [x] 2.2 Refactor alias normalization and API-key enforcement so all tier writers resolve before the shared prohibition, preserving enforcement provenance and disabled-policy behavior.
- [x] 2.3 Apply the shared helper after warmup alias normalization and after owner-forward tier restoration, before routing, reservation, request-state capture, or serialization.
- [x] 2.4 Enumerate every application write/normalization site for `service_tier` and confirm it is covered or document why it is not an outbound request writer.

## 3. Verification and Documentation

- [x] 3.1 Run the focused proxy, WebSocket, compact, chat, and warmup tests plus lint/type checks for changed files.
- [x] 3.2 Sync the delta requirements and stable rationale/example into the owning main OpenSpec capability documents, then run strict OpenSpec validation. The change and `fast-mode-policy` capability validate strictly; repository-wide spec validation remains blocked by pre-existing failures recorded in `notes.md`.
- [x] 3.3 Run the repository's proportionate final local gate and integration targets that exercise changed proxy paths.
- [x] 3.4 Run CodeRabbit review, read every finding, resolve all critical/major findings and any impactful lower-severity findings, and document deliberate deferrals.
- [x] 3.5 Verify the OpenSpec change against implementation and tests, then archive it only after verification is clean.
