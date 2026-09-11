# Company model gateways

## Why
The local Codex client uses codex-lb, but company models are currently accessible mainly through separate agent CLIs. Users need the same model-selection, streaming, tool execution, and usage surfaces through codex-lb.

## What Changes
- Inventory TRAE, LLMBox, TTADK, Coco and TMates entrypoints, separating inference APIs from agent execution APIs; exclude GLM at the user's request.
- Add an explicit TRAE source backed by local login and the fixed company gateway, with native Responses translation, function/custom tools, continuation, queue handling and explicit failure semantics.
- Discover model metadata without publishing credentials or bundled agent instructions. Expose quota as unknown when upstream provides no quota contract, separately from observed consumption and queue/load.
- Validate eligible non-GLM models end-to-end. Prepare an isolated replacement and only cut over the active service with request continuity or coordinated interruption.

## Impact
Model source management, catalog, transport, dashboard and operator documentation. Existing subscription routes and user routing edits must be preserved. New sources start disabled.
