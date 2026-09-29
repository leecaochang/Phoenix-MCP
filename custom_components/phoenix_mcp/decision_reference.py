"""Review singular references anchored by an earlier local intent, without effects."""
from __future__ import annotations

import asyncio
import json
from time import monotonic
from typing import Any

from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .command_routing import RoutingAuthority, async_evaluate_command, effective_routing_mode, routing_language
from .const import (
    DECISION_CONTEXT_MAX_BYTES, DECISION_REFERENCE_LANGUAGES, DECISION_REFERENCE_EXECUTION_MODELS,
    DECISION_TARGET_INTENTS, MAX_SEARCH_QUERY_LEN,
)
from .decision_batch import batch_catalog_digest, batch_execution_eligible, capture_batch_targets
from .decision_context import context_history
from .decision_history import history_texts
from .decision_provider import DecisionError, DecisionProvider, confident_choice
from .helpers import redact_inaccessible_entity_ids_in_text, redact_secrets_in_text


def reference_execution_eligible(hass: Any, data: Any, language: object) -> bool:
    """Only qualified Chat accounts may produce internal reference plans."""
    from .agentcli import loaded_decision_account
    settings = data.store.get_settings()
    account = settings.decision_provider_id
    cfg = loaded_decision_account(hass, account) if account else None
    return bool(effective_routing_mode(settings, "chat") == "hybrid"
                and routing_language(language, preview=True) in DECISION_REFERENCE_LANGUAGES
                and cfg and (cfg.kind, cfg.base_url, cfg.model) in DECISION_REFERENCE_EXECUTION_MODELS)


async def async_preview_reference(hass: Any, data: Any, token: Any, sentence: str,
                                  language: str, messages: Any, local: dict) -> dict:
    """Administrator previews never return execution plans."""
    return await _async_review_reference(hass, data, token, sentence, language, messages, local)


async def async_prepare_chat_reference(hass: Any, data: Any, token: Any, sentence: str,
                                        language: str, messages: Any, local: dict, cancel: asyncio.Event) -> dict:
    """Return a bound plan only to the internal Chat routing ingress."""
    return await _async_review_reference(hass, data, token, sentence, language, messages, local,
                                         prepare=True, cancel=cancel)


async def _async_review_reference(hass: Any, data: Any, token: Any, sentence: str,
                                  language: str, messages: Any, local: dict, *, prepare: bool = False,
                                  cancel: asyncio.Event | None = None) -> dict:
    """Share the decision slot; all effects still belong to the guarded executor."""
    started = monotonic()

    def result(reason: str, **extra: Any) -> dict:
        return {"reason": reason, "executed": False,
                "elapsed_ms": round((monotonic() - started) * 1000, 2), **extra}

    history = context_history(messages)
    lang = routing_language(language, preview=True)
    if lang not in DECISION_REFERENCE_LANGUAGES:
        return result("unsupported_language")
    if (history is None or not isinstance(sentence, str) or not sentence.strip()
            or len(sentence) > MAX_SEARCH_QUERY_LEN):
        return result("invalid_context")
    if local.get("proposal") or local.get("reason") not in ("no_match", "no_target", "unsupported"):
        return result("local_unavailable")
    if cancel is not None and cancel.is_set():
        return result("cancelled")
    if prepare and not reference_execution_eligible(hass, data, lang):
        return result("context_unverified")
    authority = RoutingAuthority.capture(data, token)
    if authority.current(hass) is None:
        return result("authority_changed")
    runtime = data.decision_shadow
    if runtime.task is not None:
        return result("busy")

    async def evaluate() -> dict:
        from .agentcli import _get_secret_store, PROVIDER_DEFINITIONS, decision_account_fingerprint
        try:
            async with asyncio.timeout(authority.settings.decision_timeout_ms / 1000):
                store = await _get_secret_store(hass)
                account = authority.settings.decision_provider_id
                cfg = store.resolve(account, purpose="decision") if account else None
                current = authority.current(hass)
                if current is None:
                    return result("authority_changed")
                if cfg is None or account is None or not cfg.model:
                    return result("not_configured")
                texts = history_texts([sentence, history], check_sensitive=True)
                if any(redact_inaccessible_entity_ids_in_text(redact_secrets_in_text(text) or "", current, hass) != text
                       or (cfg.api_key and cfg.api_key in text) for text in texts):
                    return result("context_unavailable")
                extended = batch_execution_eligible(cfg, lang)
                snapshot = capture_batch_targets(hass, current, extended=extended)
                # A successful, exact transcript can carry its last subject across pronoun turns.
                # Never search older messages for a convenient referent.
                binding = [current.id, current.token_hash, current.settings_version, language, authority.settings_values]
                remembered = data.command_routing.chat_history.subject(
                    history, [binding, decision_account_fingerprint(hass, account)]) if prepare else None
                anchor = ({"reason": "supported", "proposal": remembered} if remembered else
                          await async_evaluate_command(hass, data, current, history[-2]["content"], lang, preview=True))
                current = authority.current(hass)
                if current is None or store.resolve(account, purpose="decision") != cfg:
                    return result("authority_changed")
                if capture_batch_targets(hass, current, extended=extended) != snapshot:
                    return result("context_changed")
                proposal = anchor.get("proposal")
                if anchor.get("reason") != "supported" or not isinstance(proposal, dict):
                    return result("local_unavailable")
                ids = proposal.get("entity_ids")
                if not isinstance(ids, list) or len(ids) != 1 or not ids[0].startswith("light."):
                    return result("local_unavailable")
                descriptor = next((target for target in snapshot[0].values() if target["entity_id"] == ids[0]), None)
                if descriptor is None:
                    return result("context_unavailable")
                state = {"utterance": sentence, "language": lang, "history": history,
                         "anchor": {"history_index": len(history) - 2, "previous_request": proposal, "target": descriptor,
                                    "source": "verified_reply" if remembered else "strict_recognition"}}
                encoded = json.dumps(state, ensure_ascii=False)
                if len(encoded.encode()) > DECISION_CONTEXT_MAX_BYTES or (cfg.api_key and cfg.api_key in encoded):
                    return result("context_unavailable")
                provider = DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter,
                                        request_budget=data.decision_budget, request_limit=authority.settings.decision_request_budget,
                                            metrics=runtime.metrics, metric_scope="chat" if prepare else "preview")
                def request_guard() -> None:
                    latest = authority.current(hass)
                    if latest is None or store.resolve(account, purpose='decision') != cfg:
                        raise DecisionError('authority_changed')
                    if capture_batch_targets(hass, latest, extended=extended) != snapshot:
                        raise DecisionError('context_changed')
                provider.request_guard = request_guard
                answer = await provider.review_reference(async_get_clientsession(hass), state,
                                                         authority.settings.decision_timeout_ms)
                current = authority.current(hass)
                if current is None or store.resolve(account, purpose="decision") != cfg:
                    return result("authority_changed")
                if capture_batch_targets(hass, current, extended=extended) != snapshot:
                    return result("context_changed")
                checks = answer["checks"]
                category = checks["route"]["choice"]
                if (category == "provider" or checks["reference"]["choice"] != "bound"
                        or checks["constraints"]["choice"] != "clear"):
                    return result("provider_fallback", decision=answer)
                if not all(confident_choice(check) for check in checks.values()):
                    return result("low_probability", decision=answer)
                if category not in descriptor["eligible_categories"]:
                    return result("category_mismatch", decision=answer)
                selected = {"intent": DECISION_TARGET_INTENTS[category], "entity_ids": list(ids)}
                reviewed = result("target_selected", decision=answer, proposal=selected)
                if prepare:
                    from .command_execution import _capture_plan, _digest
                    if (not reference_execution_eligible(hass, data, lang) or answer["model"] not in
                            DECISION_REFERENCE_EXECUTION_MODELS.get((cfg.kind, cfg.base_url, cfg.model), ())):
                        return result("context_unverified")
                    plan = _capture_plan(hass, data, current, selected, ingress="chat")
                    account_digest = decision_account_fingerprint(hass, account)
                    if plan is None or account_digest is None:
                        return result("context_changed")
                    review = {"account": account_digest, "model": answer["model"], "language": lang,
                              "proposal": _digest(selected)}
                    plan.update(decision_category=category, decision_catalog=snapshot[1][category].digest(),
                                decision_account=account_digest, reference_review=review,
                                decision_batch={"model": answer["model"], "language": lang,
                                                "catalog": batch_catalog_digest(snapshot), "reference": _digest(review)})
                    reviewed.update(reason="plan_ready", _plan=plan)
                return reviewed
        except DecisionError as error:
            return result(str(error))
        except TimeoutError:
            return result("timeout")
        except (ValueError, RecursionError):
            return result("context_unavailable")
        except Exception:
            return result("unavailable")

    task = hass.async_create_background_task(evaluate(), "phoenix_reference_preview")
    runtime.task = task
    waiter = asyncio.create_task(cancel.wait()) if cancel is not None else None
    try:
        if waiter is not None:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
            if cancel is not None and cancel.is_set():
                return result("cancelled")
        return await task
    except asyncio.CancelledError:
        caller = asyncio.current_task()
        if caller is not None and caller.cancelling():
            raise
        return result("cancelled")
    finally:
        if not task.done():
            task.cancel()
        if waiter is not None:
            waiter.cancel()
        await asyncio.gather(task, *([waiter] if waiter is not None else []), return_exceptions=True)
        if runtime.task is task:
            runtime.task = None
