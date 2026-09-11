# Context

Issue #2343 reproduces the observation gap on upstream c52941a4c using an admitted native HTTP bridge stream, synthetic upstream and a held real reservation finalizer. Current global and bridge counters correctly show zero while detached settlement remains reserved. Existing graceful persistence draining returns false while held and true after release.

The accepted additive observation describes registered process ownership, not historical write success or an atomic stop certificate. A completed task remains an owner until its synchronous done callback removes it and registers any fallback. Filtering done tasks before that callback can expose false zero. The observer must share the shutdown persistence classifier and keep existing asynchronous settlement and bridge-only cleanup meanings.

Example: global0, bridge pending0 and bridge blockerfalse may coexist with persistence pending1. Unsupported service observation returns unknown with no numeric count. A deployment consumer still needs admission and lifecycle controls. This source change cannot retrofit the running predecessor or change its interruption limit.
