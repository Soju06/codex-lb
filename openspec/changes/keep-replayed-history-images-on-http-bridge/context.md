# Context: keep-replayed-history-images-on-http-bridge

Normative text lives in the delta `specs/responses-api-compat/spec.md`. This
file records provenance and the known risk of the clause being flipped back.

## Requirement history (the clause has not flipped back and forth)

| Date | Commit | Change to `Requirement: Responses input images bypass the HTTP bridge` | Cause |
|---|---|---|---|
| 2026-06-03 | `bcd63c827` (#903) | Added: bypass for "any `input_image` part in top-level input items, nested message content, or tool output content", "over the raw HTTP Responses stream path" | Live beta: an invalid inline PNG held a bridge pending slot until timeout |
| 2026-06-05 | `fb8800f46` (#930) | Code only: the bypass condition gains `image_generation_request` | Restore Codex image-generation tool |
| 2026-07-13 | `07c6babe4` (#1252) | Synced into the main spec unchanged | Bulk archive |
| 2026-09-11 | `cb21daaed` (#2386) | "raw HTTP" became "raw (non-bridge)"; added "MUST NOT by itself pin the upstream stream transport". The bypass trigger was unchanged | #2363: image threads pinned to upstream HTTP |
| 2026-09-26 | this change | Bypass trigger narrowed: external URL anywhere, any image in the current turn, or `image_generation` | Replayed history keeps a session off the bridge permanently; #2425 |

Each step narrowed or clarified the clause. None reversed an earlier one. The
bypass trigger itself has changed only once, in this change.

## Conflicts with sibling requirements

- **Active change `narrow-input-image-upstream-transport-pin`** (landed as
  `cb21daaed`, never archived). It carries a `MODIFIED` delta of this same
  requirement with the old "any `input_image` part" trigger. Archiving it after
  this change would sync the old text back over the new text: the same
  flip-back mechanism as the hamburger regression. See tasks 3.1.
- The precedence item 2 and external-URL scenarios in `Requirement:
  Downstream-HTTP upstream transport follows a configurable policy` govern the
  *upstream transport*, not the bridge. They agree with this change and stay
  unchanged.
- `docs/routing.md` says "image-capable requests … also bypass the bridge". Task
  1.4 updates that sentence.

## Example

Turn N of a full-history client. The screenshot came from a tool several turns
ago:

```json
{"model": "gpt-6-luna", "prompt_cache_key": "k", "input": [
  {"role": "user", "content": "look at the page"},
  {"type": "function_call", "call_id": "c1", "name": "screenshot", "arguments": "{}"},
  {"type": "function_call_output", "call_id": "c1",
   "output": [{"type": "input_image", "image_url": "data:image/png;base64,iVBOR..."}]},
  {"role": "assistant", "content": [{"type": "output_text", "text": "I see a form."}]},
  {"role": "user", "content": "now submit it"}
]}
```

- Before: `bypass reason=image`, raw path, and `upstream_transport='auto'` in
  the request log. This repeats on every later turn.
- After: the current turn is the final user message and holds no image, so the
  request stays on the bridge. If the tool output with the screenshot were the
  last item, that one request would bypass. The next turn would not.

## Failure modes

- A client that sends new images without any model-output item before them
  (single-shot) still bypasses. This is the #903 behavior.
- An external URL in history bypasses forever. This is intended: the upstream
  WebSocket does not accept it, and the bridge guard does not see nested URLs.
