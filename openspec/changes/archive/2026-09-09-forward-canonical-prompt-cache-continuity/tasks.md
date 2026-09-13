# Tasks

## 1. Contract

- [x] 1.1 Specify that turn-state and previous-response evidence remains hard when the durable canonical bridge key is prompt-cache based.
- [x] 1.2 Preserve prompt-cache-only soft locality and explicit recovery-rebind behavior.

## 2. Implementation

- [x] 2.1 Route a live remote canonical prompt-cache owner through the internal owner-forward path when hard continuation evidence is present.

## 3. Regression Coverage

- [x] 3.1 Add a focused service test for the retained canonical prompt-cache key.
- [x] 3.2 Add `/v1/responses` coverage matching the two-replica failure shape.

## 4. Validation

- [x] 4.1 Run focused unit and integration tests.
- [x] 4.2 Run Ruff, type checks, and strict OpenSpec validation.
- [x] 4.3 Verify the fix against two live replicas and restore the shared deployment to one replica.
