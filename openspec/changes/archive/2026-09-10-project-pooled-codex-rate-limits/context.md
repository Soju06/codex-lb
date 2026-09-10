The reported warning originated in the Codex CLI's threshold logic, which
accepts default `codex.rate_limits` events as its current account usage. A
selected account at 97% used could overwrite a pool at 66.5% used. The upstream
privacy switch made this more confusing by hiding pooled headers while raw
account events remained visible.

Projection uses pooled headers as the existing aggregate contract. HTTP keeps
one snapshot consistent with its response headers; long-lived WebSockets read
the cached aggregate on quota events only. No upstream refresh is introduced
on each event. Original ingestion remains upstream of projection, so routing
still sees account-specific usage. Unrepresented limit families are omitted
instead of presenting global usage as model-specific capacity.

With privacy enabled, this fix removes misleading account warnings; it does
not publish previously hidden pool data. Operators who want pool percentages
in Codex must use the existing upstream-quota visibility setting. API-key
self-usage remains a distinct key-budget surface.
