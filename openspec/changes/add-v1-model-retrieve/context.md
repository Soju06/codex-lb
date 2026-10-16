## Context

Visual Studio Copilot's custom OpenAI-compatible provider probes
`GET /v1/models/{model_id}` before accepting a model. Without the route the probe
fails with 404 even though the model is listed.

Decisions:

- The route always returns the OpenAI `model` object. Unlike `GET /v1/models`, it does
  not branch on `client_version`: the Codex catalog has no single-model form, and
  Codex clients do not call this route.
- The path converter is `:path` because model-source slugs are not
  character-restricted (for example `org/name`).
- Visibility is computed by the same helper as the list, so allowlists and source
  assignment cannot diverge between the two routes.

Example: `GET /v1/models/org/name` with a key assigned to the source that defines
`org/name` returns that source model's list entry; a key not assigned to the source
gets `404` with `error.code = "model_not_found"`.
