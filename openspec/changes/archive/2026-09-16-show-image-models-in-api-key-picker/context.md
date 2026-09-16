## Missing image choices

The Images endpoints already accept image model IDs, but the API-key picker used a catalog built for conversational models. The fix supplements the dashboard catalog from the same allowlist used by Images validation. See the change's API-key delta spec for the requirements.

This list represents supported request model names, not a guarantee that every connected account can generate images. Existing authentication and model restrictions still apply. The public Responses/Codex catalogs retain their existing behavior. Adapter entries carry `imageOnly=true` so the Automations picker, which shares the dashboard catalog, can exclude them from Responses jobs.

For example, an operator can create a key with `allowedModels: ["gpt-image-2"]`, reopen its edit dialog and replace it with `gpt-image-1-mini`. An unrestricted key (`All models`) already permits these names subject to other policies; this fix makes explicit image-only restrictions configurable in the UI.

The backend addition requires no migration or new setting. After deployment, reload the dashboard to refresh the cached model list. Bootstrap and refreshed catalogs are both supplemented; duplicate IDs appear once.
