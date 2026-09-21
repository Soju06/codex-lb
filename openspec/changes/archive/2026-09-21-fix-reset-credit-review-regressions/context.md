This follow-up implements the eight findings in the independent review recorded at `/tmp/codex-lb-reset-independent-review/review.md`. The previous archive's passing helper-heavy tests did not establish readiness; this change adds real-session consume coverage.

For example, a credit with twenty seconds left must not receive an unconditional thirty-second retry delay. The original credit identity remains pinned, and receipt persistence retries never become consume retries.

The scope is local code, tests and specifications. Throughput remains dependent on upstream latency and queue size; prolonged persistence outages remain an explicit limit. No production runtime data is changed by this work.
