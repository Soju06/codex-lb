## Tasks

- [x] Add GPT-5.6 bootstrap model metadata and tests.
- [x] Align GPT-5.6 bootstrap metadata field-for-field with the upstream
      bundled catalog (`codex-rs/models-manager/models.json` at rust-v0.144.1):
      `tool_mode`, `multi_agent_version`, `minimal_client_version` 0.144.0,
      `experimental_supported_tools`, reasoning-summary fields, 21-plan
      availability, Sol `availability_nux`.
- [x] Extend backend reasoning effort validation for `max` and `ultra`.
- [x] Alias `ultra` -> `max` on the upstream wire (request policy + chat
      enforcement) with unit and route-level regression tests.
- [x] Accept `max`/`ultra` in the OpenAI-compatible `thinking` string alias.
- [x] Raise the fallback `model_registry_client_version` to 0.144.0.
- [x] Extend frontend model/API-key/automation schemas and controls.
- [x] Extend the Responses Lite route regression to a GPT-5.6 bootstrap slug
      (additional_tools preserved, Lite header signaling, ultra aliased).
- [x] Run targeted backend and frontend tests.
- [x] Run OpenSpec validation (`openspec validate add-gpt56-bootstrap-models --strict`).
