"""HTTP views for Agent Chat and provider accounts."""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import datetime, timedelta
import ipaddress
import json
import logging
from typing import Any, cast
from urllib.parse import urlparse

from aiohttp import ClientError, web
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util.dt import parse_datetime, utcnow

from .admin_view import _audit_admin, _ok, _read_body, require_admin
from .view_base import _err
from .agent_chat import async_run_agent_turn
from .agent_turn import (
    _ATTACHMENT_BUDGET_CHARS, _chat_attachments, _image_payload_chars, _without_history_images,
)
from .const import DOMAIN, MAX_REQUEST_BODY_BYTES
from .data import PhoenixData
from .helpers import get_client_ip, LocalizedReason
from .home_companion import is_companion
from .llm.accounts import (
    _default_base_url, _default_model, _get_secret_store, _provider_catalog,
    DuplicateProviderError, PROVIDER_DEFINITIONS, provider_purpose, ProviderConfig,
)
from .llm.base import AGENTCLI_HEARTBEAT_SECONDS, ProbeConfigError
from .llm.factory import build_provider
from .llm.probes import _effort_probe_body, async_probe_capabilities, async_probe_vision
from .provider_constants import (
    AGENTCLI_CLIENT_IP, AGENTCLI_PROBE_SENTINEL, AGENTCLI_REQUEST_BODY_BYTES,
    PROVIDER_API_KEY_MAX_LENGTH,
)
from .routing.decision_history import without_thinking
from .routing.decision_provider import DecisionError, decision_error_reason, DecisionProvider
from .view_base import PhoenixView
from .voice_agent import async_remove_assist_pipeline


_LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# HTTP views
# --------------------------------------------------------------------------- #

class PhoenixAgentCliChatView(PhoenixView):
    """POST /api/phoenix-mcp/agentcli/chat - the streaming agent turn (admin, SSE)."""

    url = "/api/phoenix-mcp/agentcli/chat"
    name = "api:phoenix-mcp:agentcli:chat"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        # Room for base64 images; everything else keeps the ordinary cap below.
        body = await _read_body(request, rid, limit=AGENTCLI_REQUEST_BODY_BYTES)
        if isinstance(body, web.Response):
            return body
        attachments = body.pop("attachments", None)
        history_images = _image_payload_chars(body.get("messages"))
        if (history_images > _ATTACHMENT_BUDGET_CHARS
                or len(json.dumps(body).encode()) - history_images > MAX_REQUEST_BODY_BYTES):
            return _err("request_too_large", "Request body too large.", 413, rid, key="bodyTooLarge")
        data = self.hass.data.get(DOMAIN)
        if data is None:
            return _err(
                "not_found", "Phoenix MCP is not set up.", 404, rid,
                key="setupMissing",
            )

        # Agent Chat bypasses async_get_authenticated_access_key (it is admin-authed, not
        # bearer-authed), so it must enforce the same invariants that gate every
        # MCP request: shutdown, the runtime kill switch, and access key validity.
        settings = data.store.get_settings()
        if data.shutting_down:
            return _err("service_unavailable", "Home Assistant is shutting down.", 503, rid, key="serviceStopping")
        if not data.ready:
            return _err("service_unavailable", "Phoenix MCP setup is not complete.", 503, rid, key="setupIncomplete")
        if settings.kill_switch:
            return _err(
                "service_unavailable", "Phoenix MCP is disabled (kill switch).", 503, rid,
                key="serviceDisabled",
            )

        access_key_id = str(body.get("access_key_id") or "")
        access_key = data.store.get_access_key_by_id(access_key_id)
        if access_key is None:
            return _err(
                "not_found", "Access key not found.", 404, rid,
                key="accessKeyNotFound",
            )
        if not access_key.is_valid():
            return _err(
                "invalid_request", "This access key is revoked or expired.", 400, rid,
                key="accessKeyInactive",
            )

        instance_id = str(body.get("instance_id") or "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, body.get("model") or None)
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        if not cfg.model:
            return _err(
                "invalid_request", "No model selected for this provider.", 400, rid,
                key="providerModelMissing",
            )

        images = _chat_attachments(attachments, rid)
        if isinstance(images, web.Response):
            return images
        if images:
            if body.get("continue") is True:
                # Continue adds no message to carry them.
                return _err("invalid_request", "Images can only be attached to a new message.", 400, rid,
                            key="attachmentsContinue")
            if is_companion(access_key):
                # Home Companion delegates only the text command.
                return _err("invalid_request",
                            "Home Companion takes text commands only, so images cannot be attached.", 400, rid,
                            key="attachmentsCompanion")
            if cfg.vision is False:
                # Declared or learned. Unknown support is sent, and a refusal
                # teaches Phoenix (see _refused_option).
                return _err("invalid_request", "This model does not accept images.", 400, rid,
                            key="attachmentsVisionUnsupported")

        provider = build_provider(cfg)
        messages = body.get("messages")
        if not isinstance(messages, list):
            messages = []
        if cfg.vision is False:
            _without_history_images(messages)
        if body.get("continue") is True:
            # Resume a turn that paused at the round-cap checkpoint: no new user
            # message is appended; the model continues from the tool results the
            # prior turn left behind. Requires an existing conversation.
            if not messages:
                return _err(
                    "invalid_request", "Nothing to continue.", 400, rid,
                    key="nothingToContinue",
                )
        else:
            # A finished turn's tool-loop thinking blocks are no longer needed.
            messages = without_thinking(messages)
            user_text = str(body.get("user") or "")
            if not user_text.strip() and not images:
                return _err(
                    "invalid_request", "Empty message.", 400, rid,
                    key="emptyMessage",
                )
            message = ({"role": "user", "content": user_text} if not images
                       else provider.user_message(user_text if user_text.strip() else "", images))
            messages = [*messages, message]
        options = body.get("options") if isinstance(body.get("options"), dict) else {}

        session = async_get_clientsession(self.hass)
        try:
            base_url = str(request.url.origin())
        except ValueError:  # A Host header that does not form a URL.
            return _err("invalid_request", "Invalid Host header.", 400, rid, key="invalidHostHeader")
        client_ip = AGENTCLI_CLIENT_IP or get_client_ip(request)

        resp = web.StreamResponse(
            status=200,
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "X-Phoenix-Request-ID": rid,
            },
        )
        await resp.prepare(request)
        cancel = asyncio.Event()

        async def emit(name: str, payload: dict) -> None:
            # Learn from a refusal the provider returned during REAL work. This is the
            # backstop under the declared and probed answers: it costs nothing
            # extra, it is ground truth rather than an inference, and it covers
            # the providers that publish nothing and validate nothing, which
            # neither of the other two mechanisms can reach.
            refused = payload.get("refused") if name == "error" else None
            if name == "error" and payload.get("status") == 404:
                # "Model not found" is how a provider says a model was retired.
                # Re-read the catalogue so the next turn resolves to its successor.
                self.hass.async_create_task(async_refresh_catalog(self.hass, instance_id))
            if refused and cfg.model:
                await store.record_refusal(instance_id, cfg.model, refused, utcnow().isoformat())
            frame = f"event: {name}\ndata: {json.dumps(payload, default=str)}\n\n"
            try:
                await resp.write(frame.encode("utf-8"))
            except (ConnectionResetError, ClientError, RuntimeError):
                cancel.set()

        async def _heartbeat() -> None:
            # Send an SSE comment periodically so an idle intermediary (reverse
            # proxy, Nabu Casa, a backgrounded tab) does not drop the streaming
            # connection during a quiet stretch, e.g. while the model is thinking
            # or an approval is pending. Comment frames carry no data: line, so the
            # panel's parser ignores them. A failed write means the client is gone.
            try:
                while not cancel.is_set():
                    await asyncio.sleep(AGENTCLI_HEARTBEAT_SECONDS)
                    try:
                        await resp.write(b": keepalive\n\n")
                    except (ConnectionResetError, ClientError, RuntimeError):
                        cancel.set()
                        return
            except asyncio.CancelledError:
                pass

        heartbeat = self.hass.loop.create_task(_heartbeat())
        try:
            await emit("ready", {"provider": cfg.kind, "model": cfg.model, "access_key_id": access_key_id})
            await async_run_agent_turn(
                hass=self.hass, data=data, access_key=access_key, provider=provider,
                session=session, messages=messages, options=options or {},
                log_conversation_id=body.get("log_conversation_id"),
                client_ip=client_ip, base_url=base_url, emit=emit, cancel=cancel,
                max_iterations=settings.agentcli_max_iterations,
                conversation_style=settings.agentcli_conversation_style,
                detail_level=settings.agentcli_detail_level,
                home_focused=settings.agentcli_home_focused,
                routing_language=body.get("language") if body.get("continue") is not True else None,
                account=(instance_id, body.get("model") or None),
            )
        except Exception:  # never leak internals to the client
            _LOGGER.exception("agentCLI turn failed")
            await emit("error", {"code": "internal", "message": "Internal error."})
        finally:
            heartbeat.cancel()
            try:
                await resp.write_eof()
            except (ConnectionResetError, ClientError, RuntimeError):
                pass
        return cast(web.Response, resp)


# Cloud metadata endpoints that the link-local and reserved ranges do not cover.
_METADATA_ADDRESSES = frozenset(ipaddress.ip_address(address) for address in (
    "100.100.100.200", "168.63.129.16", "192.0.0.192", "fd00:ec2::254",
))


def _ip_is_blocked(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, allow_private: bool,
) -> bool:
    """Return whether an operator-supplied endpoint may target this address.

    An IPv6 form that carries an IPv4 address is judged by what it carries. A
    remote provider must be globally routable, which also rules out shared CGNAT
    space; a local server may sit there, so only metadata addresses are refused.
    """
    if isinstance(ip, ipaddress.IPv6Address) and (carried := ip.ipv4_mapped or ip.sixtofour):
        return _ip_is_blocked(carried, allow_private=allow_private)
    return (
        ip in _METADATA_ADDRESSES
        or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        or (not allow_private and not ip.is_global)
    )


async def _validate_base_url(
    url: str, *, allow_private: bool = True,
) -> ProbeConfigError | None:
    """Check URL shape and resolved addresses at setup time.

    This administrator-only guard cannot pin future DNS resolution. Ollama may
    use private HTTP endpoints; other providers require HTTPS. Redirect behavior
    depends on the provider adapter, so this check is not a connection-time SSRF
    guarantee.
    """
    try:
        parts = urlparse(url)
    except ValueError:
        return ProbeConfigError("Enter a valid server URL.", "providerUrlInvalid")
    schemes = ("http", "https") if allow_private else ("https",)
    if parts.scheme not in schemes:
        if allow_private:
            return ProbeConfigError(
                "The server URL must start with http:// or https://.",
                "providerUrlScheme",
            )
        return ProbeConfigError(
            "The provider URL must start with https://.",
            "providerRemoteUrlScheme",
        )
    if not parts.hostname:
        return ProbeConfigError(
            "Enter a valid server URL including a host.", "providerUrlHostRequired",
        )
    if parts.username or parts.password:
        return ProbeConfigError(
            "The server URL must not contain embedded credentials.",
            "providerUrlCredentialsForbidden",
        )
    if parts.fragment:
        return ProbeConfigError(
            "The server URL must not contain a '#' fragment.",
            "providerUrlFragmentForbidden",
        )
    try:
        literal = ipaddress.ip_address(parts.hostname)
    except ValueError:
        literal = None
    if literal is not None:
        if _ip_is_blocked(literal, allow_private=allow_private):
            if allow_private:
                return ProbeConfigError(
                    "The server URL must not point at a link-local or metadata address.",
                    "providerUrlAddressForbidden",
                )
            return ProbeConfigError(
                "The provider URL must not point at a private, loopback, "
                "link-local, multicast, or reserved address.",
                "providerRemoteUrlAddressForbidden",
            )
        return None
    # A hostname: resolve it and reject if ANY address is link-local/metadata.
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(parts.hostname, None)
    except (OSError, ValueError):
        return None  # unresolvable at setup time; the actual request fails cleanly
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except (ValueError, IndexError):
            continue
        if _ip_is_blocked(ip, allow_private=allow_private):
            if allow_private:
                return ProbeConfigError(
                    "The server URL resolves to a link-local or metadata address.",
                    "providerUrlResolutionForbidden",
                )
            return ProbeConfigError(
                "The provider URL resolves to a private, loopback, link-local, "
                "multicast, or reserved address.",
                "providerRemoteUrlResolutionForbidden",
            )
    return None


async def _probe_config(kind: str, body: dict) -> ProviderConfig | ProbeConfigError:
    """Build a throwaway config or a localized setup error contract."""
    definition = PROVIDER_DEFINITIONS.get(kind)
    if definition is None:
        return ProbeConfigError("Unknown provider.", "providerUnknown")
    api_key: str | None = None
    if any(field.id == "api_key" for field in definition.fields):
        raw_key = body.get("api_key", "")
        if (not isinstance(raw_key, str) or len(raw_key) > PROVIDER_API_KEY_MAX_LENGTH
                or any(ord(char) < 32 or ord(char) == 127 for char in raw_key)):
            return ProbeConfigError("Enter your API key.", "providerApiKeyRequired")
        api_key = raw_key.strip()
        if not api_key and any(field.id == "api_key" and field.required for field in definition.fields):
            return ProbeConfigError("Enter your API key.", "providerApiKeyRequired")

    endpoint_id: str | None = None
    if definition.endpoints:
        endpoint_id = str(body.get("endpoint_id") or definition.endpoints[0].value)
        if endpoint_id not in {choice.value for choice in definition.endpoints}:
            return ProbeConfigError(
                "Choose a valid provider plan.", "providerEndpointInvalid",
            )

    if any(field.id == "base_url" for field in definition.fields):
        base_url = str(body.get("base_url") or "").strip().rstrip("/")
        if not base_url:
            if kind == "ollama":
                return ProbeConfigError(
                    "Enter the Ollama server URL.", "ollamaUrlRequired",
                )
            return ProbeConfigError(
                "Enter the provider base URL.", "providerBaseUrlRequired",
            )
        err = await _validate_base_url(base_url, allow_private=kind == "ollama")
        if err:
            return err
    else:
        base_url = _default_base_url(kind, endpoint_id)
    model = str(body.get("model") or "").strip()
    if definition.purpose == "decision":
        if urlparse(base_url).query:
            return ProbeConfigError("The base URL must not contain a query.", "providerUrlInvalid")
        if len(model) > 200:
            return ProbeConfigError("Invalid provider configuration.", "providerUrlInvalid")
    return ProviderConfig(kind=kind, model=model or _default_model(kind),
                          base_url=base_url.rstrip("/"), api_key=api_key,
                          endpoint_id=endpoint_id)


def _stored_setup_config(cfg: ProviderConfig, model: str = "") -> dict[str, Any]:
    """Return only the setup values this provider persists, never fixed URLs."""
    definition = PROVIDER_DEFINITIONS[cfg.kind]
    out: dict[str, Any] = {}
    if any(field.id == "api_key" for field in definition.fields):
        out["api_key"] = cfg.api_key
    if any(field.id == "base_url" for field in definition.fields):
        out["base_url"] = cfg.base_url
    if cfg.endpoint_id:
        out["endpoint_id"] = cfg.endpoint_id
    if model:
        out["model"] = model
    return out


def _build_setup_provider(cfg: ProviderConfig):
    if provider_purpose(cfg.kind) == "decision":
        return DecisionProvider(cfg, PROVIDER_DEFINITIONS[cfg.kind].adapter)
    return build_provider(cfg)


async def _checked_decision_models(provider, session, model: object, rid: str) -> tuple[list[str], web.Response | None]:
    """Require a real advertised model before persisting a decision account."""

    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        return [], _err("invalid_request", "A model is required.", 400, rid, key="providerModelRequired")
    try:
        models = await provider.list_models(session)
    except DecisionError as error:
        reason = decision_error_reason(str(error))
        return [], _err("invalid_request", "Could not load decision provider models.", 400, rid,
                       key=reason.key, params=reason.params or None)
    if model.strip() not in models:
        return [], _err("invalid_request", "Choose a model returned by this provider.", 400, rid,
                       key="providerModelNotListed")
    return models, None


class PhoenixAgentCliProvidersView(PhoenixView):
    """GET (list accounts) / POST (create an account) for agentCLI providers."""

    url = "/api/phoenix-mcp/admin/agentcli/providers"
    name = "api:phoenix-mcp:admin:agentcli:providers"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        purpose = request.query.get("purpose", "conversation")
        return _ok({
            "instances": [i for i in store.list_instances() if provider_purpose(i["kind"]) == purpose],
            "provider_types": _provider_catalog(purpose),
        }, request_id=rid)

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        kind = str(body.get("kind") or "")
        if kind not in PROVIDER_DEFINITIONS:
            return _err(
                "invalid_request", "Unknown provider.", 400, rid,
                key="providerUnknown",
            )

        probe = await _probe_config(kind, body)
        if isinstance(probe, ProbeConfigError):
            return _err(
                "invalid_request", probe.message, 400, rid,
                key=probe.key, params=probe.params,
            )

        model = str(body.get("model") or "").strip()
        cfg = _stored_setup_config(probe, model)

        store = await _get_secret_store(self.hass)
        if store.has_duplicate(kind, cfg):
            return _err(
                "already_exists", str(DuplicateProviderError()), 409, rid,
                key="providerAlreadyConfigured",
            )

        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(probe)
        decision_models = None
        if provider_purpose(kind) == "decision":
            decision_models, error = await _checked_decision_models(provider, session, body.get("model"), rid)
            if error is not None:
                return error
        else:
            ok, reason = await provider.validate(session)
            if not ok:
                if isinstance(reason, LocalizedReason):
                    return _err("invalid_request", f"Connection failed: {reason}", 400, rid,
                                key=reason.key, params=reason.params or None)
                return _err(
                    "invalid_request", f"Connection failed: {reason}", 400, rid,
                    key="providerConnectionFailed", params={"detail": reason},
                )

        try:
            instance_id = await store.add(kind, cfg)
        except DuplicateProviderError as err:
            return _err(
                "already_exists", str(err), 409, rid,
                key="providerAlreadyConfigured",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        # Initial setup must establish the same declared model capabilities as
        # the explicit Refresh models action. This is metadata-only (no model
        # completion calls), but it may be one /api/show request per model for
        # Ollama. The account is already valid and stored, so a transient
        # catalogue failure must not turn a successful setup into a failure.
        try:
            models = decision_models if decision_models is not None else await provider.list_models(session)
            capabilities = await provider.list_model_capabilities(session, models)
            checked_at = utcnow().isoformat()
            await store.set_capabilities(instance_id, capabilities, checked_at)
            if provider_purpose(kind) == "conversation":
                await store.set_catalog(
                    instance_id, models, provider.model_created, checked_at,
                    [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
                    if capabilities else None,
                )
        except Exception:  # capability discovery is best effort
            _LOGGER.debug("Initial Agent Chat capability discovery failed", exc_info=True)
        created = next((i for i in store.list_instances() if i["id"] == instance_id), None)
        return _ok({"instance": created}, status=201, request_id=rid)


class PhoenixAgentCliProviderView(PhoenixView):
    """PATCH/DELETE /api/phoenix-mcp/admin/agentcli/providers/{instance_id} - change the default model or remove one account."""

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}"
    name = "api:phoenix-mcp:admin:agentcli:provider"
    requires_auth = True

    @require_admin
    async def patch(self, request: web.Request, instance_id: str) -> web.Response:
        """Change this account's default model.

        Conversational accounts permit unlisted model IDs for custom deployments.
        Decision accounts require a current advertised model, including API writes.
        """
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        if unknown := set(body) - {"model", "auto"}:
            fields = ", ".join(sorted(unknown))
            return _err("invalid_request", f"The request contains fields Phoenix does not recognize: {fields}.", 400, rid,
                        key="unknownFields", params={"fields": fields})
        auto = body.get("auto")
        if auto is not None and not isinstance(auto, bool):
            return _err("invalid_request", "auto must be a boolean (true or false).", 400, rid,
                        key="booleanRequired", params={"field": "auto"})
        model = str(body.get("model") or "").strip()
        if auto is True and model:
            return _err("invalid_request", "Send either a model or auto, not both.", 400, rid,
                        key="providerModelOrAuto")
        if not model and auto is not True:
            return _err(
                "invalid_request", "A model is required.", 400, rid,
                key="providerModelRequired",
            )
        store = await _get_secret_store(self.hass)
        record = store.get(instance_id)
        if auto is True and record and provider_purpose(record.get("kind", "")) == "decision":
            # Decision accounts are qualified against exact model ids.
            return _err("invalid_request", "Decision accounts need an explicit model.", 400, rid,
                        key="decisionModelRequired")
        if record and provider_purpose(record.get("kind", "")) == "decision":
            cfg = store.resolve(instance_id, purpose="decision")
            if cfg is None:
                return _err("not_found", "Provider account not found.", 404, rid,
                            key="providerAccountNotFound")
            _, error = await _checked_decision_models(
                _build_setup_provider(cfg), async_get_clientsession(self.hass), body.get("model"), rid,
            )
            if error is not None:
                return error
            data: PhoenixData = self.hass.data[DOMAIN]
            async with data.settings_update_lock:
                if store.get(instance_id) != record:
                    return _err("conflict", "Provider account changed. Reload and try again.", 409, rid,
                                key="providerAccountChanged")
                if not await store.set_model(instance_id, model):
                    return _err("not_found", "Provider account not found.", 404, rid,
                                key="providerAccountNotFound")
        elif not await store.set_model(instance_id, model):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        if auto is True:
            # Show the operator the model automatic mode resolves to right now.
            await async_refresh_catalog(self.hass, instance_id, force=True)
        current = next((i for i in store.list_instances() if i["id"] == instance_id), None)
        return _ok({"instance": {
            "id": instance_id, "model": (current or {}).get("model", model),
            "model_mode": "auto" if auto is True else "fixed",
        }}, request_id=rid)

    @require_admin
    async def delete(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        data = self.hass.data.get(DOMAIN)
        if data is None:
            await store.delete(instance_id)
            return _ok({"deleted": instance_id}, request_id=rid)

        # Settings PATCH validates provider references under this same lock. Delete
        # the provider and clear every dependent reference in one transaction so a
        # validated PATCH cannot persist the ID after it disappears.
        async with data.settings_update_lock:
            await store.delete(instance_id)
            async with data.store.async_lock:
                settings = data.store.get_settings()
                voice_removed = settings.voice_agent_provider_id == instance_id
                ai_task_removed = settings.ai_task_provider_id == instance_id
                patchable: dict[str, Any] = {}
                if settings.decision_provider_default_id == instance_id:
                    # One decision account must stay selected while any exists, so
                    # the default passes to a remaining account. Using it is a
                    # separate switch, which stays off (see below).
                    remaining = [
                        i["id"] for i in store.list_instances()
                        if provider_purpose(i["kind"]) == "decision"
                    ]
                    patchable["decision_provider_default_id"] = remaining[0] if remaining else None
                if settings.decision_provider_id == instance_id:
                    data.decision_shadow.cancel()
                    patchable.update(decision_provider_id=None, decision_shadow_enabled=False,
                                     agentcli_tool_routing_mode="off", voice_agent_tool_routing_mode="off", ai_task_tool_routing_mode="off")
                    for key in ("voice_agent_routing_mode", "agentcli_routing_mode"):
                        if getattr(settings, key) == "hybrid":
                            patchable[key] = "off"
                if voice_removed:
                    patchable.update(
                        voice_agent_provider_id=None,
                        voice_agent_model=None,
                    )
                if ai_task_removed:
                    patchable.update(
                        ai_task_provider_id=None,
                        ai_task_model=None,
                    )
                if patchable:
                    await data.store.async_patch_settings(**patchable)

            if voice_removed:
                if data.async_sync_voice_agent is not None:
                    data.async_sync_voice_agent()
                # The voice agent lost its provider and is unregistered;
                # remove any Phoenix-created Assist pipeline as well.
                await async_remove_assist_pipeline(self.hass, data)
            if ai_task_removed and data.async_sync_ai_task is not None:
                # The AI Task is not fully configured; remove its entity
                # from Home Assistant and the AI Task picker.
                data.async_sync_ai_task()
        _audit_admin(self.hass, request, rid, f"provider:{instance_id}")
        return _ok({"deleted": instance_id}, request_id=rid)


class PhoenixAgentCliModelsView(PhoenixView):
    """GET /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models - available models."""

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/models"
    name = "api:phoenix-mcp:admin:agentcli:models"
    requires_auth = True

    @require_admin
    async def get(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, purpose=provider_purpose((store.get(instance_id) or {}).get("kind", "")))
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        session = async_get_clientsession(self.hass)
        try:
            models = await _build_setup_provider(cfg).list_models(session)
        except DecisionError:
            return _err("invalid_request", "Could not connect to the decision provider.", 400, rid,
                        key="providerConnectionFailedNoDetail")
        return _ok({"models": models}, request_id=rid)


_CATALOG_REFRESH_MIN_GAP = timedelta(seconds=60)


async def async_refresh_catalog(hass: HomeAssistant, instance_id: str, *, force: bool = False) -> bool:
    """Re-read one conversation account's model catalogue and declared capabilities.

    This is what keeps automatic mode current: it runs on a timer, when an account
    is added or refreshed, and after a provider answers "model not found". A
    per-account minimum gap stops a failing model from turning every turn into a
    catalogue request. Returns True when a catalogue was recorded.
    """
    store = await _get_secret_store(hass)
    cfg = store.resolve(instance_id)
    if cfg is None:
        return False
    attempts: dict[str, datetime] = hass.data.setdefault("_phoenix_catalog_attempts", {})
    now = utcnow()
    if not force and (last := attempts.get(instance_id)) and now - last < _CATALOG_REFRESH_MIN_GAP:
        return False
    attempts[instance_id] = now
    # Listing with the configured model blanked: a provider that cannot answer
    # falls back to "the configured model", which must not be recorded as the
    # whole catalogue.
    provider = build_provider(dataclasses.replace(cfg, model=""))
    session = async_get_clientsession(hass)
    try:
        models = await provider.list_models(session)
        previous = (store.get(instance_id) or {}).get("catalog") or {}
        changed = sorted(models) != sorted(previous.get("models") or [])
        definition = PROVIDER_DEFINITIONS[cfg.kind]
        # Ollama declares capabilities per model (one request each), so it is
        # re-read only when its library actually changed.
        capabilities = (
            await provider.list_model_capabilities(session, models)
            if models and (definition.ollama is None or changed) else None
        )
    except Exception:  # catalogue refresh is best effort
        _LOGGER.debug("Provider catalogue refresh failed", exc_info=True)
        return False
    checked_at = utcnow().isoformat()
    eligible = None
    if capabilities:
        # A model that declares it cannot call tools is no use to Agent Chat, so
        # automatic mode never lands on one (an undeclared capability stays eligible).
        eligible = [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
        await store.set_capabilities(instance_id, capabilities, checked_at)
    return await store.set_catalog(instance_id, models, provider.model_created, checked_at, eligible)


async def async_refresh_all_catalogs(hass: HomeAssistant) -> None:
    """Re-read the catalogues that are due (the scheduled job, run hourly).

    An account that has never had a catalogue is always read: automatic mode
    cannot choose a model without one, so that is not an optional check. Every
    other account is re-read only when the operator has periodic checks enabled
    and its last read is older than the configured period. The last-read time is
    stored with the catalogue, so a restart does not reset the schedule.
    """
    data = hass.data.get(DOMAIN)
    settings = data.store.get_settings() if data is not None else None
    enabled = settings.provider_catalog_refresh_enabled if settings is not None else True
    hours = settings.provider_catalog_refresh_hours if settings is not None else 24
    store = await _get_secret_store(hass)
    now = utcnow()
    for inst in store.list_instances():
        if provider_purpose(inst["kind"]) != "conversation":
            continue
        checked = parse_datetime(inst["models_checked_at"]) if inst.get("models_checked_at") else None
        due = checked is None or (enabled and now - checked >= timedelta(hours=hours))
        if due:
            await async_refresh_catalog(hass, inst["id"], force=True)


class PhoenixAgentCliRefreshView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh.

    Re-read one account's model list AND whatever capabilities its provider
    declares, then store both with a timestamp. Separate from the GET models
    endpoint because the two cost very different things: listing models is one
    cheap request and runs whenever the settings card is opened, while Ollama's
    capabilities cost one request PER MODEL, which is an explicit-button amount
    of work rather than a page-load amount.

    Nothing here spends completion tokens; probing the knobs that no provider
    declares is a separate step the operator consents to (the probe endpoint).
    """

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/refresh"
    name = "api:phoenix-mcp:admin:agentcli:refresh"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id, purpose=provider_purpose((store.get(instance_id) or {}).get("kind", "")))
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(cfg)
        try:
            models = await provider.list_models(session)
            capabilities = await provider.list_model_capabilities(session, models)
        except DecisionError:
            return _err("invalid_request", "Could not connect to the decision provider.", 400, rid,
                        key="providerConnectionFailedNoDetail")
        checked_at = utcnow().isoformat()
        if not await store.set_capabilities(instance_id, capabilities, checked_at):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if provider_purpose(cfg.kind) == "conversation":
            await store.set_catalog(
                instance_id, models, getattr(provider, "model_created", {}), checked_at,
                [m for m in models if (capabilities.get(m) or {}).get("tools") is not False]
                if capabilities else None,
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider_refresh:{instance_id}")
        # `declared` tells the panel whether this provider reports capabilities at
        # all, so it can say "this provider does not publish them" instead of
        # showing an empty result that reads like a failed refresh.
        return _ok({
            "models": models,
            "capabilities": capabilities,
            "declared": bool(capabilities),
            "checked_at": checked_at,
        }, request_id=rid)


class PhoenixAgentCliProbeCapsView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe.

    Ask the provider what its SELECTED model actually accepts, by sending a
    handful of one-token completions and reading the status codes.

    A SEPARATE endpoint from /refresh, and the separation is the consent
    boundary rather than tidiness: /refresh reads catalogues and costs nothing,
    while this spends the operator's own credit on real completion requests.
    Anything that bills someone gets its own button and its own confirmation.

    Only the selected model is probed. Probing a catalogue would multiply the
    spend by models nobody has chosen, and the only model whose knobs are about
    to be used is this one.
    """

    url = "/api/phoenix-mcp/admin/agentcli/providers/{instance_id}/probe"
    name = "api:phoenix-mcp:admin:agentcli:probecaps"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request, instance_id: str) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        store = await _get_secret_store(self.hass)
        cfg = store.resolve(instance_id)
        if cfg is None:
            return _err(
                "not_found", "Provider account is not configured.", 404, rid,
                key="providerNotConfigured",
            )
        if not cfg.model:
            return _err(
                "invalid_request", "Choose a model for this account first.", 400, rid,
                key="providerChooseModel",
            )
        session = async_get_clientsession(self.hass)
        probed, calls, answered = await async_probe_capabilities(session, cfg)
        # Image support is checked only when nothing has said what it is: a
        # provider's own declaration, or a refusal learned in conversation.
        vision_checked = answered and cfg.vision is None
        if vision_checked:
            vision = await async_probe_vision(session, cfg)
            calls += 1
            if vision is not None:
                probed["vision"] = vision

        # Merged over what the provider DECLARED rather than replacing it: the two
        # answer different questions (declared says what exists, probed says what
        # this endpoint accepts) and a probe that established nothing must leave
        # the declared answer standing.
        stored = store.get(instance_id) or {}
        existing = dict(stored.get("capabilities") or {})
        merged = {**(existing.get(cfg.model) or {}), **probed}
        existing[cfg.model] = merged
        checked_at = utcnow().isoformat()
        if not await store.set_capabilities(instance_id, existing, checked_at):
            return _err(
                "not_found", "Provider account not found.", 404, rid,
                key="providerAccountNotFound",
            )
        if self.hass.data.get(DOMAIN) is not None:
            _audit_admin(self.hass, request, rid, f"provider_probe:{instance_id}")
        return _ok({
            "model": cfg.model,
            "probed": probed,
            "calls": calls,
            "checked_at": checked_at,
            # Whether this backend has an effort control to ask about AT ALL.
            # Without it the panel cannot tell "asked, and the provider ignores
            # unknown options" from "there was nothing here to ask" (a backend
            # whose thinking control is a boolean flag).
            "effort_checkable": _effort_probe_body(cfg.kind, AGENTCLI_PROBE_SENTINEL) is not None,
            # False when the provider declined every question (no credit, a bad
            # key, unreachable). Reporting that as a finding about the model
            # would blame the provider for an account problem.
            "answered": answered,
            # Whether the image check ran; probed.vision says what it found.
            "vision_checked": vision_checked,
        }, request_id=rid)


class PhoenixAgentCliProbeView(PhoenixView):
    """POST /api/phoenix-mcp/admin/agentcli/probe.

    Validate a submitted credential/base URL and list the provider's models
    WITHOUT storing anything, so the Settings form can populate a model dropdown
    before the operator commits (and can cancel with no side effect).
    """

    url = "/api/phoenix-mcp/admin/agentcli/probe"
    name = "api:phoenix-mcp:admin:agentcli:probe"
    requires_auth = True

    @require_admin
    async def post(self, request: web.Request) -> web.Response:
        rid = request.get("phoenix_mcp_rid", "")
        body = await _read_body(request, rid)
        if isinstance(body, web.Response):
            return body
        kind = str(body.get("kind") or "")
        if kind not in PROVIDER_DEFINITIONS:
            return _err(
                "invalid_request", "Unknown provider.", 400, rid,
                key="providerUnknown",
            )

        probe = await _probe_config(kind, body)
        if isinstance(probe, ProbeConfigError):
            return _ok(probe.payload(), request_id=rid)
        cfg = _stored_setup_config(probe)
        store = await _get_secret_store(self.hass)
        if store.has_duplicate(kind, cfg):
            return _err(
                "already_exists", str(DuplicateProviderError()), 409, rid,
                key="providerAlreadyConfigured",
            )
        session = async_get_clientsession(self.hass)
        provider = _build_setup_provider(probe)
        ok, reason = await provider.validate(session)
        if not ok:
            if isinstance(reason, LocalizedReason):
                return _ok(ProbeConfigError(reason, reason.key, reason.params or None).payload(), request_id=rid)
            if reason:
                return _ok(
                    {
                        "ok": False,
                        "error": reason,
                        "message_passthrough": True,
                        "models": [],
                    },
                    request_id=rid,
                )
            return _ok(
                ProbeConfigError(
                    "Connection failed.", "providerConnectionFailedNoDetail",
                ).payload(),
                request_id=rid,
            )
        models = await provider.list_models(session)
        return _ok({"ok": True, "models": models}, request_id=rid)


# Admin config/models views are kill-switch-immune (like the rest of the admin
# API); the streaming chat view is agent activity and is registered through the
# kill-switch-gated route set in __init__.py.
ALL_AGENTCLI_ADMIN_VIEWS: list[type[PhoenixView]] = [
    PhoenixAgentCliProvidersView,
    PhoenixAgentCliProviderView,
    PhoenixAgentCliModelsView,
    PhoenixAgentCliRefreshView,
    PhoenixAgentCliProbeCapsView,
    PhoenixAgentCliProbeView,
]
ALL_AGENTCLI_CHAT_VIEWS: list[type[PhoenixView]] = [
    PhoenixAgentCliChatView,
]
