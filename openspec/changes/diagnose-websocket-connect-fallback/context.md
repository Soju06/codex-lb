# Context

Observed ClientConnection.recv_messages AttributeError follows TLS initialization loss. Original TLS exception remains chained but fallback logging loses it. Record fixed known exception categories (unknown types become Other), numeric errno/winerror/verify_code, at most five explicit causes, bridge-attempt elapsed milliseconds and existing request correlation. Do not serialize exception messages, args, headers, payloads, URLs or tracebacks. Elapsed time is bridge-attempt duration, not a pure TLS duration. Example: ProxyResponseError -> ConnectionResetError errno10054.
