## 1. Transport policy

- [x] 1.1 Add a Responses payload helper that detects the built-in `image_generation` tool.
- [x] 1.2 Update auto upstream transport selection to prefer HTTP when that helper matches, while preserving explicit transport overrides.

## 2. Verification

- [x] 2.1 Add regression coverage for transport resolution and stream path selection with `image_generation`.
- [x] 2.2 Run targeted pytest, ruff, and `openspec validate --specs`.
