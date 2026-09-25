The upstream Codex catalog uses `tool_mode` and `multi_agent_version` to tell
the client which collaboration protocol to assemble. Model-source metadata is
operator-controlled JSON, so the proxy must preserve those fields without
inventing multi-agent support for sources that did not declare it. A declared
multi-agent version is also the source capability signal needed by the
Responses tool filter: the collaboration tool is represented by a
`type: namespace` declaration and must survive forwarding to that source.

Example: a source model with `{"tool_mode":"code_mode_only",
"multi_agent_version":"v2"}` is listed with those fields and forwards its
`type: namespace` collaboration tool; a source without that metadata keeps
the existing filtering behavior.
