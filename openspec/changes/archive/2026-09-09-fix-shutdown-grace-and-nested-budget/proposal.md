# Fix shutdown settlement grace and nested cleanup bounds

Graceful shutdown must reserve the existing post-drain cleanup window for the work that runs after the drain barrier: terminal websocket settlement and the recovery-settlement pre-drain both draw on that shared remainder, so an exhausted drain still leaves them a budget. Lifespan cleanup must use a live remainder throughout instead of nesting the full configured drain timeout inside the server cleanup window.

This change also validates the configured drain timeout as a positive, bounded integer.
