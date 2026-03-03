## Overview

Reuse the existing Responses payload sanitization hook and add one additional unsupported upstream parameter.

## Design

1. Add `safety_identifier` to `_UNSUPPORTED_UPSTREAM_FIELDS` in `app/core/openai/requests.py`.
2. Keep stripping centralized in `to_payload()` for both `ResponsesRequest` and `ResponsesCompactRequest`.
3. Keep non-listed extra fields untouched.

## Testing Strategy

- Verify `ResponsesRequest.to_payload()` strips `safety_identifier`.
- Verify `ResponsesCompactRequest.to_payload()` strips `safety_identifier`.
- Verify Chat->Responses mapped payload also drops `safety_identifier`.
