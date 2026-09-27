## Context

Production request `d6e68849-c632-43b8-be22-2e932b83feff` was rejected on the native Codex Responses route before source dispatch. Disabling four newly added sources restored successful requests on the original source. Request archives were disabled, so the exact original body is unavailable. A synthetic request captured from installed Codex CLI 0.157.1 using the current source model catalog reproduces two independent blockers: IDs on inline user messages, and web search `search_content_types: ["text", "image"]`. The reported client is Codex Desktop 0.158.0-alpha.2.1; the reproduction establishes compatible failure modes without claiming to have captured that client's original request.

## Goals / Non-Goals

**Goals:** Keep valid client-authored messages portable when source count grows; keep continuations with known encrypted/response ownership on the correct source across replicas; admit validated direct-source search controls.

**Non-Goals:** Infer unknown opaque owners from source age or name, route encrypted context to arbitrary credentials, alter subscription replay, change token identity, modify production source enablement, or change the database schema.

## Decisions

1. Add one strict client-message predicate shared by direct-source reference extraction and direct-source portability classification. Require a user/system/developer role, a nonblank string ID, and a message body that passes the existing account-neutral validator after removing only `id`. Assistant output IDs remain upstream references. This prevents the selector and classifier from disagreeing.
2. Use classification copies. Keep actual source requests, message IDs, and web search controls byte-for-byte equivalent at the JSON field level. Do not hide compatibility failures by stripping client data in the wire payload.
3. Normalize only validated `search_content_types` in the direct-source classification view. Leave malformed/unknown values in place so the closed validator declines them. Existing unknown web-search fields and references remain nonportable.
4. Test the public Responses routes with five credential fixtures and an independent application instance sharing persisted ownership. Test both canonical routes and trailing slashes, forwarded payload equality, and negative ownership cases.

Follow-up review found that the shared account-neutral validator also admits full `additional_tools` bundles. Checking only the role before invoking it would classify a developer-role bundle ID as a message ID. Require the item type to be absent or exactly `message` before applying the ID allowance. Keep IDs on non-message items authoritative even when their content is otherwise self-contained.

## Risks / Trade-offs

- Overbroad message classification could hide upstream references. Reuse the closed account-neutral message validator and retain all assistant, opaque, reference-only, and malformed messages.
- Older successful requests may have ownership entries for client message IDs. These entries represent client-authored content, not credential-owned state; ignoring only proven self-contained client IDs is deliberate. Other references in the same request still constrain selection.
- Newer clients may add further fields. Unknown fields remain fail-closed rather than being implicitly accepted.
- Original production payload was not retained. Validation must report the captured-client reproduction and limits honestly; source re-enablement requires a later rollout/operational step.
