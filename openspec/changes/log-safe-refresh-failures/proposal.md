# Secret-safe refresh failure diagnostics

## Why
Refresh endpoint failures cannot currently be correlated to an account and OAuth failure category without inspecting credentials or guessing from timestamps.

## What Changes
- Log one bounded diagnostic for every failed OAuth exchange in AuthManager.
- Use a stable SHA-256 account reference (first 16 hexadecimal characters), an allowlisted error category, and permanent/transport flags.
- Never log raw account identifiers, tokens, provider messages, response bodies, or exception traces, including shared private refresh work.

## Impact
- Account authentication diagnostics and unit regression tests only; no credential lifecycle changes.
