## 1. Responses validation and normalization

- [x] 1.1 Normalize Responses `input` string into a single `input_text` list item in request validation
- [x] 1.2 Reject `previous_response_id`, `truncation`, and `input_file.file_id` with OpenAI invalid_request_error
- [x] 1.3 Reject unsupported built-in tool types in Responses tools with invalid_request_error

## 2. Chat multimodal mapping

- [x] 2.1 Map chat content parts to Responses parts (text -> input_text, image_url -> input_image, input_audio/file -> input_file)
- [x] 2.2 Convert audio/file parts to data URLs and reject `file_id` with invalid_request_error
- [x] 2.3 Preserve existing tool_choice/response_format/reasoning_effort mappings after multimodal changes

## 3. Image URL proxying

- [x] 3.1 Implement image URL fetch + data URL conversion with size limit and timeout
- [x] 3.2 Apply image URL proxying to Responses `input_image` parts before upstream

## 4. Compatibility tooling and docs

- [x] 4.1 Add a supported/unsupported matrix table to `refs/openai-compat-test-plan.md`
- [x] 4.2 Update `scripts/openai_compat_live_check.py` to print expected unsupported list and include it in results JSON

## 5. Tests

- [x] 5.1 Add/update integration tests for Responses normalization and unsupported parameters/tools
- [x] 5.2 Add integration tests for chat multimodal mapping and file_id rejection
- [x] 5.3 Add unit tests for image URL proxying helper (success, failure, size limit)
