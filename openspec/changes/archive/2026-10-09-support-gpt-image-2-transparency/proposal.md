## Why
GPT Image 2 transparent PNG and WebP requests are rejected by the local Images validator before reaching the ChatGPT Responses image-generation tool. Live generation and editing through that tool have returned genuine alpha, so the unconditional model-wide rejection prevents supported requests from working.

## What Changes
- Accept transparent PNG/WebP generation and editing for GPT Image 2.
- Reject transparent JPEG locally with `param=output_format`, for every supported image model.
- Add schema and route regressions for public/Codex-native paths, JSON/streaming output, unchanged parameters and bytes, and upstream errors.
- Synchronize the image specification and context.

## Capabilities
### Modified Capabilities
- `images-api-compat`: alpha-capable output-format validation and transport parity.

## Impact
The runtime change is confined to image parameter validation. Host selection, account routing, authentication, request budgeting, and image translation remain unchanged. No setting, migration, or setup step is added.
