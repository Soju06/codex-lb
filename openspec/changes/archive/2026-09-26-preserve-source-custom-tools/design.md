## Context

See proposal.md for the observed Codex failure. Source capability filtering is shared by the backend and public Responses routes; hosted capabilities remain opt-in. The catalog already carries code-mode and freeform patch declarations, but the source capability resolver ignores them.

## Goals / Non-Goals

Restore the custom execution tools required by declared Codex capabilities. Keep plain source defaults and hosted-tool filtering intact; do not enable all custom tools for every source or alter tool schemas, replay identity, or transport selection.

## Decisions

Derive `custom` support in `source_model_supported_tool_types` from exact `tool_mode == "code_mode_only"` or `apply_patch_tool_type == "freeform"` declarations. Reuse the existing filter and choice handling. This avoids redundant persisted flags and keeps other consumers of the capability resolver consistent. An explicit `experimental_supported_tools` opt-in remains supported for other models.

Route tests cover both routes and trailing slashes, grammar retention, direct and allowed tool choices, custom tool-call SSE responses, and the undeclared-source negative case.

## Risks / Trade-offs

Incorrect operator capability declarations can cause upstream rejection; they already select the client's tool protocol. Exact string matches prevent malformed or unrelated metadata from enabling the capability. Sources without the declarations retain existing behavior.

## Migration Plan

No schema migration is needed. For the running deployment, add `custom` to the affected models' existing `experimental_supported_tools` through the model-source service, preserving all other fields. Verify using a disposable Codex CLI workspace. The code fix takes effect on the next authorized deployment; this task does not deploy the unrelated working-tree changes.
