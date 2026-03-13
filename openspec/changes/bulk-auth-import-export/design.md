# Design: bulk-auth-import-export

## Summary

Keep the existing `POST /api/accounts/import` route for single-file imports and add `POST /api/accounts/import/batch` for repeated `auth_json` multipart parts. The batch route returns `imported` and `failed` arrays. Each file is processed independently so one malformed or conflicting payload does not discard successful imports in the same request.

Add `GET /api/accounts/export` that returns a zip archive of current auth payloads built from the persisted account records. Before serializing each payload, attempt to refresh tokens when the stored access token is already expired or the persisted `last_refresh` is beyond the usual refresh threshold.

## Import Flow

1. Batch API reads all uploaded `auth_json` files.
2. Service parses each file independently.
3. If the uploaded access token is expired and a refresh token is present, service performs an immediate refresh exchange before saving the account.
4. Service upserts the account using the existing repository conflict policy.
5. Service records either an imported item or a failed item for each uploaded file.

## Export Flow

1. Service loads all persisted accounts.
2. For each account, decrypt stored tokens and attempt a best-effort refresh when the access token is expired or due for refresh.
3. Service serializes the current token set back into the existing `auth.json` shape.
4. Service writes one `auth.json` per account into an in-memory zip archive and returns it as a download.

## API Shape

- `POST /api/accounts/import`
  - request: multipart form with one `auth_json` file part
  - response: single imported account result
- `POST /api/accounts/import/batch`
  - request: multipart form with one or more `auth_json` file parts
  - response: `{ imported: [...], failed: [...] }`
- `GET /api/accounts/export`
  - response: `application/zip`

## Failure Handling

- Malformed payloads map to `invalid_auth_json` entries in `failed`.
- Import identity conflicts map to `duplicate_identity_conflict` entries in `failed`.
- Import-time refresh failures map to `refresh_failed` entries in `failed`.
- Export is best-effort for token freshness: if a refresh attempt fails, the archive still includes the latest persisted token set for that account.
