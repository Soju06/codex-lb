## 1. Local account-cap recovery

- [x] 1.1 Classify `account_stream_cap` and `account_response_create_cap` as recoverable account-capacity waits.
- [x] 1.2 Let streaming selection failures for those local cap codes use the existing bounded keepalive wait/retry loop.
- [x] 1.3 Preserve non-waitable permanent no-account selection failures.
- [x] 1.4 Add unit coverage for helper, streaming, and HTTP bridge capacity-wait behavior.

## 2. Validation

- [x] 2.1 Run targeted unit tests.
- [x] 2.2 Validate OpenSpec change strictly.
