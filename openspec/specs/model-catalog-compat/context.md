# Model catalog compatibility context

The requirements are in [spec.md](spec.md).

## Codex client discovery

The native catalog can advertise a model correctly while a client still uses
its bundled list. This distinction matters when a model becomes available
after the installed client's bundled catalog was built.

In Codex 0.159.0, configuring a provider's `env_key` puts discovery on the
API-key path, including when `requires_openai_auth = true`. That path needs
both the `api_key_model_discovery` feature and support for an API-key catalog.
A custom `base_url` needs an explicit `model_catalog_url` for the latter.

For example, a provider whose base URL is
`http://127.0.0.1:2455/backend-api/codex` uses
`http://127.0.0.1:2455/backend-api/codex/models` as its catalog URL, together
with `api_key_model_discovery = true` in `[features]`. Keep the host and port
aligned when configuring a remote installation. These settings do not replace
the provider's authentication or capability-routing settings.

The [client setup guide](../../../docs/client-setup.md#model-discovery-in-the-codex-app)
and [downloadable example](../../../docs/examples/codex/config.toml) include
both settings. Merge them into existing TOML tables and restart the desktop
app so it loads the updated configuration.

### Evidence and limits

An isolated Codex 0.159.0 app-server probe using the same authentication and
binary produced the following `model/list` results:

| Configuration | Newly advertised model |
| --- | --- |
| Built-in subscription provider | Present, not hidden |
| Custom provider with `env_key`, without discovery settings | Absent |
| Custom provider with the discovery feature only | Absent |
| Custom provider with the feature and explicit catalog URL | Present, not hidden |

This verifies catalog ingestion, not inference entitlement. Setting `model`
by name and populating the model picker are separate concerns. The behavior
is version-specific; this evidence does not promise compatibility with every
Codex release.

Version-pinned Codex sources:
- [API-key discovery gate](https://github.com/openai/codex/blob/687a119f0fcaace47e1f1abcc77cec6c813fd6da/codex-rs/models-manager/src/manager.rs#L485-L495)
- [Custom-provider catalog support](https://github.com/openai/codex/blob/687a119f0fcaace47e1f1abcc77cec6c813fd6da/codex-rs/model-provider/src/models_endpoint.rs#L201-L209)
