## Why

Model sources are only consulted by the HTTP request handlers. The WebSocket
session path goes straight to subscription-account selection, so a model served
by an enabled OpenAI-compatible source is dispatched to a ChatGPT account and
rejected upstream with:

```
The '<model>' model is not supported when using Codex with a ChatGPT account.
```

`docs/client-setup.md` documents `supports_websockets = true`, so model sources
are unusable with the documented Codex client configuration. See #1658.

## What Changes

- Extract the shared model-source resolution helpers into
  `app/modules/model_sources/selection.py` so the HTTP and WebSocket paths agree
  on which models belong to a source.
- Fail the WebSocket connect with `model_source_requires_http_transport` when the
  requested model resolves to an enabled Responses-capable source, instead of
  selecting a subscription account.
- Emit the failure as a `503` connect failure. Codex clients fall back to the
  HTTP transport only on service-level connect failures; a `4xx` is treated as
  terminal and surfaces to the user. After the fallback, the HTTP path routes to
  the model source normally.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `responses-api-compat`
