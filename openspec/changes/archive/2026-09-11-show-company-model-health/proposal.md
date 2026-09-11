# Show independent company-model health in the dashboard

Expose the last scheduled probe for each company model in Settings → Model sources, including freshness, latency, failure code, measured token usage and catalog eligibility. Separate the source's current admission constraints from probe health: a successful minimal probe does not guarantee a long-context request or cross-provider compaction will work. Reads must not trigger probes or alter configuration. Preserve production while verifying an isolated preview.
