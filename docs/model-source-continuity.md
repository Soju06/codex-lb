# Model-source continuation

Responses requests for the same public model can use equivalent authorized sources while fresh and free of provider-owned references. Continuations stay on the source credential that produced their response or tool references. The proxy records that ownership before delivery so it can be checked across replicas.

For example, `previous_response_id: "resp-example"` produced by source A cannot move to source B after A is disabled. The request fails before dispatch; start a fresh conversation after correcting configuration. Resubmitting the same API key preserves ownership, while replacing the credential invalidates old references.

Reference history remains after active entries expire. This prevents stale IDs from silently changing owners. Source errors release or settle each attempt before another eligible source is tried. Requests containing account-owned files retain subscription routing requirements.

Requirements and storage trade-offs: [model-source-routing](../openspec/specs/model-source-routing/spec.md) and [context](../openspec/specs/model-source-routing/context.md).
