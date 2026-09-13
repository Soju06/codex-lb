## Context

The API-key router has the prefix `/api/api-keys`, while its collection
handlers are registered only at `"/"`. Starlette therefore owns the missing
slash behavior instead of the API-key module, and direct clients can receive a
redirect before the request reaches the collection operation. The application
also has a catch-all SPA route, but it intentionally rejects `api/` paths.

## Goals / Non-Goals

**Goals:**

- Serve both collection URL forms directly for GET and POST.
- Keep one canonical OpenAPI operation per collection method.
- Preserve existing dependencies, response models, detail routes, and SPA
  fallback behavior.

**Non-Goals:**

- Changing global slash redirect behavior.
- Adding compatibility aliases for API-key detail routes.
- Changing authentication, API-key persistence, schemas, or frontend calls.

## Decisions

Register an additional `""` decorator on each existing collection handler and
exclude that alias from the generated schema. Both decorators therefore invoke
the same typed function with the same dependencies and response model, while
the existing `"/"` route remains the documented canonical path.

Alternative: disable slash redirects globally. Rejected because it changes
every router and does not itself register the missing API-key path.

Alternative: change the SPA catch-all. Rejected because API compatibility
belongs to the API-key router, and broadening the fallback would increase the
blast radius without fixing direct route ownership.

## Risks / Trade-offs

- Duplicate OpenAPI operations could confuse generated clients -> hide the
  unslashed compatibility aliases from the schema.
- Decorator drift could make route forms diverge -> stack both decorators on
  the same handlers and assert request-level response equivalence.
