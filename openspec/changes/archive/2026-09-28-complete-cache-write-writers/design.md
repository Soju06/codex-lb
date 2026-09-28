# Design

Retain the existing request-log repository boundary. Automation extends its
compact-usage tuple and log call; limit warm-up passes the parsed response
detail into the existing log call; quota planner retains the detail in
`WarmupUsage` through both log insertion and keyed reservation finalization.
Missing upstream detail remains nullable in logs and zero at the existing
reservation settlement boundary.

Regenerate, rather than hand-edit, the bundled snapshot with the parser
already merged from #2459. Keep admission estimates and unrelated tests
unchanged. A route-level regression for automation and quota planner, an
upstream-event parsing check, and a writer-level limit warm-up regression
pin the three affected paths.
