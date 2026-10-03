# Model source aliases

In Settings → Model Sources, enter `friendly-model=provider-model` in Models. Clients select `friendly-model`; the source endpoint receives `provider-model`. Bare model IDs keep identity forwarding. Separate entries with commas or newlines.

Aliases use the existing source credential and capabilities. Public IDs continue to govern model permissions, source assignment, pricing and logs. Successful response model fields use the public ID; generated text and tool arguments are unchanged. Remove `=provider-model` to return to identity forwarding.

Roll out alias support to all replicas before configuring mappings. Older replicas ignore the mapping; remove it before reverting to an older release.

Requirements: [model-source-routing](../openspec/specs/model-source-routing/spec.md) and [model-catalog-compat](../openspec/specs/model-catalog-compat/spec.md).
