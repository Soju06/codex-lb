"""Opt-in loopback bridge from a ChatGPT-signed-in Codex Desktop to codex-lb.

Spec: ``openspec/changes/add-codex-desktop-auth-bridge/specs/desktop-auth-bridge/spec.md``.
"""

from app.desktop_bridge.relay import BridgeConfig, create_bridge_app, run_bridge

__all__ = ["BridgeConfig", "create_bridge_app", "run_bridge"]
