"""The fold state machine: collapse N upstream rounds into ONE downstream response.

Reasoning streams to the agent live; the (possibly truncated) message / tool
calls of each round are buffered as *tentative output* and either discarded
(round was truncated → continue) or flushed (round finished cleanly). The
synthetic continue machinery never reaches the agent. Truncation is the SOLE
gate — message and function_call output are treated identically.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator, Iterator

import httpx

from .codex import (
    build_round_payload,
    commentary_message,
    continue_pair,
    is_truncation_pattern,
    reasoning_tokens,
    should_continue,
    tier_n,
)
from .config import Config
from .sse import DONE, incremental_sse, serialize_done, serialize_event

log = logging.getLogger("middleware.proxy")

_TERMINAL = ("response.completed", "response.failed", "response.incomplete")
_USAGE_TOP = ("input_tokens", "output_tokens", "total_tokens")


async def open_round(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    """Open a streaming upstream request (caller must aclose the response).

    Sends the body as raw JSON bytes (content=, not json=) so httpx does not
    invent a Content-Type — it comes from the passed-through agent headers.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = client.build_request("POST", url, content=body, headers=headers, timeout=None)
    return await client.send(req, stream=True)


async def open_passthrough(
    client: httpx.AsyncClient,
    url: str,
    raw_body: bytes,
    headers: dict[str, str],
) -> httpx.Response:
    """Open a streaming upstream request forwarding the raw body unchanged."""
    req = client.build_request("POST", url, content=raw_body, headers=headers, timeout=None)
    return await client.send(req, stream=True)


async def _tee(aiter: AsyncIterator[bytes], dump_path: Path) -> AsyncIterator[bytes]:
    """Pass bytes through while copying them to a per-round dump file."""
    f = dump_path.open("wb")
    try:
        async for chunk in aiter:
            f.write(chunk)
            yield chunk
    finally:
        f.close()


def _sum_usage(acc: dict[str, Any], usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    for k in _USAGE_TOP:
        if usage.get(k) is not None:
            acc[k] = acc.get(k, 0) + int(usage[k])
    itd = usage.get("input_tokens_details") or {}
    if itd.get("cached_tokens") is not None:
        d = acc.setdefault("input_tokens_details", {})
        d["cached_tokens"] = d.get("cached_tokens", 0) + int(itd["cached_tokens"])
    otd = usage.get("output_tokens_details") or {}
    if otd.get("reasoning_tokens") is not None:
        d = acc.setdefault("output_tokens_details", {})
        d["reasoning_tokens"] = d.get("reasoning_tokens", 0) + int(otd["reasoning_tokens"])


def _fmt_usage(usage: dict[str, Any] | None) -> str:
    u = usage or {}
    itd = u.get("input_tokens_details") or {}
    otd = u.get("output_tokens_details") or {}
    return (
        f"in={u.get('input_tokens')} cached={itd.get('cached_tokens')} "
        f"out={u.get('output_tokens')} reason={otd.get('reasoning_tokens')} "
        f"total={u.get('total_tokens')}"
    )


def _find_buffer(buf: list[dict[str, Any]], up_oi: Any) -> dict[str, Any] | None:
    for entry in buf:
        if entry["oi"] == up_oi:
            return entry
    return None


class _Seq:
    """Monotonic downstream sequence_number counter."""

    def __init__(self) -> None:
        self.n = 0

    def __call__(self) -> int:
        s = self.n
        self.n += 1
        return s


def _flush_entry(
    entry: dict[str, Any], ds_oi: int, seq: _Seq, cfg: Config
) -> Iterator[bytes]:
    """Emit a buffered (message | function_call) item's events downstream,
    rewriting output_index/sequence_number. Optionally re-chunk message text."""
    events: list[dict[str, Any]] = entry["events"]
    rechunk = cfg.stream.rechunk_final_answer and entry["itype"] == "message"

    if not rechunk:
        for ev in events:
            if "output_index" in ev:
                ev["output_index"] = ds_oi
            ev["sequence_number"] = seq()
            yield serialize_event(ev)
        return

    # Re-chunk: replace the original output_text.delta run with uniform slices.
    full_text = "".join(
        e.get("delta", "") for e in events if e.get("type") == "response.output_text.delta"
    )
    emitted = False
    for ev in events:
        if ev.get("type") == "response.output_text.delta":
            if not emitted:
                item_id = ev.get("item_id")
                content_index = ev.get("content_index", 0)
                size = max(1, cfg.stream.rechunk_size)
                for i in range(0, len(full_text), size):
                    yield serialize_event(
                        {
                            "type": "response.output_text.delta",
                            "item_id": item_id,
                            "output_index": ds_oi,
                            "content_index": content_index,
                            "delta": full_text[i : i + size],
                            "sequence_number": seq(),
                        }
                    )
                emitted = True
            continue  # drop original delta
        if "output_index" in ev:
            ev["output_index"] = ds_oi
        ev["sequence_number"] = seq()
        yield serialize_event(ev)


def _commentary_events(item: dict[str, Any], ds_oi: int, seq: _Seq) -> Iterator[bytes]:
    """Emit a synthetic phase:"commentary" message item downstream (forward_marker),
    so the agent records it and echoes it back next turn. Full item lifecycle with
    downstream-owned output_index / sequence_number."""
    iid = item["id"]
    text = item["content"][0]["text"]
    head = {"id": iid, "type": "message", "role": "assistant", "phase": "commentary"}
    evs = [
        {"type": "response.output_item.added", "output_index": ds_oi, "item": head},
        {"type": "response.content_part.added", "output_index": ds_oi, "item_id": iid,
         "content_index": 0, "part": {"type": "output_text", "text": ""}},
        {"type": "response.output_text.delta", "output_index": ds_oi, "item_id": iid,
         "content_index": 0, "delta": text},
        {"type": "response.output_text.done", "output_index": ds_oi, "item_id": iid,
         "content_index": 0, "text": text},
        {"type": "response.content_part.done", "output_index": ds_oi, "item_id": iid,
         "content_index": 0, "part": {"type": "output_text", "text": text}},
        {"type": "response.output_item.done", "output_index": ds_oi, "item": item},
    ]
    for ev in evs:
        ev["sequence_number"] = seq()
        yield serialize_event(ev)


def _agent_usage(
    first: dict[str, Any] | None,
    total: dict[str, Any] | None,
    final_round: dict[str, Any] | None,
    flushed_final: bool,
) -> dict[str, Any]:
    """Usage as if the fold were ONE response, for the downstream agent.

    input/cached = round 1 (what the agent actually sent — NOT summed across our
    hidden rounds, which would falsely look like a blown context window).
    reasoning    = summed (every round's reasoning was forwarded).
    output       = reasoning + the final flushed round's non-reasoning part
                   (message/tool the agent actually received; discarded truncated
                   messages are excluded). 0 final part if nothing was flushed.
    total        = input + output (recomputed).
    """
    first = first or {}
    total = total or {}
    in_tok = first.get("input_tokens") or 0
    cached = (first.get("input_tokens_details") or {}).get("cached_tokens")
    reasoning = (total.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
    final_nonreason = 0
    if flushed_final and final_round:
        fo = final_round.get("output_tokens") or 0
        fr = (final_round.get("output_tokens_details") or {}).get("reasoning_tokens") or 0
        final_nonreason = max(0, fo - fr)
    out_tok = reasoning + final_nonreason
    usage: dict[str, Any] = {
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "total_tokens": in_tok + out_tok,
        "output_tokens_details": {"reasoning_tokens": reasoning},
    }
    if cached is not None:
        usage["input_tokens_details"] = {"cached_tokens": cached}
    return usage


def _with_proxy_metadata(
    resp: dict[str, Any],
    rounds: list[dict[str, Any]],
    stopped_reason: str | None,
    billed_usage: dict[str, Any] | None,
) -> None:
    md = dict(resp.get("metadata") or {})
    md["proxy_rounds"] = rounds
    if billed_usage:
        md["proxy_billed_usage"] = billed_usage  # true summed cost across rounds
    if stopped_reason:
        md["proxy_stopped_reason"] = stopped_reason
    resp["metadata"] = md


def _reconstruct_terminal(
    terminal: dict[str, Any] | None,
    base_response: dict[str, Any] | None,
    output_items: list[dict[str, Any]],
    usage: dict[str, Any],
    seq: int,
    rounds: list[dict[str, Any]],
    stopped_reason: str | None,
    billed_usage: dict[str, Any] | None,
) -> dict[str, Any]:
    """Final downstream terminal event: keep round-1's response identity (id /
    created_at, matching the `created` we forwarded), take status from the
    upstream terminal, and supply our reconstructed `output` (upstream's is
    empty) + single-response `usage` + proxy metadata (billed usage included)."""
    tresp = (terminal or {}).get("response") or {}
    resp = dict(base_response or tresp)
    resp["output"] = output_items
    if usage:
        resp["usage"] = usage
    resp["status"] = tresp.get("status", "completed")
    if "incomplete_details" in tresp:
        resp["incomplete_details"] = tresp["incomplete_details"]
    _with_proxy_metadata(resp, rounds, stopped_reason, billed_usage)
    ttype = (terminal or {}).get("type", "response.completed")
    return {"type": ttype, "response": resp, "sequence_number": seq}


def _synthetic_incomplete(
    base_response: dict[str, Any] | None,
    output_items: list[dict[str, Any]],
    usage: dict[str, Any],
    seq: int,
    reason: str,
    rounds: list[dict[str, Any]],
    billed_usage: dict[str, Any] | None,
) -> dict[str, Any]:
    resp = dict(base_response or {})
    resp["output"] = output_items
    if usage:
        resp["usage"] = usage
    resp["status"] = "incomplete"
    resp["incomplete_details"] = {"reason": reason}
    _with_proxy_metadata(resp, rounds, reason, billed_usage)
    return {"type": "response.incomplete", "response": resp, "sequence_number": seq}


async def fold_stream(
    client: httpx.AsyncClient,
    cfg: Config,
    base_body: dict[str, Any],
    headers: dict[str, str],
    first_response: httpx.Response,
    id_store: Any | None = None,
    url: str | None = None,
) -> AsyncIterator[bytes]:
    """Yield the folded downstream SSE byte stream. `first_response` is the
    already-opened (2xx) round-1 upstream response; later rounds are opened here
    against `url` (the resolved upstream, which may come from the
    Responses-API-Base header). `id_store` records continuation reasoning ids
    when repair_followup="stateful".
    """
    cont = cfg.cont
    url = url or cfg.upstream.url
    orig_input = list(base_body.get("input") or [])

    seq = _Seq()
    ds_oi = 0
    base_response: dict[str, Any] | None = None
    saw_done = False
    final_output: list[dict[str, Any]] = []  # reasoning (all rounds) + final flushed items
    total_usage: dict[str, Any] = {}  # summed across rounds → metadata.proxy_billed_usage
    first_usage: dict[str, Any] | None = None  # round-1 usage → agent-facing input/cached
    replay_tail: list[Any] = []
    rounds_info: list[dict[str, Any]] = []  # per-round breakdown → metadata.proxy_rounds

    response = first_response
    round_no = 0

    try:
        while True:
            round_no += 1
            oi_map: dict[Any, int] = {}
            item_kind: dict[Any, str] = {}
            out_buffer: list[dict[str, Any]] = []
            round_reasoning: list[dict[str, Any]] = []
            terminal: dict[str, Any] | None = None
            usage: dict[str, Any] | None = None

            byte_src = response.aiter_bytes()
            if cfg.log.dump_rounds_dir:
                dump_dir = Path(cfg.log.dump_rounds_dir)
                dump_dir.mkdir(parents=True, exist_ok=True)
                byte_src = _tee(byte_src, dump_dir / f"codex_mw_r{round_no}.sse.txt")

            async for ev in incremental_sse(byte_src):
                if ev is DONE:
                    saw_done = True
                    continue
                if not isinstance(ev, dict):
                    continue
                t = ev.get("type", "")

                # Lifecycle: emit one created+in_progress (round 1), swallow the rest.
                if t in ("response.created", "response.in_progress"):
                    if round_no == 1:
                        if t == "response.created":
                            base_response = ev.get("response") or {}
                        ev["sequence_number"] = seq()
                        yield serialize_event(ev)
                    continue

                if t in _TERMINAL:
                    terminal = ev
                    usage = (ev.get("response") or {}).get("usage")
                    break

                up_oi = ev.get("output_index")

                if t == "response.output_item.added":
                    item = ev.get("item") or {}
                    if item.get("type") == "reasoning":
                        item_kind[up_oi] = "reasoning"
                        oi_map[up_oi] = ds_oi
                        ev["output_index"] = ds_oi
                        ds_oi += 1
                        ev["sequence_number"] = seq()
                        yield serialize_event(ev)
                    else:  # message | function_call → buffer (tentative output)
                        item_kind[up_oi] = "buffered"
                        out_buffer.append(
                            {"oi": up_oi, "itype": item.get("type"), "events": [ev], "item": item}
                        )
                    continue

                kind = item_kind.get(up_oi)
                if kind == "reasoning":
                    if up_oi in oi_map:
                        ev["output_index"] = oi_map[up_oi]
                    ev["sequence_number"] = seq()
                    if t == "response.output_item.done":
                        ritem = ev.get("item") or {}
                        round_reasoning.append(ritem)
                        final_output.append(ritem)
                    yield serialize_event(ev)
                elif kind == "buffered":
                    entry = _find_buffer(out_buffer, up_oi)
                    if entry is not None:
                        entry["events"].append(ev)
                        if t == "response.output_item.done":
                            entry["item"] = ev.get("item") or entry["item"]
                else:
                    # Item-scoped event with no preceding `added` we tracked; forward best-effort.
                    ev["sequence_number"] = seq()
                    yield serialize_event(ev)

            # --- round ended: decide -------------------------------------
            saw_terminal = terminal is not None
            _sum_usage(total_usage, usage)
            if round_no == 1:
                first_usage = usage
            rt = reasoning_tokens(usage)
            n = tier_n(rt, cont.truncation_step)
            rounds_info.append({"round": round_no, "reasoning_tokens": rt, "n": n})
            has_enc = bool(round_reasoning and round_reasoning[-1].get("encrypted_content"))
            within_caps = cont.max_total_output_tokens == 0 or (
                total_usage.get("output_tokens", 0) < cont.max_total_output_tokens
            )
            do_continue = (
                cont.enabled
                and saw_terminal
                and should_continue(rt, min_n=cont.min_n, max_n=cont.max_n, step=cont.truncation_step)
                and has_enc
                and round_no <= cont.max_continue
                and within_caps
            )

            # If we stop while STILL on the 518n-2 pattern, which guard fired (#5).
            stopped_reason = None
            if not do_continue and is_truncation_pattern(rt, cont.truncation_step):
                if not has_enc:
                    stopped_reason = "no_encrypted_content"
                elif round_no > cont.max_continue:
                    stopped_reason = "max_continue"
                elif not within_caps:
                    stopped_reason = "max_total_output_tokens"
                else:
                    stopped_reason = "tier_out_of_window"

            buffered = [e["itype"] for e in out_buffer]
            decision = (
                "continue" if do_continue
                else "upstream_eof" if not saw_terminal
                else stopped_reason or "clean"
            )
            log.info("round %d: %s | n=%s buffered=%s -> %s",
                     round_no, _fmt_usage(usage), n, buffered or "[]", decision)

            await response.aclose()

            if do_continue:
                last_id = round_reasoning[-1].get("id") or ""
                if cont.method == "commentary":
                    marker_items = [commentary_message(cont.marker_text)]
                else:  # tool_pair (legacy)
                    if cont.repair_followup == "stateful" and id_store is not None and last_id:
                        id_store.add(last_id)  # record id for cross-turn re-insertion
                    call, output = continue_pair(
                        last_id,
                        tool_name=cont.continue_tool_name,
                        output_text=cont.continue_output_text,
                    )
                    marker_items = [call, output]
                replay_tail.extend([*round_reasoning, *marker_items])

                # forward_marker (commentary only): surface the marker downstream so the
                # agent echoes it back next turn. A phase:"commentary" message is safe to
                # expose; a synthetic tool call is not, so tool_pair never forwards.
                if cont.method == "commentary" and cont.forward_marker:
                    fwd_item = {
                        "id": f"msg_continue_{round_no}",
                        "type": "message",
                        "status": "completed",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": cont.marker_text}],
                        "phase": "commentary",
                    }
                    for chunk in _commentary_events(fwd_item, ds_oi, seq):
                        yield chunk
                    ds_oi += 1
                    final_output.append(fwd_item)

                payload = build_round_payload(
                    base_body,
                    input_items=orig_input + replay_tail,
                    force_include_encrypted=cfg.stream.force_include_encrypted,
                    drop_previous_response_id=True,
                )
                response = await open_round(client, url, payload, headers)
                if response.status_code >= 400:
                    body = (await response.aread())[:2000]
                    await response.aclose()
                    log.warning("continuation round %d failed: %s %s", round_no + 1,
                                response.status_code, body)
                    log.info("done: %d round(s) | %s | status=incomplete stop=upstream_error",
                             round_no, _fmt_usage(total_usage))
                    yield serialize_event(
                        _synthetic_incomplete(
                            base_response, final_output,
                            _agent_usage(first_usage, total_usage, usage, flushed_final=False),
                            seq(), "upstream_error", rounds_info, total_usage)
                    )
                    return
                continue

            # --- stop ----------------------------------------------------
            if not saw_terminal:
                # Upstream closed without a terminal event. Do NOT flush the
                # buffered tentative output (message / tool calls) — it is not a
                # real final answer. Keep only the reasoning already live-streamed
                # and mark the response incomplete (#7).
                log.warning("round %d: upstream EOF with no terminal event", round_no)
                log.info("done: %d round(s) | %s | status=incomplete stop=upstream_eof",
                         round_no, _fmt_usage(total_usage))
                yield serialize_event(
                    _synthetic_incomplete(
                        base_response, final_output,
                        _agent_usage(first_usage, total_usage, usage, flushed_final=False),
                        seq(), "upstream_eof", rounds_info, total_usage)
                )
                return

            # Clean finish: flush this round's tentative output as the real answer.
            for entry in out_buffer:
                for chunk in _flush_entry(entry, ds_oi, seq, cfg):
                    yield chunk
                ds_oi += 1
                final_output.append(entry["item"])

            status = ((terminal or {}).get("response") or {}).get("status", "completed")
            log.info("done: %d round(s) | %s | status=%s stop=%s",
                     round_no, _fmt_usage(total_usage), status, stopped_reason or "natural")
            yield serialize_event(
                _reconstruct_terminal(
                    terminal, base_response, final_output,
                    _agent_usage(first_usage, total_usage, usage, flushed_final=True),
                    seq(), rounds_info, stopped_reason, total_usage)
            )
            if saw_done:
                yield serialize_done()
            return

    except (httpx.HTTPError, ConnectionError) as exc:
        log.warning("upstream error mid-stream (round %d): %r", round_no, exc)
        log.info("done: %d round(s) | %s | status=incomplete stop=upstream_error",
                 round_no, _fmt_usage(total_usage))
        yield serialize_event(
            _synthetic_incomplete(
                base_response, final_output,
                _agent_usage(first_usage, total_usage, None, flushed_final=False),
                seq(), "upstream_error", rounds_info, total_usage)
        )
        return
    finally:
        try:
            await response.aclose()
        except Exception:
            pass
