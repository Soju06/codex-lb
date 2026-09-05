The official Responses tool-search guide includes tool_search_output items containing tools returned by hosted discovery. This item does not match the generic _call/_call_output suffix checks. Explicit recognition keeps the existing unknown-item policy narrow.

Reference: https://developers.openai.com/api/docs/guides/tools-tool-search

This change concerns downstream response normalization. It does not change account-neutral replay projection, compact recovery, or tool-search pairing rules covered by separate work such as PR #1952.

For example, a completed response containing tool_search_output(tools=[calculate_total]) followed by function_call(calculate_total) previously reached the public client with only the function call. The corrected response retains both items and the original loaded schema.

Verification uses deterministic route-level upstream fixtures. The same four tests fail on unchanged main and pass with the single production-code change. The related route and public response-contract suites contain 154 passing tests.
