"""Claude OAuth pool module.

Holds the Claude-specific proxy, auth manager, model catalog, and admin routes.
The Codex (``app.modules.proxy.*``) surface is kept untouched; this module is a
parallel provider stack addressable at ``/claude/v1/*`` routes.
"""
