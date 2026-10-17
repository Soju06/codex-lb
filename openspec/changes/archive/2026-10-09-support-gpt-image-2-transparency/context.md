# Transparency validation rationale

This change removes a local rejection rather than changing the upstream image request shape. Successful live PNG/WebP generation, PNG editing, and Codex built-in image generation on stable 1.24.0 showed genuine alpha-zero background pixels. The contribution is reapplied to current main so newer host-selection behavior is preserved.

Regression tests use a deterministic upstream byte payload to prove that the adapter does not rewrite output. They cover JSON and streaming generation, public multipart edits, Codex-native JSON edits, transparent-JPEG rejection, opaque output, and preserved upstream error details.

Live dimensions/quality sometimes differed from requested values before and after the validator fix. This patch forwards settings and preserves upstream bytes; it does not promise exact output dimensions or universal upstream availability.
