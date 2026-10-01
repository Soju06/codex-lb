## Context

The monetary calculator receives per-million rates and uses decimal token-rate
products directly in microdollars. It cannot repair an already distorted input.

## Decision

Multiply the validated source value and scale as decimal values, then convert
the result to the existing float-valued ModelPrice contract. Keep the existing
finite/range checks and monetary calculator. Do not round arbitrary rates or
increase genuinely fractional final costs.

## Verification

Exercise both ordinary and Ultrafast source rates through parsing and the
monetary calculator. Real Responses requests with an installed LiteLLM catalog
must settle the same 20-microdollar example in both response modes.
