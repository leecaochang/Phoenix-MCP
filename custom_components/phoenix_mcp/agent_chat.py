"""Agent Chat, voice and AI Task turns."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Awaitable, Callable

from aiohttp import ClientSession
from homeassistant.core import HomeAssistant

from .access_key_store import AccessKeyRecord
from .agent_recovery import recovery_turn
from .agent_turn import (
    _after_dispatch_failure, _AGENTCLI_ADDENDUM, _agentcli_time_context,
    _agentcli_time_guidance, _clip_display, _consume_model_stream,
    _conversation_behavior_prompt, _DETAIL_INSTRUCTIONS, _home_focus_output_contract,
    _kill_switch_on, _review_stop_error, _run_headless_turn, _run_tool_batch,
    _STYLE_INSTRUCTIONS, _with_time_context, HeadlessUsage, TURN_AUTHORITY_LOST, TURN_EXHAUSTED,
    TURN_OUTCOME_UNKNOWN, TURN_OUTPUT_LIMIT, TURN_PROVIDER_ERROR, TURN_TRUNCATED,
)
from .approvals import get_approval
from .const import (
    AI_TASK_CLIENT_IP, CONVERSATION_STYLE_DIRECT, DETAIL_LEVEL_CONCISE,
    HOME_COMPANION_REPORT_STATUSES, VOICE_AGENT_CLIENT_IP, VOICE_FIRST_REPLY_SECONDS,
)
from .conversation_log import flush_assistant, recorded_chat
from .data import PhoenixData
from .helpers import voice_text
from .home_companion import is_companion, internal_turn
from .home_companion_turn import async_delegate
from .llm.base import (
    _invalid_tools_error, _local_tool_results, _merged_results, _operator_message_index,
    _tool_limit_problem, _tool_loader,
)
from .llm.provider_claude import ClaudeProvider
from .llm.provider_openai import OpenAICompatProvider
from .provider_constants import (
    AGENTCLI_MAX_ITERATIONS, AGENTCLI_MAX_TOOL_CALL_CORRECTIONS, AGENTCLI_MAX_TURN_OUTPUT_BYTES,
)
from .routing.chat_routing import async_route_chat_command
from .routing.command_routing import RoutingAuthority
from .routing.routing_statistics import tracked_turn, untracked_turn, voice_result
from .routing.tool_routing import create_review_state, async_review_tools, withheld_results
from .tool_catalog import build_agent_tool_list
from .tool_dispatch import _build_instructions

_LOGGER = logging.getLogger(__name__)


@recorded_chat
@tracked_turn("chat")
@recovery_turn
async def async_run_agent_turn(
    *, hass: HomeAssistant, data: PhoenixData, access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession, messages: list[dict], options: dict,
    client_ip: str, base_url: str,
    emit: Callable[[str, dict], Awaitable[None]],
    cancel: asyncio.Event,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
    conversation_style: str = CONVERSATION_STYLE_DIRECT,
    detail_level: str = DETAIL_LEVEL_CONCISE,
    home_focused: bool = False,
    routing_language: object = None,
    account: tuple[str, str | None] | None = None,
) -> list[dict]:
    """Run one user turn to completion, streaming SSE events. Returns updated messages.

    account is the chat's provider account and model, which a Home Companion
    request runs on in place of the Voice settings.

    The loop body is three phases: drain one model call (_consume_model_stream),
    decide whether to keep the assistant message against the turn output budget,
    and run whatever tool calls it asked for (_run_tool_batch). What stays here
    is the turn-level policy the phases must not own: the runaway-iteration cap
    and its human-in-the-loop reset, the output budget, and how the turn ends.
    """
    await data.agent_recovery.reconcile(data.store, access_key.id)
    if data.agent_recovery.needs_attention(access_key.id):
        await emit("error", {"code": "outcome_unknown", "retryable": False,
                             "message": "An earlier action outcome is unknown. Check the Needs Attention window of the Phoenix MCP panel before continuing."})
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "error"})
        return messages

    if not await async_route_chat_command(hass, data, access_key, messages, routing_language, cancel,
                                           base_url=base_url, client_ip=client_ip, emit=emit):
        return messages

    if is_companion(access_key):
        request = messages[-1].get("content") if messages and messages[-1].get("role") == "user" else None
        if not isinstance(request, str) or cancel.is_set():
            await emit("error", {"code": "cancelled" if cancel.is_set() else "invalid_request",
                                 "message": voice_text(routing_language, "companion_clarify")})
            await emit("messages", {"messages": messages})
            await emit("done", {"stop_reason": "error"})
            return messages
        usage = HeadlessUsage()
        task = asyncio.create_task(async_delegate(hass, data, access_key, request, routing_language, base_url,
                                                   style=conversation_style, detail=detail_level, usage=usage,
                                                   account=account))
        stopped = asyncio.create_task(cancel.wait())
        try:
            done, _ = await asyncio.wait({task, stopped}, return_when=asyncio.FIRST_COMPLETED)
            if stopped in done:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                await emit("error", {"code": "cancelled", "message": voice_text(routing_language, "local_cancelled") + " " + voice_text(routing_language, "companion_unverified")})
            else:
                report = await task
                if (frame := usage.frame()) is not None:
                    await emit("usage", frame)
                messages.append({"role": "assistant", "content": report["message"]})
                if report["status"] in HOME_COMPANION_REPORT_STATUSES:
                    await emit("assistant_delta", {"text": report["message"]})
                for url in report["review_urls"]:
                    record = get_approval(data.store, url.rsplit("/", 1)[-1])
                    if record is not None and record.access_key_id == access_key.id:
                        await emit("approval_required", {
                            "approval_id": record.id, "review_url": url, "tool_name": record.tool_name,
                            "expires_at": record.expires_at, "diff": record.diff,
                        })
                if report["status"] not in HOME_COMPANION_REPORT_STATUSES:
                    await emit("error", {"code": report["status"], "message": report["message"], "retryable": False})
        finally:
            for waiter in (task, stopped):
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(task, stopped, return_exceptions=True)
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "end_turn"})
        return messages

    review_state = create_review_state(data, access_key, "chat")


    agent_tools = build_agent_tool_list(access_key, data, "chat")
    loader = _tool_loader(provider, agent_tools)
    system = (
        _build_instructions(access_key, data, base_url, hass)
        + _conversation_behavior_prompt(
            conversation_style, detail_level, home_focused=home_focused,
        )
        + _AGENTCLI_ADDENDUM
        + _home_focus_output_contract(home_focused)
        + (loader.directory() if loader is not None else "")
        + _agentcli_time_guidance()
    )
    # The clock goes on the operator's message in each request copy, after the
    # history the next turn re-sends, so that history stays a cached prefix.
    operator = _operator_message_index(messages)
    time_note = _agentcli_time_context(hass)
    if operator is None:
        system += time_note
    declared = loader.round_tools() if loader is not None else agent_tools
    tools = provider.format_tools(declared)
    tools_key = tuple(tool["name"] for tool in declared)
    if (too_many := _tool_limit_problem(provider, tools)) is not None:
        await emit("error", too_many)
        await emit("messages", {"messages": messages})
        await emit("done", {"stop_reason": "error"})
        return messages
    # The access key is re-resolved per dispatch (see _current_dispatch_access_key), so a
    # mid-turn revoke/expire/cap-change/kill-switch is honored before any side
    # effect. Each dispatch gets the inline-confirm wait zeroed so a confirm gate
    # returns pending immediately (snappy approval card); the loop's own longer,
    # cancel-aware wait runs afterward.
    access_key_id = access_key.id

    stop_reason = "end_turn"
    dispatch_started = False
    write_started = False
    saw_text = False
    turn_errored = False
    # Bound the TOTAL provider-generated output retained across the whole turn.
    # The per-response _sse_lines cap bounds one stream, but a full turn of
    # iterations would still let messages[] accumulate a large multiple; this shared budget
    # (measured on the retained assistant messages, which is what is held and
    # re-sent) caps the turn as a whole against a faulty/hostile provider. The
    # check happens BEFORE append (below), so the offending message is never
    # retained and the returned transcript itself stays within the budget.
    turn_output_bytes = 0
    # The safety cap bounds a RUNAWAY model (tools calling tools with no human in
    # the loop). A human approving or rejecting an action proves they are in the
    # loop, so that round resets the budget to zero rather than counting toward it.
    iterations = 0
    # Set when the loop reaches the per-turn cap: the turn ends cleanly (the
    # conversation is left resumable, ending in tool results) and a
    # continue_required checkpoint is emitted so the operator can grant another
    # N rounds. Distinct from a hard error or a completed answer.
    paused_for_continue = False
    # Provider-reported token usage across the turn's model calls. cur_* holds
    # the in-flight call's latest cumulative report (EV_USAGE is replace, not
    # add); done_* sums finished calls. The browser owns cross-turn session
    # accumulation, so the SSE event carries turn-cumulative totals plus the
    # last-known context size (the newest call's input tokens).
    usage_done_input = usage_done_output = 0
    usage_cur_input = usage_cur_output = 0
    usage_done_cache = (0, 0)
    context_tokens = 0
    # Rounds spent handing malformed tool-call JSON back to the model.
    corrections = 0
    # Model calls and tools run this turn, for the status line; unlike iterations,
    # never reset by an approval.
    step = tools_run = 0
    while True:
        if iterations >= max_iterations:
            # Pause at the checkpoint rather than stopping dead: the loop has
            # done max_iterations rounds with no human gating an action, so ask
            # the operator whether to continue. The conversation is resumable
            # (the last round appended its tool results), so a continue request
            # picks up exactly here for another N rounds.
            paused_for_continue = True
            break
        iterations += 1
        if cancel.is_set():
            await emit("error", {"code": "cancelled", "message": "Cancelled."})
            break
        step += 1
        if loader is not None:
            definitions = loader.round_tools()
            if (key := tuple(tool["name"] for tool in definitions)) != tools_key:
                tools, tools_key = provider.format_tools(definitions), key
        call = await _consume_model_stream(
            provider, session, system=system, messages=_with_time_context(messages, operator, time_note),
            tools=tools, options=options, cancel=cancel, emit=emit,
            usage_done_input=usage_done_input, usage_done_output=usage_done_output,
            context_tokens=context_tokens, usage_done_cache=usage_done_cache,
            detect_focus_refusal=home_focused, dispatch_started=dispatch_started,
            write_started=write_started, status={"step": step, "tools": tools_run},
        )
        flush_assistant()
        tool_calls = call.tool_calls
        assistant_msg = call.assistant_msg
        stop_reason = call.stop_reason
        errored = call.errored
        saw_text = saw_text or call.saw_text
        usage_cur_input, usage_cur_output = call.usage_input, call.usage_output
        context_tokens = call.context_tokens
        # Fold the finished call's usage into the turn totals (even on error:
        # a partial call still consumed the tokens it reported).
        usage_done_input += usage_cur_input
        usage_done_output += usage_cur_output
        usage_done_cache = (usage_done_cache[0] + call.usage_cache[0], usage_done_cache[1] + call.usage_cache[1])
        usage_cur_input = usage_cur_output = 0
        if errored:
            # An upstream error is already emitted to the browser. Mark the
            # turn terminal so the generic empty-reply notice cannot mislabel
            # a useful provider diagnostic, such as a billing refusal.
            turn_errored = True
            stop_reason = "error"
            break
        invalid = call.invalid_tools
        if invalid is not None:
            # Nothing from this response ran. Hand the JSON error back as tool
            # results so the model can resend the call, within a small budget.
            if corrections >= AGENTCLI_MAX_TOOL_CALL_CORRECTIONS:
                failure = _invalid_tools_error()
                if dispatch_started:
                    failure = _after_dispatch_failure(failure, write_started)
                await emit("error", {
                    "code": failure["code"], "message": failure["message"], "retryable": failure["retryable"],
                    "diagnostic": failure["diagnostic"],
                    **({"outcome": "outcome_unknown"} if failure.get("outcome") == "outcome_unknown" else {}),
                })
                turn_errored = True
                stop_reason = "error"
                break
            corrections += 1
            assistant_msg = invalid["assistant_msg"]
            stop_reason = "tool_use"
        if assistant_msg is not None:
            # Check the prospective total BEFORE appending, and reject the message
            # if it would blow the turn budget, so the offending message is never
            # retained in messages (which is returned/re-sent). The transcript thus
            # stays within the budget rather than budget + one whole response.
            prospective = turn_output_bytes + len(json.dumps(assistant_msg, default=str))
            if prospective > AGENTCLI_MAX_TURN_OUTPUT_BYTES:
                await emit("error", {"code": "output_limit", "message": (
                    "The provider produced too much output this turn; stopped.")})
                turn_errored = True
                break
            provider.append_assistant(messages, assistant_msg)
            turn_output_bytes = prospective
        if invalid is not None:
            provider.append_tool_results(messages, invalid["results"])
            await emit("notice", {"code": "bad_tool_call_retry", "message": (
                "The model sent a tool call whose arguments were not valid JSON. Nothing from that "
                "response ran; the error was returned so the model can send the call again.")})
            continue
        if stop_reason != "tool_use":
            break

        proposed = tool_calls
        slots, tool_calls = _local_tool_results(loader, proposed, data)
        for tc, slot in zip(proposed, slots):
            if slot is not None:
                await emit("tool_call", {"id": tc.get("id"), "name": slot["tool_name"], "arguments": tc.get("input") or {}})
                await emit("tool_result", {"id": tc.get("id"), "name": slot["tool_name"], "is_error": slot["is_error"],
                                           "summary": _clip_display(slot["result_text"])})
        if not tool_calls:
            # Only loads and refusals this round: nothing was dispatched.
            provider.append_tool_results(messages, _merged_results(slots, []))
            continue
        review = await async_review_tools(hass, data, review_state, surface="chat",
            language=routing_language, messages=messages, calls=tool_calls, system=system,
            remaining_rounds=max_iterations - iterations, cancel=cancel, announced_tools=agent_tools)
        if review.action != "continue":
            provider.append_tool_results(messages, _merged_results(slots, withheld_results(tool_calls, review)))
            if review.action == "stop":
                await emit("error", _review_stop_error(review.reason, data))
                turn_errored = True
                break
            continue
        dispatch_started = True
        batch = await _run_tool_batch(
            tool_calls, hass=hass, data=data, access_key_id=access_key_id,
            client_ip=client_ip, base_url=base_url, emit=emit, cancel=cancel,
        )
        results = _merged_results(slots, batch.results)
        tools_run += len(tool_calls)
        write_started = write_started or batch.write_started
        human_resolved = batch.human_resolved
        stop_turn = batch.stop_turn
        authority_lost = batch.authority_lost
        if results:
            provider.append_tool_results(messages, results)
        if batch.outcome_unknown:
            # Appended above first, so the conversation handed back to the
            # browser stays valid for the provider once the barrier is reviewed.
            await emit("error", {"code": "outcome_unknown", "retryable": False,
                                 "message": "An action outcome is unknown. Check the Needs Attention window of the Phoenix MCP panel before retrying."})
            turn_errored = True
            stop_reason = "error"
            break
        # A human approved or rejected an action this round: reset the runaway
        # guard, since a human is actively gating the loop.
        if human_resolved:
            iterations = 0
        # The access key lost authority mid-batch (revoked/expired/kill-switch): the
        # batch was completed with synthetic results (protocol-safe); end the
        # turn rather than looping into more denied dispatches.
        if authority_lost:
            # The kill switch is not the key's fault; say which one stopped the turn.
            if _kill_switch_on(data):
                await emit("error", {"code": "kill_switch",
                                     "message": "The kill switch is on, so the agent stopped. Nothing more was run."})
            else:
                await emit("error", {"code": "unauthorized",
                                     "message": "This access key is no longer authorized."})
            break
        if stop_turn:
            # Defence in depth with no observable effect today, so no test can
            # meaningfully pin it: every path that sets stop_turn is already
            # handled. Authority loss breaks above; a cancel is caught by the
            # loop-top check BEFORE the next model call, which emits the same
            # "cancelled" error. It stays because it makes the turn's exit
            # explicit at the point the batch decided to stop, rather than
            # depending on an ordering argument two screens away.
            if cancel.is_set():
                await emit("error", {"code": "cancelled", "message": "Cancelled."})
            break

    # Make an ambiguous ending explain itself, so a reply the model truncated at
    # its output limit or an empty response never looks like the agent silently
    # stalled.
    if not turn_errored and not cancel.is_set():
        if stop_reason == "max_tokens":
            await emit("notice", {"code": "max_tokens_stop", "message": (
                "The model stopped because it reached its output length limit. "
                "Ask it to continue, or raise the model's output token limit "
                "(for Ollama, its num_predict/context settings)."
            )})
        elif stop_reason == "end_turn" and not saw_text:
            await emit("notice", {"code": "empty_turn", "message": (
                "The model ended the turn without a text reply."
            )})
    # The loop hit the per-turn round cap with no human in the loop: offer to
    # continue instead of stopping silently. Emitted before the messages frame
    # so the client has the resumable conversation to send back on "Continue".
    if paused_for_continue and not cancel.is_set():
        await emit("continue_required", {"iterations": iterations})
    _LOGGER.debug(
        "agentCLI turn ended: stop_reason=%s saw_text=%s errored=%s paused=%s",
        stop_reason, saw_text, turn_errored, paused_for_continue,
    )
    await emit("messages", {"messages": messages})
    await emit("done", {"stop_reason": stop_reason})
    return messages


_VOICE_ADDENDUM = (
    "\n\nYou are answering the user through Home Assistant's voice/chat assistant. "
    "Keep replies spoken-friendly, even when the selected detail level is Detailed. "
    "Use the tools above to answer or act. "
    "If a tool result says \"queued_for_approval\", the action has NOT happened yet: it is "
    "waiting for an administrator to approve it in the Phoenix MCP panel. Tell the user it is queued "
    "for approval; do not retry it and do not claim it is done."
)


async def async_run_voice_turn(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    user_text: str,
    *,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
    include_review_hint: bool = False,
    language: object = None,
) -> str:
    """Run one conversation-agent turn to completion; return the spoken answer.

    A headless sibling of async_run_agent_turn for HA's Assist/voice pipeline
    (engine: _run_headless_turn): no SSE, no interactive approval wait. A confirm
    gate is fed back to the model as a "queued_for_approval" tool result, so a
    voice turn never blocks.

    include_review_hint says where to approve anything queued this turn. It is a
    sentence rather than a link: one string is both displayed and spoken, and
    nothing separates typed Assist from Companion-app voice, so a URL would be
    read aloud. Pass False for a voice satellite, whose model reply already says
    that the action is waiting.

    language is the CONVERSATION's language: a spoken reply goes to whoever is
    talking, not to the server. Every sentence that is not the model's own words
    comes from catalog_templates.VOICE_TEMPLATES, so a failure is never spoken in English to a
    non-English pipeline. None falls back to English.
    """
    if is_companion(access_key) and not internal_turn(access_key):
        report = await async_delegate(hass, data, access_key, user_text, language, base_url)
        voice_result("ok" if report["status"] in HOME_COMPANION_REPORT_STATUSES
                     else report["status"], bool(report["review_urls"]), bool(report["message"]))
        answer = report["message"]
        if include_review_hint and report["review_urls"]:
            answer += "\n\n" + voice_text(language, "approval_link")
        return answer


    settings = data.store.get_settings()
    home_focused = settings.voice_agent_home_focused
    behavior = _conversation_behavior_prompt(
        settings.voice_agent_conversation_style,
        settings.voice_agent_detail_level,
        home_focused=home_focused,
    )

    turn = await _run_headless_turn(
        hass, data, access_key, provider, session, base_url,
        system=(
            _build_instructions(access_key, data, base_url, hass)
            + behavior
            + _VOICE_ADDENDUM
            + _home_focus_output_contract(home_focused)
        ),
        seed=user_text, client_ip=VOICE_AGENT_CLIENT_IP, max_iterations=max_iterations,
        detect_focus_refusal=home_focused, routing_surface="voice", routing_language=language,
        # A local Ollama server's first reply includes loading the model and
        # reading the whole prompt on the operator's own hardware, which can take
        # minutes; only the silent-stream bound applies there.
        first_reply_seconds=None if getattr(provider, "kind", "") == "ollama" else VOICE_FIRST_REPLY_SECONDS,
    )
    voice_result(turn.outcome, bool(turn.review_urls), bool(turn.text.strip()))
    # Never raise at a microphone: every ending becomes something sayable, in the
    # CONVERSATION's language (voice_text), not the server's. A partial answer the
    # model already produced is preferred over an apology that discards it.
    if turn.outcome == TURN_OUTCOME_UNKNOWN:
        return voice_text(language, "prior_action_unresolved" if turn.blocked_by_prior_action else "outcome_unknown")
    if turn.outcome == TURN_PROVIDER_ERROR:
        if turn.text.strip():
            return turn.text.strip()
        # The detail is English for the Conversations log and AI Task; speak a
        # sentence of the conversation's own language instead.
        if turn.detail_code == "provider_slow":
            return voice_text(language, "provider_slow")
        return voice_text(language, "model_api_unsupported" if turn.detail_code == "model_api_unsupported"
                          else "provider_unavailable")
    if turn.outcome == TURN_OUTPUT_LIMIT:
        return turn.text.strip() or voice_text(language, "output_limit")
    if turn.outcome == TURN_AUTHORITY_LOST:
        # The closing round usually produces a sentence ("I could not do that");
        # say it. The fallback covers a model that asked for tools again instead.
        return turn.text.strip() or voice_text(language, "access_key_unavailable")
    if turn.outcome == TURN_EXHAUSTED:
        # By construction there is no partial answer here: the round that runs out
        # of steps is a tool round, which has already reset the text.
        return voice_text(language, "out_of_steps")

    if turn.focus_declined:
        # A weak model can emit a marked refusal and then answer the unrelated
        # request anyway. Voice has no visible transcript to preserve, so render
        # the refusal deterministically and never speak the model's mixed content.
        answer = voice_text(language, "focus_declined")
    else:
        answer = turn.text.strip() or voice_text(language, "could_not_complete")
    if turn.outcome == TURN_TRUNCATED and turn.text.strip():
        # Said out loud, because a cut-off answer is acted on as a whole one.
        answer = f"{answer} {voice_text(language, 'answer_truncated')}"
    if include_review_hint and turn.review_urls:
        answer = f"{answer}\n\n{voice_text(language, 'approval_link')}"
    return answer


_AI_TASK_ADDENDUM = (
    "\n\nYou are performing a Home Assistant AI Task: generate the requested data from "
    "the instructions. Use the tools above only when you need live data to answer. "
    "If a tool result says \"queued_for_approval\", that action is waiting for an "
    "administrator and has NOT happened; do not retry it. Return only the requested "
    "result as your final message, with no extra commentary."
)

_AI_TASK_STRUCTURE_DIRECTIVE = (
    "\n\nYour final message MUST be a single valid JSON value conforming to this JSON "
    "schema, with no prose, explanation, or markdown code fences:\n{schema}"
)


def _ai_task_free_text_output_contract(style: str, detail: str) -> str:
    """Repeat free-text presentation defaults at AI Task's final-output boundary."""
    style_instruction = _STYLE_INSTRUCTIONS.get(style, _STYLE_INSTRUCTIONS["direct"])
    detail_instruction = _DETAIL_INSTRUCTIONS.get(detail, _DETAIL_INSTRUCTIONS["balanced"])
    return (
        "\n\nAI Task free-text output contract: "
        f"{style_instruction} {detail_instruction} "
        "Return only the requested data. Do not mention tools, available entities, the task "
        "process, or additional work Phoenix could do unless the task instructions request it."
    )


class AiTaskError(Exception):
    """The AI Task model loop failed (provider/stream error)."""


@untracked_turn
async def async_run_ai_task(
    hass: HomeAssistant,
    data: PhoenixData,
    access_key: AccessKeyRecord,
    provider: ClaudeProvider | OpenAICompatProvider,
    session: ClientSession,
    base_url: str,
    instructions: str,
    *,
    structure_json: dict | None = None,
    max_iterations: int = AGENTCLI_MAX_ITERATIONS,
) -> str:
    """Run one AI Task (ai_task.generate_data) to completion; return the final text.

    A headless sibling of async_run_voice_turn for HA's AI Task platform: same gated tool
    loop (per-access-key scope, MESA, approvals, audit via _dispatch_mcp with sentinel
    client_ip "ai_task"), no SSE, no interactive wait. A confirm gate returns pending,
    fed back as a "queued_for_approval" tool result so a task never blocks. The access key
    is re-resolved before every dispatch, so a mid-task revoke/kill-switch is honored.

    When structure_json is given (the caller's JSON schema derived from the task's
    structure), the model is instructed to emit only matching JSON; the caller parses
    the returned text.
    """
    companion_authority = None
    if is_companion(access_key):
        companion_authority = RoutingAuthority.capture(data, access_key)
        settings = data.store.get_settings()
        report = await async_delegate(hass, data, access_key, instructions, hass.config.language, base_url,
                                      style=settings.ai_task_conversation_style, detail=settings.ai_task_detail_level)
        if companion_authority.current(hass) is None:
            raise AiTaskError(voice_text(hass.config.language, "access_key_unavailable"))
        if report["status"] != "completed":
            raise AiTaskError(report["message"])
        if structure_json is None:
            return report["message"]
        instructions = json.dumps({"task": instructions, "phoenix_report": report}, ensure_ascii=False)


    settings = data.store.get_settings()
    system = _build_instructions(access_key, data, base_url, hass)
    if structure_json is None:
        system += _conversation_behavior_prompt(
            settings.ai_task_conversation_style,
            settings.ai_task_detail_level,
        )
    system += _AI_TASK_ADDENDUM
    if structure_json is None:
        system += _ai_task_free_text_output_contract(
            settings.ai_task_conversation_style,
            settings.ai_task_detail_level,
        )
    else:
        system += _AI_TASK_STRUCTURE_DIRECTIVE.format(schema=json.dumps(structure_json))

    turn = await _run_headless_turn(
        hass, data, access_key, provider, session, base_url,
        system=system, seed=instructions, client_ip=AI_TASK_CLIENT_IP,
        max_iterations=1 if companion_authority is not None else max_iterations, routing_surface="ai_task", routing_language=hass.config.language,
        response_only=companion_authority is not None,
    )
    if companion_authority is not None and companion_authority.current(hass) is None:
        raise AiTaskError(voice_text(hass.config.language, "access_key_unavailable"))
    # Unlike the voice surface there is no one listening to an apology: an
    # automation asked for data, so every non-OK ending is an error the caller
    # wraps as a HomeAssistantError. These messages are operator-facing and
    # deliberately English, like the rest of the agent-facing error text.
    if turn.outcome == TURN_OUTCOME_UNKNOWN:
        if turn.blocked_by_prior_action:
            raise AiTaskError("This task was not executed because an earlier action has an unresolved outcome. Check the Needs Attention window of the Phoenix MCP panel before trying again.")
        raise AiTaskError("An action outcome is unknown. Reconcile Home Assistant and approval results before retrying.")
    if turn.outcome == TURN_PROVIDER_ERROR:
        raise AiTaskError(turn.detail)
    if turn.outcome == TURN_OUTPUT_LIMIT:
        raise AiTaskError("the model produced too much output this turn")
    if turn.outcome == TURN_AUTHORITY_LOST:
        raise AiTaskError(
            "the access key lost authority mid-task (revoked, expired, or the kill "
            "switch); at least one tool call was refused, so the result is unsound"
        )
    if turn.outcome == TURN_EXHAUSTED:
        raise AiTaskError(
            "the model ran out of steps before returning a result; raise "
            "Steps before check-in in Phoenix MCP settings if the task needs more"
        )
    if turn.outcome == TURN_TRUNCATED:
        raise AiTaskError(
            "the model stopped at its own output length limit, so the result is "
            "incomplete; raise the model's output token limit"
        )
    return turn.text.strip()
