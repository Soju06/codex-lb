# Model source aliases

Operators can give opaque custom models a stable public ID. In Model Sources, create or edit the Models field using `cd/gpt-6-astra=cd/linxaq`. Entries are separated by commas or newlines; a bare model ID keeps identity forwarding. To retain both public names, list both `cd/linxaq` and `cd/gpt-6-astra=cd/linxaq` in the same source. Both entries use that source's credential; aliases do not create a token pool.

The left side is the model clients request and select in their catalog. The right side must exactly match the ID accepted by the upstream endpoint. For an endpoint whose actual ID is `ch/linxaq`, the corresponding entry is `cd/gpt-6-astra=ch/linxaq`.

The dashboard API represents the same mapping as:

```json
{
  "model": "cd/gpt-6-astra",
  "rawMetadataJson": "{\"upstream_model\":\"cd/linxaq\"}"
}
```

Merge `upstream_model` into existing raw metadata; replacing the entire object can discard capabilities such as `multi_agent_version`, instructions and reasoning settings. Editing an existing model into `new-alias=existing-model-id` in the dashboard carries its settings across automatically. Clearing `=upstream-id` removes the mapping. API updates replace the supplied model list, so callers must retain every model they intend to keep.

The routing setting lives in the existing metadata JSON to avoid a database migration or environment setting. Resolution happens after the proxy checks the public model's permissions, capability and source assignment. Targets are one-hop strings, not links to other model entries. Pricing and logs remain keyed by the public model. Only protocol model fields in successful JSON/SSE responses are translated; generated text, function arguments and upstream error messages are unchanged.

Roll out support to every replica before saving alias metadata. Older versions ignore this setting and would send the public alias directly upstream. Remove mappings before reverting to an older version. Existing sources without the setting keep their current behavior. A client with a pinned local model catalog must refresh that file after aliases are configured, then select the public ID.

The feature uses the existing HTTP routes; it adds no WebSocket or compaction support. Invalid targets are rejected at create/update time. Empty sides, duplicate public names and multiple equals separators are rejected by the form. Unsupported trailing-slash URLs retain their existing errors.

Requirements are in the [routing delta](specs/model-source-routing/spec.md) and [catalog delta](specs/model-catalog-compat/spec.md).
