# Ultrafast cost accounting

The existing public contract accounts for input, cached reads and output.
Ultrafast needs explicit prices rather than a multiplier: above 272000 input
tokens, Astra's input/cached/output rates are 120/12/450 USD per million,
versus 60/6/300 at or below the boundary. Prices are API-equivalent estimates,
not monthly subscription charges or purchased subscription credits.

For example, 100000 uncached input tokens and 10000 output tokens cost USD9.00
when the response bills Ultrafast, but USD1.50 if a requested Ultrafast response
is downgraded to default. Requested tier alone cannot justify rewriting an
existing recorded cost. Subscription quota percentages already reflect upstream
consumption and must not be multiplied.

The official source is https://learn.chatgpt.com/api/docs/pricing. Metadata
refresh may omit Ultrafast fields; the existing compatible merge preserves
known fields when standard prices and context thresholds agree. Incomplete,
negative and non-finite tier groups must not enter the active catalog.

PR #2504 separately introduces cache-write accounting. This change does not
duplicate its storage, migration, prices or writer plumbing. PR #2544 is a
metadata refresh, not an alternative Ultrafast implementation.
