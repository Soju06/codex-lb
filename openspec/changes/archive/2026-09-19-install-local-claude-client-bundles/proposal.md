# Install local Claude client bundles

## Why

Installed Claude commands currently point into the source checkout. Moving that
checkout, unmounting its volume, or losing access to it prevents `cc` from
starting before agent-lb receives a request.

## What changes

- Copy public clients, sibling runtime helpers, policy files, and the installer
  into a versioned local bundle.
- Point managed commands and policy at a stable local `current` link.
- Keep preview non-mutating, preserve replaced files and links, and retain old
  versions for active processes and rollback.
- Verify installed commands and uninstall after the source checkout is moved.
- Diagnose unavailable working directories and raise low process file-descriptor
  limits within the inherited hard limit before Claude starts.

## Impact

The client installer and runtime-portability contract change. Routing, account
credentials, source-volume permissions, and system-wide resource limits do not.
