# Synthetic dashboard comparison

Both images render `RecentRequestsTable` with four explicit synthetic rows. The before component is from main at d1fd2f21; the after component includes this change. All API requests were aborted in the screenshot harness.

- Short output: 10 non-reasoning tokens over 5 ms; the sample is now unavailable.
- Cleanup delay: 160 non-reasoning tokens, first output at 200 ms, terminal at 1000 ms, total request at 3000 ms; the observed estimate is approximately 200 TPS.
- Historical row: all three new evidence fields are null; its value is labelled as a legacy estimate.
- Unknown reasoning usage: no numeric TPS.

The images contain no service records, accounts, credentials or request contents.
