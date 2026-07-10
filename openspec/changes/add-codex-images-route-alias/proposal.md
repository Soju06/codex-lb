# Proposal: add-codex-images-route-alias

## Why

Codex clients configured with codex-lb use `/backend-api/codex` as their base
URL. When Codex invokes image generation with reference images, it submits a
JSON request containing `images[].image_url` data URLs to
`/backend-api/codex/images/edits`. codex-lb currently registers the existing
Images API adapter only under `/v1/images/edits`, so the Codex-base request
reaches no POST handler and returns `405 Method Not Allowed`.

## What Changes

- Register the existing image generation handler and a Codex-native JSON image
  edit adapter under the Codex-base router as non-OpenAPI aliases.
- Keep `/v1/images/generations` and `/v1/images/edits` as the canonical public
  OpenAI-compatible routes.
- Decode Codex's `images[].image_url` base64 data URLs, then preserve the
  existing validation, authentication, account routing, observability, and
  Images response shape for both route families.

## Impact

- Codex can use image generation and reference-image editing through its
  configured codex-lb provider without changing `base_url`.
- The change is limited to routing aliases and regression coverage; it does
  not introduce a new upstream API or alter model selection.
