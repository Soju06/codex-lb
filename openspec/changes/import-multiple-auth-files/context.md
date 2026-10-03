# Context

Sequential imports reuse the existing single-file request and refresh hook. A batch of A, B, C that fails on B retains B and C for retry and never resubmits A. Selection and submission are disabled while a request is in flight. The API and OAuth flows remain unchanged.

The change remains active for upstream review; archive after acceptance. Screenshots use synthetic data at 1440px and 390px.
