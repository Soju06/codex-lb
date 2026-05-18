# Proposal: support-responses-builtin-tools

## Why

Recent Codex Desktop builds now send newer built-in Responses tools such as `image_generation` and computer-use tool definitions. The current proxy still rejects those tool objects on full Responses routes and forwards them unchanged to the compact endpoint, which causes upstream `400 invalid_request_error` failures.

## What Changes

- Allow built-in Responses tools to pass through on `/backend-api/codex/responses` and `/v1/responses`.
- Keep Chat Completions compatibility behavior unchanged: only `web_search` remains supported there.
- Sanitize `/backend-api/codex/responses/compact` and `/v1/responses/compact` requests so tool-related fields are removed before the upstream compact call.

## Impact

- Restores compatibility with newer Codex Desktop request payloads.
- Reduces future breakage from new built-in Responses tool types on the full Responses path.
- Prevents compact requests from failing when desktop clients reuse full Responses payload shapes.
