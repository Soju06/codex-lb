# Native source Responses WebSocket

This upstream port depends on public model aliases and durable source reference ownership. A session stays on one source credential/revision; every turn rechecks authorization, acquires admission and settles its own reservation. Equivalent source retries are bounded and allowed only before externally visible work on a source-neutral request.

The source capability defaults off. Operators enable it only after confirming their endpoint supports native Responses WebSocket. A provider accepting HTTP SSE is not evidence of WebSocket compatibility. For example, enable the flag on a test source, connect to `/v1/responses`, send two sequential `response.create` events and verify the same credential owns both turns and both usage records.

The upstream port excludes the fork-only key dashboard/installer, deployment and subscription-overflow. Catalog preferences use the complete permitted source pool. Real-client handshake fallback and real-provider conformance remain staging gates. The PR stays draft while those and maintainer product review are outstanding.
