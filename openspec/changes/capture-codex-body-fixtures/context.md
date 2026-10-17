## Context

Replaces the synthetic pair of hand-written Codex request bodies with real captures. It was written while the subscription-exhaustion overflow feature (#2123) was still shipping, to settle whether native Codex traffic is portable to a generic OpenAI-compatible provider; what it found is why that feature was withdrawn. The corpus stays as a general traffic-parity asset.

## Decisions

1. **Capture in the origin, not behind a proxy.** C2-PREFLIGHT §4 proposed running `origin_fixture.py` as a reverse origin behind the mitmproxy addon with `capture_body_mode=full`. That is not possible as written: `capture_body_mode` is an option of `scripts/traffic_analysis/mitmproxy_addon.py` rather than of `origin_fixture`, mitmproxy is not installed and its documented invocation goes through a package manager this host cannot run, and `origin_fixture._read_request_json` parses the body and discards the bytes. The origin receives plaintext HTTP on loopback, so it can persist the decoded bytes itself — no TLS, no addon, no zstd guesswork (`decode_request_body` already handles the `Feature::EnableRequestCompression` encoding).

2. **Kernel-enforced isolation over reviewed isolation, and observed rather than asserted.** `unshare --map-root-user --net` plus `ip link set lo up` works unprivileged and makes external egress impossible rather than merely unconfigured. Files created inside map back to the invoking account outside. `--no-network-namespace` exists for hosts without `unshare` and demands an explicit second flag, so the safe path is never the accidental one. The claim is then *measured*: the run asks the kernel for its namespace's interfaces (`socket.if_nameindex`, which is namespace-aware — `/sys/class/net` is not, because `unshare --net` creates no mount namespace and sysfs stays bound to the one it was mounted in) and refuses unless loopback is the only answer. The child learns it is already inside on its command line, not through an environment variable: an inherited `CODEX_BODY_CAPTURE_NETNS=1` skipped the re-exec entirely while the run still printed "loopback only" and recorded `network_namespace: true`, which is the failure mode an attestation derived from flags cannot detect.

3. **A pinned catalog is mandatory, not an optimisation.** The Codex model manager caches `/models` for 300 s and invalidates on a `client_version` mismatch, so a capture with a newer CLI than the cache always refetches from the provider's base URL. The catalog file is therefore part of the run and its digest part of the provenance. It is *not* sufficient provenance: 0.154.0 layers bundled `model_info` overrides on top of the served rows — a row with `apply_patch_tool_type: null` and `supports_search_tool: false` still yields a body containing a custom `apply_patch`, `web_search` and `tool_search` — so the CLI version is primary and the digest secondary. A throwaway `CODEX_HOME` also protects the operator's own cache, which an earlier origin-fixture run had already polluted with a probe row because cache eligibility ignores provider identity.

4. **Provenance lives in a sidecar, never in the body.** An in-body origin marker would be a top-level key no client sends, so it would both misrepresent the wire and break the byte-fidelity claim.

5. **Record what the capture shows instead of forcing green or red.** A real capture makes the synthetic pair's "portable once `custom` is declared" assumption false. The corpus states the fact that native Codex traffic is not portable; CI neither hides it nor fails on it.

6. **Two fixture roles in one directory.** The synthetic pair is *pre-strip*: it carries `client_metadata` and `stream_options` on purpose, because the projection unit tests exist to prove `strip_source_telemetry` removes them. The captured pair is *rebuilt*. `carries_client_telemetry` in provenance distinguishes them, and it is the only exemption the privacy gate grants — a bare telemetry key name. Every value-shaped finding (live UUID, workspace path, email, host identity, credential shape) applies to both roles.

7. **Validate through production's own dispatch.** A real Responses-Lite body has no `instructions` key (`ResponsesApiRequest.instructions` is skipped when empty and the Lite branch hardcodes an empty string), so `ResponsesRequest.model_validate` fails with `instructions Field required`. Production survives because `api._has_openai_responses_shape` returns true for `input`-without-`instructions` and the request validates as `openai_compat`. Any fixture-shape test must use `normalize_responses_request_payload(payload, openai_compat=_has_openai_responses_shape(payload))`, or it red-lines for the wrong reason the day a capture lands. The synthetic Lite fixture hid this by writing `"instructions": ""`.

8. **The fixture is rebuilt from a structural allowlist, not scrubbed.** Two earlier rounds kept the captured text and widened the regexes that rewrote parts of it. Both were bypassed. The second bypass was measured: `OPERATOR_PATH`'s lookbehind excluded a path sitting immediately after `:`, so `PATH=/usr/local/bin:/home/jane/.local/bin` in a `function_call_output.output` was rewritten to `PATH=/workspace/repo:/home/jane/.local/bin` — removing the one path the pattern could see and, with it, the privacy scan's own trigger, after which the scan reported a clean body carrying the operator's home directory. The same pattern was reused by the scan over bytes, so the rewriter and its backstop were blind in the same place.

   The shape was wrong, not the tuning. Every node of a captured body is now matched against a declared rule: a rule either preserves a value from a closed domain the replay-safety predicates read, or replaces it with a substitute derived from its JSON path. Free text is always replaced, so "a pattern did not match" has no path to a fixture and "the rewrite deleted the evidence" has nothing to rewrite. A node no rule describes refuses the rebuild rather than being copied or dropped — dropping would change the key set `_input_item_has_only_known_fields` validates.

   The Lite `additional_tools` bundle and the top-level `tools` array are no longer byte-preserved. They were, on the argument that they are Codex-generated; an MCP server the operator runs supplies its own descriptions to the same array, which is exactly how operator text reaches a fixture that no rewriter touches. What survives is what the predicates read: the namespace, the declaration types, the field sets, the tool names Codex itself uses. Codex's `spawn_agent` description, which documents agent task namespaces as `/root/task1`, is synthesised away rather than mangled, so the `/root/taskN` exemption now exists only for the residual scan.

   The residual patterns moved wholesale into `fixture_privacy_scan`, so no pattern both rewrites a captured body and judges it, and the scan reads the captured body as well as the rebuilt one: a kind that vanishes between them is evidence about the rebuild, never a pass.

10. **The boundary is human diff review, and the tooling says so.** No scan can tell an operator that a fixture is the one they meant to commit. Writing into `tests/fixtures/codex_bodies/` requires `--i-have-read-the-sanitised-body`; the allowlist's job is to make the reading short enough that it actually happens.

9. **Input-item ids are replaced, not deleted, by default.** A non-empty prefixed id is precisely what makes `responses_input_items_are_self_contained_fresh_replay` return false, so deleting it would erase the evidence for the third non-portability cause. `strip_item_ids=True` produces the stripped variant when a future fixture needs one.

## What the captures establish

The captured Codex 0.154.0 `gpt-5.5` body is not portable to a generic
OpenAI-compatible provider, whatever tool types the provider declares. Three
independent causes, each pinned to source at capture time:

1. The real declaration is `{"type": "tool_search", "execution": "client", "description": "…"}`; a stateless declaration would have to be `{description, type}` alone. Declaring the type cannot help.
2. The real `web_search` declaration carries `external_web_access` and `search_content_types`, outside `replay_safety._ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["web_search"]`.
3. Every input item carries a prefixed `id`, and `responses_input_items_are_self_contained_fresh_replay` rejects any non-empty item id.

Removing all three by hand from the captured body makes it portable, which is how the causes were isolated. The Lite capture adds the `reasoning.context` / `additional_tools` namespace as a fourth, and shows that the real bundle declares the `functions` and `collaboration` namespaces with no `web` or `image_gen` in this lane, behind four developer prefix messages rather than one.

A later 60-body sweep over both transports, seven model slugs and five prompt classes found 0 portable bodies and no clause that is ever the sole blocker; the subscription-exhaustion overflow feature was withdrawn as a result (#2123).

## Limitations recorded rather than fixed

- The lane is uncredentialed. A ChatGPT-authenticated capture would add account headers, possibly `service_tier` and `access_programs`, the real upstream catalog and the operator's real skills — none of which the body-level gate reads, at the cost of a real upstream request and quota.
- A committed fixture records shape, not prose. Codex's base instructions, its tool descriptions and its JSON Schema property names are not in the corpus; the divergence table in the corpus README is where what Codex sends is written down. No gate assertion reads any of them, and the gate asserts the recorded skeleton is unchanged by the rebuild.
- The residual scan is incomplete by construction. An operator path arriving `\uXXXX`-escaped or base64-wrapped is invisible to it — both planted and measured — and it passed the *captured* body in each case. It is worth running as a second opinion on a hand-edited fixture; it is not what makes a capture committable.
- `$ref`/`$defs` in a tool's JSON Schema refuse the rebuild rather than being rewritten, because a cross-document reference cannot be renamed alongside the property names it points at. A capture carrying one needs the rule table extended.
- The residual scan covers JSON bodies; prose and `provenance.json` are human-reviewed, while credential shapes are rejected in every file.
