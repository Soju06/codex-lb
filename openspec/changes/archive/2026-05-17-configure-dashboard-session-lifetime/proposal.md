# Change Proposal

Dashboard password sessions currently use a fixed 12-hour lifetime baked into the backend. Operators who want a longer-lived local dashboard session have no supported way to change it, so they get logged out more often than needed.

## Changes

- Add a persisted dashboard setting for the absolute dashboard password session lifetime, defaulting to the current 12 hours.
- Use that setting when issuing dashboard auth cookies so newly created password sessions honor the configured lifetime.
- Expose the setting in the dashboard Settings UI with a validated operator-controlled value and a warning when the configured lifetime exceeds 30 days.
