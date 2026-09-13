# Codex request-body fixtures (provider portability)

The corpus the provider-portability gate classifies. It backs issue #2123
(subscription-exhaustion overflow to a designated Model Source) and closes
design v3 §16 item (i): "captured bodies replace the synthetic pair".

Normative contracts:
[`openspec/specs/compatibility-tooling`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/compatibility-tooling)
(the capture lane and its refusals) and
[`openspec/specs/model-source-routing`](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-source-routing)
(what the corpus must record and what the gate must assert). Operator
walkthrough: [`docs/traffic-parity.md`](../../../docs/traffic-parity.md).

`provenance.json` is the machine-readable source of truth. The table below is
rendered from the same facts, and `tests/unit/test_codex_body_fixtures.py` pins
the file name, origin, slug, transport, capture date and CLI version columns
against it and against the files on disk, in both directions. The
"Sanitisation" column is prose and is reviewed by a human.

## Corpus

| File | Origin | Slug | Transport | Captured (UTC) | CLI version | Sanitisation |
|---|---|---|---|---|---|---|
| `captured_gpt55_standard_http.json` | captured | `gpt-5.5` | http | 2026-09-11 | codex-cli 0.154.0 | rebuilt from the structural allowlist: telemetry dropped, identifiers placeholdered, every free-text field synthesised |
| `captured_gpt56sol_lite_http.json` | captured | `gpt-5.6-sol` | http | 2026-09-11 | codex-cli 0.154.0 | rebuilt from the structural allowlist: telemetry dropped, identifiers placeholdered, every free-text field synthesised |
| `gpt55_standard_first_turn.json` | synthetic | `gpt-5.5` | http | — | — | none (pre-strip fixture) |
| `gpt56_lite_bundle.json` | synthetic | `gpt-5.6-sol` | http | — | — | none (pre-strip fixture) |

Two roles live side by side, distinguished by `carries_client_telemetry`:

* **Pre-strip** (`true`, the synthetic pair) — keeps the Codex telemetry on
  purpose, because its job is to feed `strip_source_telemetry`. The privacy
  gate exempts *only* the bare telemetry key names for these two; a live UUID,
  a workspace path, an email address or a credential shape is still rejected.
* **Rebuilt** (`false`, the captured pair) — a committable capture. Every
  privacy pass applies, unexempted.

## What a captured fixture is, and is not

A captured fixture is **not a transcript**. It is the *shape* of a real Codex
0.154.0 request, rebuilt field by field from a structural allowlist
(`scripts/traffic_analysis/codex_body_sanitize.py`). Every *answer* the
portability verdict gives survives; not every input to it does, because much of
what the verdict reads is a predicate rather than a value — `_is_nonblank_string`
on a tool `name`, a `description`, an `output`, an `operation.path`. Those become
placeholders that are equally non-blank. A few things survive that the verdict
does not read at all — Codex's own tool names, `content_item_kinds`, the model
slug — kept for readability and provenance. The preserved list is closed:

* object key names, from the rule table;
* discriminator values — `type`, `role`, `status`, `phase`, `effort`,
  `summary`, `context`, `verbosity`, `truncation`, `service_tier`,
  `prompt_cache_retention`, `detail`, `execution`, `search_content_types`,
  `search_context_size`, `include` entries, a string `tool_choice`, a custom
  tool's `format.type`/`syntax`, a web-search `user_location.type`, a `caller`
  type, JSON Schema `type`;
* JSON Schema keyword names, and the eight account-scoped reference property
  names `replay_safety` keys on — `container_id`, `encrypted_content`,
  `file_id`, `file_ids`, `file_url`, `image_url`, `vector_store_id`,
  `vector_store_ids` — in `properties` and in the `required` list that names
  them;
* booleans, `null`, and numbers — bounded: a preserved number must satisfy
  `|v| <= 2^31` with at most 4 decimal places (anything else refuses the
  rebuild), and every number inside a tool's JSON Schema is replaced with `0`,
  because an arbitrary-precision integer is an arbitrary-bandwidth channel that
  no walk over *strings* can see and no schema number is read by the verdict;
* the model slug, in slug shape — and, because a shape is not a closed domain,
  pinned twice by the gate: the committed body's `model` must equal the slug
  `provenance.json` records, and a captured fixture's slug must be one the
  committed reference catalog serves;
* Codex's own tool names and `content_item_kinds`, from an enumerated list;
* cardinality — how many items, how many content parts, how many properties —
  and whether an identifier was present and non-empty.

Everything else is **replaced, not scrubbed**. `instructions`, message content
text, a `function_call`'s `arguments`, a `function_call_output`'s `output`, a
tool `description`, a JSON Schema property name, a `title`, a `pattern`, an
`enum` value, a `metadata` key, an `apply_patch` `operation.path`, a URL, and
every identifier — each holds a placeholder derived from its own JSON path, such
as `[synthetic input[1].content[0].text]`. What a replaced string leaves behind
is small, and worth naming rather than rounding to nothing:

* whether it was blank, because `_is_nonblank_string` is what the replay
  predicate reads;
* for a URL, its scheme (`data:`/`http:`/`https:` each map to their own
  synthetic address), because the scheme is what decides account-neutrality;
* for an identifier, which other identifiers it equalled — a tool call and its
  output still pair, which is the point;
* for a `metadata` key or a JSON Schema property name, its alphabetical rank
  among its siblings, because the numbering follows sorted order so that a
  rebuild of a rebuild is byte-identical.

A value no rule describes — an unknown field at any depth, an unknown item or
tool type, a discriminator outside its vocabulary, an unreviewed JSON Schema
keyword — **refuses** the rebuild and names the path (not the key: a refusal is
pasted into an issue far more readily than a fixture is). A key is dropped only
by an explicit rule and always with a redaction entry — the Codex telemetry
fields, the websocket frame envelope, the Codex stream option, `stream_options`
itself once that removal empties it, and item ids under `--strip-item-ids`.
Anything else that went missing would change the key set the replay predicate
validates, which is why it refuses instead.

The predecessor kept the captured text and rewrote the substrings a set of
regexes recognised. It was bypassed twice. The second bypass was measured: a
`function_call_output` carrying
`PATH=/usr/local/bin:/home/jane/.local/bin` was rewritten to
`PATH=/workspace/repo:/home/jane/.local/bin` — the rewrite removed the *first*
path, which was the only one the gate's pattern could see, and the gate then
passed the body with the operator's home directory in it. Nothing rewrites a
captured string now, and a captured string only reaches the fixture from one of
the closed domains listed above, so neither "a pattern did not match" nor "the
rewrite deleted the evidence" has anywhere to happen. The CLI checks that on
every rebuild: it walks both trees and refuses to write if a captured string
outside the allowlist reached the output. That walk is a detector, not a proof —
it reads strings only, and only tokens of three characters or more in the ASCII
path/identifier alphabet.

What this costs: the corpus no longer shows Codex's own prompt text, tool
descriptions or schema property names. Nothing in the gate reads them, and the
gate asserts that the recorded verdict is identical before and after the
rebuild. For what Codex actually sends, read the divergence table below and
`docs/traffic-parity.md`, not the fixture.

## Recorded verdicts (and why they are declines)

The gate asserts the verdict `provenance.json` records, so a real body that
cannot overflow is green *and states that fact* instead of forcing a red CI or
a fake pass.

| Fixture | View | Verdict with the declared tool types |
|---|---|---|
| `captured_gpt55_standard_http.json` | admitted | `not_portable_tools` / `tool_search` |
| `captured_gpt56sol_lite_http.json` | `not_portable_lite_namespace` / `reasoning.context` | `not_portable_lite_namespace` / `additional_tools` |
| `gpt55_standard_first_turn.json` | admitted | portable |
| `gpt56_lite_bundle.json` | `not_portable_lite_namespace` / `reasoning.context` | `not_portable_lite_namespace` / `additional_tools` |

**Both captured Codex 0.154.0 bodies decline, for reasons that are structural in
`replay_safety` rather than incidental to these two captures.** The gpt-5.5 body
declines even when the source model declares `custom`, `tool_search` *and*
`web_search`, for three independent reasons:

1. The real `tool_search` declaration carries an `execution` field, and
   `replay_safety._STATELESS_TOOL_DECLARATION_FIELDS` admits exactly
   `{description, type}`. Declaring the type cannot help.
2. The real `web_search` declaration carries `external_web_access` and
   `search_content_types`, outside
   `_ACCOUNT_NEUTRAL_TOOL_DECLARATION_FIELDS["web_search"]`.
3. Every input item carries a prefixed `id` (Codex mints them deliberately),
   and `responses_input_items_are_self_contained_fresh_replay` rejects any
   non-empty item id, so `transcript_is_source_free` is false. The overflow
   decision runs before `strip_input_item_ids`, which is release-direction
   only.

These are two HTTP bodies from one CLI version through one uncredentialed lane,
so they are evidence about the mechanism rather than a census. Closing the three
gaps is production work, not fixture work: it changes `replay_safety` and the
overflow decision order. It is tracked separately, and
`add-subscription-overflow-model-source/design.md` decision 21 states the
`{type, description?}` declaration shape as a design premise that these
captures falsify.

## Where the synthetic pair diverges from Codex 0.154.0

The synthetic bodies are shape-illustrative, not byte-faithful. They are kept
because the projection unit tests exercise specific paths through them (an
admitted view, a telemetry strip that empties `stream_options`, a Lite decline)
that a real capture no longer reaches.

The **"Real 0.154.0" column describes the traffic the capture observed**, not
what the committed fixture shows, and **the gate does not assert these rows**.
What the gate asserts is the recorded portability verdict and the corpus sync;
the rows below are recorded here because they are otherwise written down
nowhere. Some are still visible in the captured pair (key presence and absence,
tool types, item counts, item ids); some are not, because the rebuild replaces
them: `<environment_context>` tag names and their text, tool `description`
strings, JSON Schema property names, titles and `enum` values, and any tool name
outside Codex's own list — four of the six `collaboration` tool names in the
Lite capture are synthesised, and only `followup_task` and `spawn_agent`
survive. The capture manifest (`body_summary`) reports the structural facts at
capture time, and `docs/traffic-parity.md` walks the lane.

| Field | Synthetic | Real 0.154.0 |
|---|---|---|
| `stream_options` | present | **absent** on both real bodies |
| `stream_options.reasoning_summary_delivery` | `sequential_cutoff` (corrected) | the enum has only that one value; `interleaved` is unemittable |
| `parallel_tool_calls` (5.5) | `true` (corrected) | `true` |
| `reasoning` (5.5) | `{effort, summary}` | `{effort}` — no `summary` |
| `tools` (5.5) | `shell`, `update_plan`, `apply_patch`, `view_image` | `exec_command`, `write_stdin`, `request_user_input`, custom `apply_patch`, `view_image`, `tool_search`, `web_search` |
| `<environment_context>` (prose; not in the rebuilt fixture) | `cwd`, `approval_policy`, `sandbox_mode`, `network_access`, `shell` | `cwd`, `shell`, `current_date`, `timezone`, `filesystem/workspace_roots/root`, `permission_profile` |
| Lite `additional_tools` | `functions(exec, wait)`, `web`, `image_gen` | `functions(exec, wait, request_user_input)`, `collaboration` (6 tools); no `web`/`image_gen` in this lane. Declaration *structure* is in the fixture; the descriptions are not |
| Lite prefix | 1 developer + 2 user | 4 developer + 2 user |
| Lite `instructions` | `""` | **absent** |
| Input-item `id` | absent (5.5) | present on every item |
| `text.verbosity` | `medium` | `low` |

The Lite `instructions` row is load-bearing for the gate: a real Lite body has
no `instructions` key, so `ResponsesRequest.model_validate` fails with
`instructions Field required`. Production survives because
`api._has_openai_responses_shape` returns true for `input`-without-
`instructions` and the request is validated as `openai_compat`. Any
fixture-shape test must therefore go through
`normalize_responses_request_payload(payload, openai_compat=_has_openai_responses_shape(payload))`.

## Capture a new body

One command. No ChatGPT credentials, no upstream contact, no quota:

```bash
uv run python -m scripts.traffic_analysis.codex_body_capture \
  --model gpt-5.5 --model gpt-5.6-sol --transport http \
  --out /mnt/scratch/tmp/codex-body-capture-$(date -u +%Y%m%d)
```

The script re-executes itself inside an unprivileged network namespace
(`unshare --map-root-user --net`), brings up loopback only, serves the pinned
catalog and a Responses lifecycle from an in-process origin, and runs
`codex exec` against it with a throwaway `CODEX_HOME` and a disposable
provider token. External egress is kernel-impossible, not merely unconfigured —
and the run proves it rather than asserting it: it asks the kernel for this
namespace's interfaces and refuses to capture if anything but loopback answers.
The observed list goes into the manifest as `network_isolation`.

`uv run` is load-bearing: the origin is FastAPI plus uvicorn and the request
decoder is `zstandard`, so a bare system interpreter fails at import — and on
many hosts `python` is not a command at all. The tools need codex-lb
*importable*, not configured: they read no settings and touch no database.

Expected output:

```text
network isolation: loopback only (observed interfaces: lo)
capture origin: http://127.0.0.1:19090/v1 (health ok)
catalog: codex-models-20260911.json sha256=de111010ad347d62a87346760fb6746bc1a490483359b8cefd66e57daa0ed576
codex: codex-cli 0.154.0
captured gpt-5.5          http 35357 B sha256=2f851811... exit=0
                 keys=client_metadata,include,input,instructions,model,...
                 tools=['custom', 'function', 'tool_search', 'web_search'] ... instructions=present
captured gpt-5.6-sol      http 42491 B sha256=03eaf127... exit=0
                 tools=None items=['additional_tools/developer', ...] instructions=ABSENT
wrote: .../{body,headers,prewarm}-*.json, manifest.json
next: uv run python -m scripts.traffic_analysis.codex_body_sanitize --in .../body-<slug>-<transport>-<stamp>.json --out .../fixture.json
      then read it, and copy it in with --i-have-read-the-sanitised-body.
```

Byte counts move between runs — the session identifier and cache key differ —
so compare the key list, the tool types and the item sequence, not the sizes.

`--transport websocket` works the same way and is verified end to end against
0.154.0. The origin serves both transports because the generated provider only
*offers* websockets and the client decides; the websocket lane primes the
context with a `generate: false` frame before sending the turn, so the capture
keeps the frame carrying the transcript as the body and writes the prewarm to a
separate `prewarm-*.json`. Keep both when capturing a Lite body: its turn frame
is ~8 KB with no `additional_tools` item, because the tool bundle travelled in
the prewarm. The artifact name and the manifest record the transport the body
arrived on, so an HTTP fallback is never reported as a websocket capture. A
websocket body is persisted verbatim with its frame envelope, which the
rebuild drops (`websocket_envelope_dropped`).

The two committed captures are HTTP, where Codex sends exactly one POST that
carries both the transcript and the tool surface — which is why they are the
corpus and the websocket lane is a tool, not a second fixture pair.

The catalog must be pinned: the Codex model manager invalidates its cache on a
`client_version` mismatch, so every run refetches `/models` from the provider's
base URL. `--catalog` defaults to
[`scripts/traffic_analysis/catalogs/codex-models-20260911.json`](../../../scripts/traffic_analysis/catalogs/README.md),
the committed catalog that produced this corpus — its SHA-256 is the
`catalog_sha256` below, and the gate pins the two against each other, so anyone
can reproduce a capture and verify the recorded digest. To capture a different
model set, pass a Codex `/models` response (the shape Codex caches as
`$CODEX_HOME/models_cache.json`) and record its digest. The catalog does not
fully determine the body — 0.154.0 layers bundled `model_info` overrides on top
of it, which is why the CLI version is the primary provenance key and the
catalog digest is secondary.

The script refuses, before starting anything, an output directory inside the
repository or under a temporary filesystem, an exported `CODEX_HOME` holding
an `auth.json`, a shell carrying `CODEX_LB_*` / `OPENAI_API_KEY` /
`OPENAI_BASE_URL` / `CHATGPT_BASE_URL` / `CODEX_ACCESS_TOKEN` /
`CODEX_API_BASE_URL` / `CODEX_SESSION_ID`, a shell carrying any outbound proxy
variable (`HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `WS_PROXY` / `WSS_PROXY`
/ `SOCKS_PROXY` / `FTP_PROXY` / `NO_PROXY`, either spelling), a non-loopback
origin, and a repository config file or `.env` passed as the catalog. It also
refuses a namespace that is not loopback-only, which can only be checked from
inside it and therefore fires in the re-executed child — still before the
capture directory, the origin or `codex` exist.

The proxy refusal is load-bearing: the Codex client routes even its loopback
POST through `HTTP_PROXY` and does not bypass `127.0.0.1`, so a proxied shell
captures nothing — and with `--no-network-namespace` it would ship the whole
request body to the proxy host instead.

## Pre-commit checklist

**The boundary is you reading the diff.** The rebuild removes the field an
operator string would have travelled in, rather than trying to recognise the
string, and the privacy scan is a second net over the result — but neither can
tell you that a committed fixture is the one you meant to commit, and neither
has any opinion about the *structure* you chose to record. Step 4 is not a
formality, and the tool asks for it by name: writing into this directory
requires `--i-have-read-the-sanitised-body`, and refuses without it. Nothing
else may be written here: `--headers-out` and `--emit-redactions` pointed at
this directory are refused outright.

1. Rebuild to a **scratch** path: `uv run python -m scripts.traffic_analysis.codex_body_sanitize --in <body> --out <scratch>/<name>.json --headers-in <headers> --headers-out <scratch>/headers.json --emit-redactions <scratch>/redactions.json`. It prints the residual-scan kinds for the captured body *and* for the rebuilt one; a kind present in the capture and absent afterwards is labelled as evidence about the rebuild, never as a clearance. It refuses to write at all if any captured string survives into the output.
2. A refusal (`field outside the reviewed allowlist`, `type outside the reviewed vocabulary`, a JSON Schema keyword it has never met) means the rule table in `codex_body_sanitize.py` has not seen this body's shape. Extend the table with a reviewer; never work around it.
3. Read the rebuilt body end to end. It is a few hundred lines; every free-text field is a `[synthetic …]` placeholder, and everything else is a key, a discriminator, a bounded number or a placeholder identifier. Anything that is none of those is something to ask about.
4. Copy it in with the acknowledgement: `uv run python -m scripts.traffic_analysis.codex_body_sanitize --in <scratch>/<name>.json --out tests/fixtures/codex_bodies/<name>.json --i-have-read-the-sanitised-body`.
5. Gate the corpus: `uv run python -m scripts.traffic_analysis.fixture_privacy_scan --root tests/fixtures/codex_bodies --strict`. It exits 0 on a pristine tree; the bodies allowed to keep bare telemetry key names are read from `provenance.json` (`carries_client_telemetry`) and printed, so no flags are needed.
6. Sanity-scan the scratch directory *without* `--strict`. The raw body, the header sidecar and the manifest all report findings by construction — that is the reminder to delete them (step 10), not a gate.
7. Add the `provenance.json` entry and the table row above: origin, slug, transport, UTC date, `codex --version`, catalog sha256, sanitisation list, expected view and expected verdict.
8. `uv run pytest -p no:cacheprovider -q tests/unit/test_codex_body_fixtures.py tests/unit/test_codex_body_sanitizer.py tests/unit/test_codex_body_capture_guards.py tests/unit/test_codex_body_capture_origin.py tests/unit/test_model_sources_projection.py tests/unit/test_replay_safety_portability.py`
9. `ruff check`, `ruff format --check`, `ty check`.
10. Delete the raw capture directory in the same session. Never commit a
   `headers-*.json` (it holds the `authorization` line even when the token was
   disposable) or a raw `body-*.json` / `prewarm-*.json`.

## Limitations

* Only an uncredentialed capture is covered. A ChatGPT-authenticated capture
  would add account headers, possibly `service_tier` and `access_programs`, the
  real upstream catalog and the operator's real skills — none of which the
  body-level gate reads, at the cost of a real upstream request and quota.
  Recorded as a deliberate v1 limitation.
* **The residual scan is a second net, and it is incomplete.** It is pattern
  matching over bytes, so it does not recognise an operator path that arrives
  `\uXXXX`-escaped, base64-wrapped, or split across fields — both of the first
  two were planted and measured, and the captured body passed the scan. What
  stops them is that the rebuild does not carry the field they arrived in.
  Treat a green scan as a second opinion on a hand-edited fixture, never as
  clearance for a capture.
* The rebuild refuses what it does not recognise, so a future Codex field, item
  type, tool type or JSON Schema keyword makes a fresh capture *unsanitisable*
  until the rule table is extended with a reviewer. `$ref`/`$defs` are
  deliberately not in the table: a cross-document reference cannot be rewritten
  alongside the property names it points at.
* One operator-chosen token is preserved: the model slug, and only in slug
  shape. The gate pins every captured fixture's slug against a slug in the
  committed reference catalog.
* JSON Schema property names are synthesised, with one exception: the eight
  account-scoped reference names above, which `replay_safety` keys on. The
  exemption is narrower than it looks —
  `_contains_account_scoped_tool_state` skips a `function` tool's own
  `parameters` at the root, so the same name moves the verdict under
  `tool_search` and does not under `function`. Measured both ways in
  `test_an_account_scoped_property_name_is_kept_where_it_moves_the_verdict`.
* Field *order* is not preserved: fixtures are written with sorted keys. No
  assertion reads ordering, and the capture runbook already says to compare the
  key list, the tool types and the item sequence rather than the bytes.
* What survives is still, in information terms, a channel: cardinality (how
  many items, content parts, properties), array order, the permutation the
  `required` list encodes, which fields were blank, and the bounded numbers
  above. That is irreducible — it is the shape the verdict is a function of —
  and it is why the boundary is a human reading the diff rather than a
  guarantee.
* The self-check that refuses to write when a captured string survives
  (`surviving_captured_strings`) walks strings only, and only tokens of three
  or more characters in the ASCII path/identifier alphabet. A two-character or
  non-ASCII survivor would be invisible to it. No current rule can produce one;
  it is recorded because the next rule might.
* Neither the rebuild nor the scan reads `socket.gethostname()` or
  `getpass.getuser()`, so the fixture a capture rebuilds to, and the verdict
  the gate reaches, are the same on every machine. The pass this replaced
  matched a bare word taken from the running box: 36 of 57 plausible host names
  red-lined the *pristine* corpus (`repo` hit all four fixtures) with no flag to
  clear the finding. The gate test tries all 56, and pairs that absence
  assertion with a planted path that must still be reported under each of the
  same 56.
* The residual scan covers `*.json` bodies. Prose (`*.md`) and
  `provenance.json` are reviewed by a human; credential shapes are still
  rejected in every file.
