## Diagnosis and operational repair

On 2026-09-26, `ch/linxaq` in Codex CLI 0.157.1 advertised `code_mode_only` and freeform patches but lacked the `custom` opt-in on its configured source. The running proxy retained `wait` and collaboration while removing `exec`; full filesystem permission did not help because the model never received the execution tool. `ch/3sc1a4` had the same metadata gap.

Until the capability resolver fix is deployed, the existing configuration repair is to append `custom` to `experimental_supported_tools` in the server-side Model Sources metadata. Preserve all existing entries, for example `["send_user_message_async", "clock", "namespace", "custom"]`. Changing only the local Codex model catalog does not change source egress filtering.

The permanent fix interprets existing code-mode/freeform capability declarations consistently. Plain sources and hosted tools keep their explicit opt-in behavior. A disposable CLI workspace can verify the full path by reading an unpredictable marker through shell and writing a derived file through apply-patch. A model's text claim alone does not prove either tool ran.
