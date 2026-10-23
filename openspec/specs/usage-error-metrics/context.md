# Controlled interruption presentation

Native Codex can interrupt an active generation to incorporate an incoming subagent message. The upstream terminal is `response.incomplete` with reason `interrupted`. Shared WebSocket settlement records that terminal as cancelled, preserving partial usage and forwarding the original event. The existing blue Cancelled badge is reused.

Retained older `error/interrupted` request-log rows receive the same display status and filter treatment. They are not rewritten in the database. Status-filtered totals use raw rows when legacy error-code splitting cannot be answered by the demand rollup. Unfiltered totals retain the rollup path.

Historical aggregate buckets retain their prior error counts under the existing historical-bucket policy. Newly stored cancellations enter the existing cancellation measures. For example, a subagent result arriving during generation can produce a Cancelled row with 241 output tokens; those tokens remain accounted for, and the row remains inspectable with reason `interrupted`.
