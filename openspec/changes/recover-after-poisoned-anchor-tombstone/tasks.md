## 1. Recovery Contract

- [x] 1.1 Add regression coverage proving the submit-time tombstone refusal returns the existing 404, retires the exact proxy-injected anchor, preserves the tombstone, and leaves client-supplied or successor anchors untouched; verify the focused test fails before implementation and passes after it

## 2. Bridge Implementation

- [x] 2.1 Reuse the fenced exact-match anchor invalidation path from the submit-time tombstone refusal before raising the existing error; verify targeted bridge tests pass

## 3. Validation

- [x] 3.1 Run strict OpenSpec validation and the proportionate proxy bridge test gate, then review the final diff for scope and contract alignment
