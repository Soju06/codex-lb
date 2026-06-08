- [x] 1. Update Responses compatibility spec to preserve backend Codex `image_generation` tools.
- [x] 2. Add failing regression coverage for backend Codex request normalization preserving `image_generation`.
- [x] 3. Implement the minimal request-policy change.
- [x] 4. Verify focused tests and live codex-lb image-generation surfaces.
- [x] 5. Attempt OpenSpec validation.
  - `openspec validate --specs`, `uv run openspec validate --specs`,
    `uvx openspec validate --specs`, and `npx openspec validate --specs`
    were attempted from this checkout, but the OpenSpec CLI is unavailable in
    the local environment/package registries. See `verify-report.md`.
