## 1. Implementation

- [x] 1.1 Add `firewall_cache_ttl_seconds` setting with sensible default and `gt=0` validation
- [x] 1.2 Use the setting when constructing the process-level `FirewallIPCache`

## 2. Verification

- [x] 2.1 Add unit coverage for default and overridden firewall cache TTL setting parsing
- [x] 2.2 Add unit coverage that the cache instance reflects the configured TTL
