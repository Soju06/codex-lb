# Complete native cache-write accounting

Automation compact pings, limit warm-ups and quota planner probes currently
discard upstream cache-write counts before writing request logs. Preserve the
count through these remaining native writers and their quota reservations.
Refresh the bundled pricing snapshot after the upstream metadata generator
update so new model entries include their explicit cache-write rates.

This change does not alter cost-limit admission estimates.
