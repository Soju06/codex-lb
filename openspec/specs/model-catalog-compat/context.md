# Model catalog compatibility context

See the [catalog specification](spec.md) for the normative contracts.

## Source base instructions

An OpenAI-compatible source can supply `base_instructions` in each model's
`raw_metadata_json`. The registry projects a string value into its first-class
instruction field so both Codex catalog routes return it unchanged. Other
JSON types retain the existing empty default.

For example, `{"base_instructions": "Keep the project's existing coding style."}`
returns that sentence in the source model's Codex catalog entry. The projection
preserves whitespace and Unicode; it does not choose or rewrite the source's
instructions.

Importing only a model slug and context window cannot restore metadata that was
never stored. Keep the source's instruction and capability fields when importing
models. Clients with a pinned `model_catalog_json` file need to refresh that file
to receive catalog changes. Instruction projection does not infer namespace,
WebSocket or Responses Lite support; those retain their existing contracts.
