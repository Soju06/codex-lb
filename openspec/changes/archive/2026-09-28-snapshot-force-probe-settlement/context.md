## Failure boundary

The dashboard route awaits `ProxyService.record_account_probe_result`, which delegates to `LoadBalancer.record_probe_result`. Its usage reads happen in `_proxy_repo_context`; `get_background_session` closes that scope by rolling back an open read transaction. SQLAlchemy expires the loaded rows on rollback even though the session uses `expire_on_commit=False`. Later normalization under the account lock tries to load attributes from the now-detached rows.

Ordinary selection already uses `clone_row` snapshots before leaving its repository scope. Force Probe will use the same snapshot boundary and retain its existing health-observation version check. A newer failure must still defeat an older successful probe; lease-only changes must not.

For example, a successful probe with healthy primary and secondary usage should advance a probing account's success streak after the read session closes. It must not merely return HTTP 200 while logging a settlement error and leaving the streak unchanged.
