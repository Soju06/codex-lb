# Verified browser evidence

Captured in Chromium on 2026-09-10 from freshly built frontend bundles. Before: the three product components from main `43a45f78`. After: this PR's components. The capture explicitly checks that a deactivated account has no Resume control in the baseline and has one after the change, in both the dashboard list and Accounts page. Both builds use the same synthetic account state and response fixtures; no credentials or live upstream requests are involved.

PNG pairs cover dashboard list at 1440x900, responsive cards at 640x900 and 1024x900, and Accounts page at 1440x900. The Accounts page uses fixture responses for trends and reset-credit queries and is scrolled to keep the recovery controls visible. The red `Not found` banner in both Accounts captures is an intentionally unsupported upstream-proxy fixture request; it is unrelated to recovery actions and is not a product failure from this change.

The permanent dashboard browser regression covers list containment and card widths 640, 1024 and 1440, including the extra reset-credit button and the no-Resume reauth_required case. All 6 browser tests passed; account/dashboard component tests passed 484 tests in 41 files. Frontend lint, typecheck, build and scoped strict OpenSpec validation passed. Product/test code is unchanged by this evidence refresh.
