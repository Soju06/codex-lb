## 1. HTTP middleware

- [x] 1.1 Add a dedicated HTTP middleware that attaches `X-App-Version` to responses whose `status_code` is in the `200-499` range
- [x] 1.2 Source the header value from `app.__version__` and preserve any explicit downstream override with `setdefault`
- [x] 1.3 Register the middleware in the shared FastAPI app wiring so it covers dashboard, health, proxy, and static HTTP responses

## 2. Regression coverage

- [x] 2.1 Add focused unit coverage for the middleware on a `2xx` response and a `5xx` response
- [x] 2.2 Add integration coverage proving a representative `2xx` route returns `X-App-Version`
- [x] 2.3 Add integration coverage proving representative handled `4xx` responses return `X-App-Version`
- [x] 2.4 Add integration coverage proving a representative `5xx` response does not return `X-App-Version`

## 3. Spec delta and verification

- [x] 3.1 Add the `api-response-metadata` capability requirements for the `X-App-Version` header contract
- [x] 3.2 Validate the OpenSpec change with `openspec validate add-app-version-response-header --strict`
