# Automatic Daybreak routing

Rationale, conversation boundaries and upgrade behavior are recorded in the
[canonical routing context](../../specs/responses-api-compat/context.md#automatic-daybreak-routing).

The contributor configuration and generated README credit are required by the
existing `Contributors attribution` CI job, which checks the PR author and
commit authors as well as merged contributors. Compact trailing-slash aliases
implement the equivalent-path recognition required by this change's Responses
compatibility specification. Both alias forms have native and legacy Daybreak
account-selection coverage in `test_native_daybreak_http.py`.

The Create API key screenshots use synthetic dashboard fixtures, built assets,
and the default unchecked toggle. Before is upstream main `72630bbf`.

| Before | After |
| --- | --- |
| ![Before](screenshots/before.png) | ![After](screenshots/after.png) |
