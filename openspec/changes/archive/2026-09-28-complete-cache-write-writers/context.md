# Writer coverage

The proxy paths already preserve `input_tokens_details.cache_write_tokens`.
Compact automation pings and two independently logged warm-up paths also
consume native Codex responses, but currently retain only cached reads.
Their missing write counts cannot be recovered by the cost backfill.

For example, 100,000 Astra input tokens that are all writes cost $1.25;
without the count an automation or warm-up log charges the $1.00 ordinary
input rate. Planner warm-ups with an API key must also settle the same count
in their reservation. Cost-limit admission estimates remain a separate
policy decision: the reservation is corrected at finalization.
