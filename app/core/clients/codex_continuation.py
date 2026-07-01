from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any, cast

from app.core.types import JsonObject, JsonValue
from app.core.utils.sse import format_sse_event, parse_sse_data_json
from middleware.codex import (
    DEFAULT_TRUNCATION_STEP,
    build_round_payload,
    commentary_message,
    is_truncation_pattern,
    reasoning_enabled,
    reasoning_tokens,
    should_continue,
    tier_n,
)

logger = logging.getLogger(__name__)

_TERMINAL_EVENT_TYPES = frozenset({"response.completed", "response.failed", "response.incomplete"})

type OpenRound = Callable[[JsonObject], AsyncIterator[str]]


@dataclass(frozen=True, slots=True)
class CodexContinuationConfig:
    enabled: bool = True
    truncation_step: int = DEFAULT_TRUNCATION_STEP
    max_continue: int = 3
    min_n: int = 1
    max_n: int = 0
    marker_text: str = "Continue thinking..."
    force_include_encrypted: bool = True
    rechunk_final_answer: bool = True
    rechunk_size: int = 16
    max_total_output_tokens: int = 0


@dataclass(slots=True)
class _BufferedOutput:
    upstream_output_index: Any
    item_type: str | None
    events: list[dict[str, Any]]
    item: dict[str, Any]


class _Seq:
    def __init__(self) -> None:
        self._next = 0

    def next(self) -> int:
        value = self._next
        self._next += 1
        return value


def codex_continuation_config_from_settings(settings: object) -> CodexContinuationConfig:
    if not hasattr(settings, "codex_continuation_enabled"):
        return CodexContinuationConfig(enabled=False)
    return CodexContinuationConfig(
        enabled=bool(settings.codex_continuation_enabled),
        truncation_step=int(settings.codex_continuation_truncation_step),
        max_continue=int(settings.codex_continuation_max_continue),
        min_n=int(settings.codex_continuation_min_n),
        max_n=int(settings.codex_continuation_max_n),
        marker_text=str(settings.codex_continuation_marker_text),
        force_include_encrypted=bool(settings.codex_continuation_force_include_encrypted),
        rechunk_final_answer=bool(settings.codex_continuation_rechunk_final_answer),
        rechunk_size=int(settings.codex_continuation_rechunk_size),
        max_total_output_tokens=int(settings.codex_continuation_max_total_output_tokens),
    )


def should_apply_codex_continuation(
    payload: JsonObject,
    config: CodexContinuationConfig,
) -> bool:
    if not config.enabled:
        return False
    body = dict(cast(dict[str, Any], payload))
    if body.get("stream") is False:
        return False
    return reasoning_enabled(body)


async def fold_responses_stream_with_codex_continuation(
    *,
    base_payload: JsonObject,
    open_round: OpenRound,
    config: CodexContinuationConfig,
) -> AsyncIterator[str]:
    base_body = dict(cast(dict[str, Any], base_payload))
    original_input = _input_items(base_body.get("input"))
    next_payload = build_round_payload(
        base_body,
        input_items=original_input,
        force_include_encrypted=config.force_include_encrypted,
        drop_previous_response_id=False,
    )

    seq = _Seq()
    downstream_output_index = 0
    base_response: dict[str, Any] | None = None
    saw_done = False
    final_output: list[dict[str, Any]] = []
    total_usage: dict[str, Any] = {}
    first_usage: dict[str, Any] | None = None
    replay_tail: list[Any] = []
    rounds_info: list[dict[str, Any]] = []
    round_number = 0
    yielded_any = False

    try:
        while True:
            round_number += 1
            output_index_map: dict[Any, int] = {}
            item_kind: dict[Any, str] = {}
            buffered_outputs: list[_BufferedOutput] = []
            round_reasoning: list[dict[str, Any]] = []
            terminal: dict[str, Any] | None = None
            usage: dict[str, Any] | None = None

            round_stream = open_round(cast(JsonObject, next_payload))
            async for event_block in round_stream:
                event = _parse_event_block(event_block)
                if event is _Done:
                    saw_done = True
                    continue
                if event is None:
                    yield event_block
                    yielded_any = True
                    continue

                event_type = _event_type(event)
                if event_type in {"response.created", "response.in_progress"}:
                    if round_number == 1:
                        if event_type == "response.created":
                            base_response = _response_payload(event)
                        event["sequence_number"] = seq.next()
                        yield _format_event(event)
                        yielded_any = True
                    continue

                if event_type in _TERMINAL_EVENT_TYPES:
                    terminal = event
                    usage = _response_usage(event)
                    saw_done = await _drain_round_stream(round_stream, saw_done=saw_done)
                    break

                upstream_output_index = event.get("output_index")
                if event_type == "response.output_item.added":
                    item = _item_payload(event)
                    if item.get("type") == "reasoning":
                        item_kind[upstream_output_index] = "reasoning"
                        output_index_map[upstream_output_index] = downstream_output_index
                        event["output_index"] = downstream_output_index
                        downstream_output_index += 1
                        event["sequence_number"] = seq.next()
                        yield _format_event(event)
                        yielded_any = True
                    else:
                        item_kind[upstream_output_index] = "buffered"
                        buffered_outputs.append(
                            _BufferedOutput(
                                upstream_output_index=upstream_output_index,
                                item_type=item.get("type") if isinstance(item.get("type"), str) else None,
                                events=[event],
                                item=item,
                            )
                        )
                    continue

                kind = item_kind.get(upstream_output_index)
                if kind == "reasoning":
                    if upstream_output_index in output_index_map:
                        event["output_index"] = output_index_map[upstream_output_index]
                    event["sequence_number"] = seq.next()
                    if event_type == "response.output_item.done":
                        reasoning_item = _item_payload(event)
                        round_reasoning.append(reasoning_item)
                        final_output.append(reasoning_item)
                    yield _format_event(event)
                    yielded_any = True
                elif kind == "buffered":
                    entry = _find_buffer(buffered_outputs, upstream_output_index)
                    if entry is not None:
                        entry.events.append(event)
                        if event_type == "response.output_item.done":
                            entry.item = _item_payload(event) or entry.item
                else:
                    event["sequence_number"] = seq.next()
                    yield _format_event(event)
                    yielded_any = True

            saw_terminal = terminal is not None
            _sum_usage(total_usage, usage)
            if round_number == 1:
                first_usage = usage

            round_reasoning_tokens = reasoning_tokens(usage)
            truncation_tier = tier_n(round_reasoning_tokens, config.truncation_step)
            rounds_info.append(
                {
                    "round": round_number,
                    "reasoning_tokens": round_reasoning_tokens,
                    "n": truncation_tier,
                }
            )
            has_encrypted_content = bool(round_reasoning and round_reasoning[-1].get("encrypted_content"))
            within_output_cap = config.max_total_output_tokens == 0 or (
                _int_value(total_usage.get("output_tokens")) < config.max_total_output_tokens
            )
            should_continue_round = (
                config.enabled
                and saw_terminal
                and should_continue(
                    round_reasoning_tokens,
                    min_n=config.min_n,
                    max_n=config.max_n,
                    step=config.truncation_step,
                )
                and has_encrypted_content
                and round_number <= config.max_continue
                and within_output_cap
            )

            stopped_reason = _stopped_reason(
                should_continue_round=should_continue_round,
                reasoning_token_count=round_reasoning_tokens,
                has_encrypted_content=has_encrypted_content,
                round_number=round_number,
                within_output_cap=within_output_cap,
                config=config,
            )
            logger.debug(
                "codex_continuation_round request_round=%s reasoning_tokens=%s tier=%s decision=%s",
                round_number,
                round_reasoning_tokens,
                truncation_tier,
                "continue" if should_continue_round else stopped_reason or "stop",
            )

            if should_continue_round:
                marker = commentary_message(config.marker_text)
                replay_tail.extend([*round_reasoning, marker])
                next_payload = build_round_payload(
                    base_body,
                    input_items=[*original_input, *replay_tail],
                    force_include_encrypted=config.force_include_encrypted,
                    drop_previous_response_id=True,
                )
                continue

            if not saw_terminal:
                yield _format_event(
                    _synthetic_incomplete(
                        base_response,
                        final_output,
                        _agent_usage(first_usage, total_usage, usage, flushed_final=False),
                        seq.next(),
                        "upstream_eof",
                        rounds_info,
                        total_usage,
                    )
                )
                return

            for entry in buffered_outputs:
                for event in _flush_entry(entry, downstream_output_index, seq, config):
                    yield _format_event(event)
                    yielded_any = True
                downstream_output_index += 1
                final_output.append(entry.item)

            yield _format_event(
                _reconstruct_terminal(
                    terminal,
                    base_response,
                    final_output,
                    _agent_usage(first_usage, total_usage, usage, flushed_final=True),
                    seq.next(),
                    rounds_info,
                    stopped_reason,
                    total_usage,
                )
            )
            if saw_done:
                yield "data: [DONE]\n\n"
            return
    except asyncio.CancelledError:
        raise
    except GeneratorExit:
        raise
    except Exception:
        if round_number <= 1 and not yielded_any:
            raise
        logger.warning("codex_continuation_hidden_round_failed round=%s", round_number, exc_info=True)
        yield _format_event(
            _synthetic_incomplete(
                base_response,
                final_output,
                _agent_usage(first_usage, total_usage, None, flushed_final=False),
                seq.next(),
                "upstream_error",
                rounds_info,
                total_usage,
            )
        )


def _input_items(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if value is None:
        return []
    return [value]


class _DoneType:
    pass


_Done = _DoneType()


def _parse_event_block(event_block: str) -> dict[str, Any] | _DoneType | None:
    if _is_done_event_block(event_block):
        return _Done
    payload = parse_sse_data_json(event_block)
    if payload is None:
        return None
    return dict(cast(dict[str, Any], payload))


def _is_done_event_block(event_block: str) -> bool:
    return any(line.strip() == "data: [DONE]" for line in event_block.splitlines())


async def _drain_round_stream(round_stream: AsyncIterator[str], *, saw_done: bool) -> bool:
    try:
        async for event_block in round_stream:
            if _parse_event_block(event_block) is _Done:
                saw_done = True
    except Exception:
        logger.warning("codex_continuation_round_drain_failed", exc_info=True)
    return saw_done


def _event_type(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    return event_type if isinstance(event_type, str) else ""


def _response_payload(event: dict[str, Any]) -> dict[str, Any]:
    response = event.get("response")
    return dict(cast(dict[str, Any], response)) if isinstance(response, dict) else {}


def _response_usage(event: dict[str, Any]) -> dict[str, Any] | None:
    response = event.get("response")
    if not isinstance(response, dict):
        return None
    usage = response.get("usage")
    return dict(cast(dict[str, Any], usage)) if isinstance(usage, dict) else None


def _item_payload(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    return dict(cast(dict[str, Any], item)) if isinstance(item, dict) else {}


def _format_event(event: dict[str, Any]) -> str:
    return format_sse_event(cast(dict[str, JsonValue], event))


def _find_buffer(entries: list[_BufferedOutput], upstream_output_index: Any) -> _BufferedOutput | None:
    for entry in entries:
        if entry.upstream_output_index == upstream_output_index:
            return entry
    return None


def _int_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    return 0


def _sum_usage(accumulator: dict[str, Any], usage: dict[str, Any] | None) -> None:
    if not usage:
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            accumulator[key] = _int_value(accumulator.get(key)) + value
    input_details = usage.get("input_tokens_details")
    if isinstance(input_details, dict) and isinstance(input_details.get("cached_tokens"), int):
        details = accumulator.setdefault("input_tokens_details", {})
        if isinstance(details, dict):
            details["cached_tokens"] = _int_value(details.get("cached_tokens")) + input_details["cached_tokens"]
    output_details = usage.get("output_tokens_details")
    if isinstance(output_details, dict) and isinstance(output_details.get("reasoning_tokens"), int):
        details = accumulator.setdefault("output_tokens_details", {})
        if isinstance(details, dict):
            details["reasoning_tokens"] = (
                _int_value(details.get("reasoning_tokens")) + output_details["reasoning_tokens"]
            )


def _stopped_reason(
    *,
    should_continue_round: bool,
    reasoning_token_count: int | None,
    has_encrypted_content: bool,
    round_number: int,
    within_output_cap: bool,
    config: CodexContinuationConfig,
) -> str | None:
    if should_continue_round or not is_truncation_pattern(reasoning_token_count, config.truncation_step):
        return None
    if not has_encrypted_content:
        return "no_encrypted_content"
    if round_number > config.max_continue:
        return "max_continue"
    if not within_output_cap:
        return "max_total_output_tokens"
    return "tier_out_of_window"


def _flush_entry(
    entry: _BufferedOutput,
    downstream_output_index: int,
    seq: _Seq,
    config: CodexContinuationConfig,
) -> list[dict[str, Any]]:
    events = [dict(event) for event in entry.events]
    rechunk = config.rechunk_final_answer and entry.item_type == "message"
    if not rechunk:
        for event in events:
            if "output_index" in event:
                event["output_index"] = downstream_output_index
            event["sequence_number"] = seq.next()
        return events

    full_text = "".join(
        str(event.get("delta", "")) for event in events if event.get("type") == "response.output_text.delta"
    )
    output: list[dict[str, Any]] = []
    emitted_text = False
    for event in events:
        if event.get("type") == "response.output_text.delta":
            if not emitted_text:
                item_id = event.get("item_id")
                content_index = event.get("content_index", 0)
                size = max(1, config.rechunk_size)
                for index in range(0, len(full_text), size):
                    output.append(
                        {
                            "type": "response.output_text.delta",
                            "item_id": item_id,
                            "output_index": downstream_output_index,
                            "content_index": content_index,
                            "delta": full_text[index : index + size],
                            "sequence_number": seq.next(),
                        }
                    )
                emitted_text = True
            continue
        if "output_index" in event:
            event["output_index"] = downstream_output_index
        event["sequence_number"] = seq.next()
        output.append(event)
    return output


def _agent_usage(
    first: dict[str, Any] | None,
    total: dict[str, Any] | None,
    final_round: dict[str, Any] | None,
    *,
    flushed_final: bool,
) -> dict[str, Any]:
    first = first or {}
    total = total or {}
    input_tokens = _int_value(first.get("input_tokens"))
    cached_tokens = None
    input_details = first.get("input_tokens_details")
    if isinstance(input_details, dict) and isinstance(input_details.get("cached_tokens"), int):
        cached_tokens = input_details["cached_tokens"]
    output_details = total.get("output_tokens_details")
    reasoning = (
        output_details.get("reasoning_tokens")
        if isinstance(output_details, dict) and isinstance(output_details.get("reasoning_tokens"), int)
        else 0
    )
    final_non_reasoning = 0
    if flushed_final and final_round:
        final_output_tokens = _int_value(final_round.get("output_tokens"))
        final_output_details = final_round.get("output_tokens_details")
        final_reasoning = (
            final_output_details.get("reasoning_tokens")
            if isinstance(final_output_details, dict) and isinstance(final_output_details.get("reasoning_tokens"), int)
            else 0
        )
        final_non_reasoning = max(0, final_output_tokens - final_reasoning)
    output_tokens = reasoning + final_non_reasoning
    usage: dict[str, Any] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning},
    }
    if cached_tokens is not None:
        usage["input_tokens_details"] = {"cached_tokens": cached_tokens}
    return usage


def _with_proxy_metadata(
    response: dict[str, Any],
    rounds: list[dict[str, Any]],
    stopped_reason: str | None,
    billed_usage: dict[str, Any] | None,
) -> None:
    metadata = response.get("metadata")
    normalized_metadata = dict(cast(dict[str, Any], metadata)) if isinstance(metadata, dict) else {}
    normalized_metadata["proxy_rounds"] = rounds
    if billed_usage:
        normalized_metadata["proxy_billed_usage"] = billed_usage
    if stopped_reason:
        normalized_metadata["proxy_stopped_reason"] = stopped_reason
    response["metadata"] = normalized_metadata


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
    terminal_response = _response_payload(terminal or {})
    response = dict(base_response or terminal_response)
    response["output"] = output_items
    response["usage"] = usage
    response["status"] = terminal_response.get("status", "completed")
    if "incomplete_details" in terminal_response:
        response["incomplete_details"] = terminal_response["incomplete_details"]
    _with_proxy_metadata(response, rounds, stopped_reason, billed_usage)
    return {
        "type": _event_type(terminal or {}) or "response.completed",
        "response": response,
        "sequence_number": seq,
    }


def _synthetic_incomplete(
    base_response: dict[str, Any] | None,
    output_items: list[dict[str, Any]],
    usage: dict[str, Any],
    seq: int,
    reason: str,
    rounds: list[dict[str, Any]],
    billed_usage: dict[str, Any] | None,
) -> dict[str, Any]:
    response = dict(base_response or {})
    response["output"] = output_items
    response["usage"] = usage
    response["status"] = "incomplete"
    response["incomplete_details"] = {"reason": reason}
    _with_proxy_metadata(response, rounds, reason, billed_usage)
    return {"type": "response.incomplete", "response": response, "sequence_number": seq}
