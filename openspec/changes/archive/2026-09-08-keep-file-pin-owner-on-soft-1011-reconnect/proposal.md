## Why

A live `input_file.file_id` pin is hard ownership and must stay on the
uploading account. Soft HTTP-bridge reconnect after upstream `1011` currently
treats that owner as skippable prompt-cache locality, so submit-on-closed
recovery can send the file to another account.

## What Changes

- Treat `file_required_preferred_account` as a required reconnect owner, even
  when the session key is soft and the close code is `1011`.
- Pass that requirement from submit-on-closed fresh-upstream retry so it
  cannot drop the pin.
- Keep `1011` skip-same-account for movable soft sessions that have no live
  file pin.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `responses-api-compat`: HTTP-bridge reconnect after `1011` must keep a live
  file-pin owner required, or fail closed.

## Impact

- `app/modules/proxy/_service/http_bridge/mixin.py` reconnect owner resolution.
- `app/modules/proxy/_service/http_bridge/request_submit.py` fresh-upstream retry.
- Unit coverage next to the existing hard-`1011` reconnect tests.
- No API, schema, dashboard, or settings changes.
