## Why

The HTTP bridge stamps the selected account's `x-codex-installation-id` into
`client_metadata` at four sequential submit sites (pre-ledger, pre-admission,
pre-send, final submit) plus the retry paths. Each stamp decodes and re-encodes
the whole `response.create` frame (50-500 KB) even though calls two to four are
idempotent no-ops and only a ~200-byte top-level object ever changes. On the
single-core production deployment this helper is the largest app-attributed
frame in the 60 s GIL profile (130/1085 samples, 12%), and the fresh
(anchor-free) retry text doubles the cost when it is present.

## What Changes

- Memoize the stamp result on the request state by installation id and `str`
  identity, so re-stamping an already-stamped text object is O(1). Every text
  rewrite yields a new `str` object and an account swap changes the id, so both
  miss and take the full path automatically. The pre-existing size check on the
  fresh text runs once per stamped object instead of once per call.
- Replace the first-pass full decode/encode with a byte-identical splice of the
  trailing top-level `client_metadata` value (or an insertion when the key is
  absent). Any other frame shape falls back to the existing decode/encode path.
- The WebSocket relay reuses the same splice-first helper.
- No spec delta: wire bytes, `request_text`, persisted operation fingerprints
  and the account-owned installation-id contract are unchanged.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- None. `upstream-proxy-routing` ("Codex installation metadata must be
  account-owned") and `responses-api-compat` ("Selected Codex installation
  identity is internally consistent") keep their requirements; this change only
  makes the implementation cheaper while producing identical output.

## Impact

- Affected code: `app/modules/proxy/_service/http_bridge/request_submit.py`
  (`_text_with_account_installation_id`,
  `_http_bridge_text_with_account_installation_id`),
  `app/modules/proxy/_service/support.py` (`_WebSocketRequestState` memo
  fields), `app/modules/proxy/_service/websocket/mixin.py`
  (`_websocket_text_with_account_installation_id`).
- Tests: `tests/unit/test_proxy_http_bridge.py`, `tests/unit/test_proxy_utils.py`.
- Expected production effect: helper share of GIL samples falls from ~12% to
  under 2%; re-profile with `py-spy --gil` after deploy.
