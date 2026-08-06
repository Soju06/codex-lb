# Fix shutdown settlement grace and nested cleanup bounds

Graceful shutdown must reserve the existing post-drain cleanup window for terminal websocket settlement. Lifespan cleanup must use the live drain remainder instead of nesting the full configured drain timeout inside the server cleanup window.

This change also validates the configured drain timeout as a positive, bounded integer.
