## Approach

Apply the existing lease policy to the observed rate-limited soft owner before the grace draw. Continue to use the final lease check for races. When a first thread would need provisional probe admission, retry without discretionary probes so the ordinary write initializes both records together.
