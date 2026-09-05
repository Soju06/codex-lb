# Support self-contained Codex context requests during account failover

## Why

A live Codex 0.153.1 request with experimental context management fails the fresh-replay validator before any response is visible. The client supplies `reasoning.context=all_turns`, local message and tool-bundle labels, typed transcript metadata, client correlation IDs, and namespaces of client-side tools. The validator currently treats these forms as unknown. A pre-visible 429 therefore pins the request to the now-excluded account and returns `preferred_account_unavailable`.

## What changes

Recognize the observed self-contained envelope through a closed, classification-only projection of local labels and transcript metadata, and validate namespaces containing ordinary function/custom tools. Keep the original wire body unchanged. Preserve existing rejection of encrypted retained state, hosted resources, stored response references and unknown extensions.

## Impact

Only the shared replay classifier and its tests/specification change. This does not deploy the proxy, make all encrypted state portable, implement persistent ownership of notes, or merge history across accounts. The experimental routing harness is used only for isolated live verification.
