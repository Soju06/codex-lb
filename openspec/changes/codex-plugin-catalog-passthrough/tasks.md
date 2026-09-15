## 1. Passthrough routes
- [x] 1.1 Add `plugin_catalog_router` at the origin root with `GET /plugins/featured` and `GET /ps/plugins/{path}` forwarding through `_codex_control_proxy`.
- [x] 1.2 Keep `ps/` and `plugins/` out of the upstream `codex/` prefix in `codex_control_request`, alongside `wham/`.
- [x] 1.3 Register the router and add `ps/` / `plugins/` to the SPA fallback's API namespaces.

## 2. Firewall
- [x] 2.1 Protect `/ps/plugins/*` and `/plugins/featured` with the IP allowlist like the other proxy surfaces.

## 3. Coverage
- [x] 3.1 Integration: every catalog read Codex issues (list, installed, suggested, per-plugin detail, featured) forwards verbatim with pool credentials; non-GET is rejected with 405.
- [x] 3.2 Unit: upstream URL mapping keeps `ps/`, `plugins/`, and `wham/` at the root while everything else stays under `codex/`.
- [x] 3.3 Unknown `/ps/*` and `/plugins/*` paths return the OpenAI 404 envelope; firewall denies the catalog surface for unlisted IPs; route inventory classifies the new routes as fail-closed.
- [x] 3.4 Pass lint, type check, the touched suites, and strict OpenSpec validation.

## 4. Documentation
- [x] 4.1 Document `chatgpt_base_url` pointing at codex-lb in the Codex client setup page and the example config, linking back to this capability.
