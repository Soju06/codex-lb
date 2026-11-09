## Why
Cancellation or failure during credit return must not silently strand a websocket or release duplicate credits.

## What Changes
Serialize receives and finish dispatched credit return before propagating cancellation. Report an unexpected missing native permit as an error rather than panicking the helper.
