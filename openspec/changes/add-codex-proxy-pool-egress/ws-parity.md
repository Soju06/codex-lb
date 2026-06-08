# Codex websocket parity gate

Before websocket production cutover, prove the Codex upstream client preserves resolver proxy use, built-in fingerprint kwargs, headers/cookies, timeout behavior, text/binary frames, close/error classification, cancellation cleanup, existing `auto/http/websocket` strategy, 426 HTTP fallback, and no replay after response events start.
