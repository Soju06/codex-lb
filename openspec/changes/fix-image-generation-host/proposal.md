# Route image generation through a compatible host model

## Why

The Images adapter currently shares the default account-probe host resolver,
which prefers `gpt-5.6-luna`. Upstream rejects the adapter's forced
`image_generation` tool choice on that host even though the same request works
through `gpt-5.6-sol`.

## What Changes

Give Images routes a dedicated ordered host resolver that prefers
`gpt-5.6-sol`, while leaving account probes unchanged. Preserve public
`gpt-image-*` model IDs and the existing request/response translation.

## Impact

Images host selection, Images route regression coverage, and Images operational
context. No setting, retry loop, or public API change is added.
