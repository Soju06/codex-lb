## Why

HTTP bridge requests can queue behind the per-session `response_create_gate`
after they have already claimed a bridge queue slot. They should wait through
normal in-flight work, but a wedged gate still needs the existing bounded local
overload path so the queue slot and account lease are not held forever.

## What Changes

- Keep visible HTTP bridge submissions on the per-session response-create gate
  instead of failing immediately while another in-flight request owns it.
- Apply the configured proxy admission wait timeout to that gate wait.
- Preserve `response_create_gate_timeout` as the stable local-overload reason
  when the gate does not open in time.
- Ensure interrupted queued submissions release admission state and queue slots.
