# Verification

The catalog capability check is the only application behavior change. Existing
Responses filtering retains the original namespace definitions and matching
choices when the source supports them.

## Local checks

- Four new catalog cases failed before the fix; failing route cases also
  demonstrated dropped namespace tools on both Responses endpoints.
- Catalog and collaboration route suites: 88 passed. The 48 route cases cover
  v1/v2, explicit namespace opt-in, both paths and trailing slashes, three tool
  choice shapes, complete nested schemas, and conservative defaults.
- Existing source tool/search/replay cases: 6 passed.
- `make lint`, full `uv run ty check` and the strict docs build passed.
- Strict change validation and all 67 main specifications passed.
- Independent source and contract review found no actionable issues.

## Live acceptance test

Codex CLI 0.157.1 ran against a temporary local instance of this branch with a
Responses-capable Model Source forwarding to an existing Codex-compatible
backend. The source declared `multi_agent_version=v2` with an empty
`experimental_supported_tools` list. The client loaded the temporary source's
catalog and used HTTP Responses. The installed service and saved client
configuration were unchanged.

All four observed source requests retained the `collaboration` namespace and
returned HTTP 200. A real child returned `CHILD_2499_OK`; its parent returned
`PARENT_2499_OK`. Persisted session records confirmed the parent-child link and
that both used the same model and provider. The test used the client's actual
reserved collaboration schema. Temporary listeners were stopped afterward.

An initial run without the source's pinned catalog used generic function tools.
It was excluded from namespace acceptance evidence even though it spawned a
child. The successful run above exercised namespace declarations explicitly.

Maintainer agreement on the contract remains pending. The full local `make ci`
gate was not run. This active change is not ready for archival.
