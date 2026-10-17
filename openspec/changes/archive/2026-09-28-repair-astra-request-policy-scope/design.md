# Design

Remove only the two Astra-specific request-policy capability checks.
Subscription serialization already drops `truncation`; the validator must
not reject it because an enforced anchored reset introduced an update.
Leave the existing automatic-compaction rejection after update validation
and the enforced reset intact.

Logprobs controls remain subject to generic Responses validation and the
upstream service. Route regressions cover both Responses paths, keyless
requests and the unchanged compaction boundary. The source-owned route
continues to use its own schema rather than these subscription rules.
