# Design

## Context

The dashboard probe builds a fixed request body and sends it directly to `/backend-api/codex/responses`. Ordinary Responses forwarding removes `max_output_tokens` before upstream dispatch. A live probe using `max_output_tokens=16` returned `400` with `Unsupported parameter: max_output_tokens`; the same minimal request without that field returned `200`.

## Goals / Non-Goals

**Goals:** Keep one small account-pinned request that can verify upstream acceptance and refresh usage.

**Non-Goals:** Change routing, account status recovery, or the dashboard response schema.

## Decisions

Remove the field from the probe's fixed body. Reusing the full Responses sanitizer would add coupling to a fixed payload with no user-supplied fields. Retrying after a `400` would send two requests and could mask other invalid-request errors.

Keep the one-dot prompt and close the upstream stream after response headers, as the existing probe does. The test will inspect the outbound body through the dashboard route, so a future unsupported field cannot pass unnoticed at the failing product path.

## Risks / Trade-offs

- The request has no explicit output-token cap. The fixed one-dot prompt and early stream close keep work small; a different upstream response may still use some quota.
- A future upstream contract can change again. The route-level test protects the known request shape, and the probe response keeps the upstream status visible.
