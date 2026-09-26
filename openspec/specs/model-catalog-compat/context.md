# Model catalog compatibility context

The [specification](spec.md) defines the catalog contracts used by Codex and
OpenAI-compatible clients.

## Custom source collaboration capabilities

Codex assembles collaboration tools from model metadata. Importing only a
custom model's slug, context window, and reasoning levels can leave the client
without subagent tools even when the source supports them. Preserve the
upstream `tool_mode`, `multi_agent_version`, `multi_agent_reasoning_effort`,
`experimental_supported_tools`, and model instruction fields in source
metadata. `base_instructions` is projected into a first-class registry field;
other capability metadata is passed through as Codex catalog extras.

For example, a source model `custom/coder` declaring `tool_mode=code_mode_only`
and `multi_agent_version=v2` exposes those fields to Codex. The source tool
filter also derives `namespace` support from that version. Older running
replicas can use an explicit `namespace` entry in
`experimental_supported_tools` until the updated code is deployed.

A client with `model_catalog_json` pinned to a local file must refresh that
file and start a new Codex session to receive metadata changes. Keep the
source's verified HTTP transport settings: multi-agent capability does not
imply WebSocket or Responses Lite support. Do not replace a model's reserved
collaboration schema with a hand-written tool schema; upstream validates it.

## Custom source model aliases

A source can expose `cd/gpt-6-astra` while sending `cd/linxaq` upstream. The
public slug comes from the model row; `upstream_model` in raw metadata is
private routing configuration and is omitted from both model catalogs.
Instructions and multi-agent capabilities remain attached to the public alias.

Configure `cd/gpt-6-astra=cd/linxaq` in the source Models field. After rollout
and configuration, refresh any pinned client catalog before selecting the alias.
See [source alias operations](../model-source-routing/context.md#model-aliases)
for metadata preservation, token scope and rollback considerations.
