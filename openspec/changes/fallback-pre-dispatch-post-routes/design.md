# Design: Pre-Dispatch POST Fallback

`CodexClient.request_with_route_metadata` already preserves two independent facts for a routed transport error: whether the failure happened before dispatch (`retryable_same_contract`) and whether it was a TLS verification failure (`is_tls_verification_failure`). The fallback decision will continue to allow all existing idempotent-method fallbacks, and will additionally allow non-idempotent methods only when the first fact is true and the second is false.

The request body is never replayed after a response has been received. A connection reset raised as `aiohttp.ClientConnectorError` occurs while opening the proxy tunnel/TLS connection, so no upstream HTTP request or streaming response can exist. The next endpoint is therefore attempted with the original request contract and route metadata is returned from the endpoint that actually produced the response.

No endpoint or pool data is changed by this patch. Existing pool ordering supplies the operational remediation for an eligible failing routed primary, while production remains on the stable 8800 egress path. Port 9674 is excluded from the supported deployment plan.
