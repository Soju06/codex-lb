# API-key secret response context

## Decision

Inject FastAPI `Response` into create/regenerate handlers and set the existing
credential export policy only after successful service operations:

- `Cache-Control: no-store, no-cache, must-revalidate, private`
- `Pragma: no-cache`
- `Expires: 0`

Typed Pydantic responses remain unchanged. Error paths never receive secret
headers or a plain key.

## Constraints

- Cover both collection URL forms and regeneration.
- Do not apply to list/update/delete.
- Preserve write authorization and secret-free application/audit logs.
