- [x] Add `server_is_overloaded` to the canonical transient failure classifier.
- [x] Add `server_is_overloaded` to the bounded streaming transient retry set.
- [x] Add unit coverage for classification without an HTTP 5xx status.
- [x] Add integration coverage proving the public Responses route retries the first terminal overload event.
- [x] Cover the production-default HTTP responses session bridge retry path.
- [x] Retry one native Codex continuation overload that arrives after
  `response.created` but before model output, with same-account backoff and
  preserved completed-response continuity.
- [x] Add lifecycle negative controls for public SDK streams, prior model
  output, and exhausted replay budget.
- [x] Update the Responses compatibility requirement and validate OpenSpec.
