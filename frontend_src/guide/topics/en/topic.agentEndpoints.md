---
{ "id": "topic.agentEndpoints", "title": "Agent Endpoints", "parent": "topic.adminApi", "order": 60, "keywords": ["MCP endpoint", "JSON-RPC", "agent API", "phx_", "Streamable HTTP"], "sectionKeywords": { "routes": ["405", "trailing slash", "kill switch"], "bearer": ["Authorization", "Bearer", "401", "WWW-Authenticate", "unauthorized", "suggestions"], "origin": ["Origin", "browser", "403", "CORS"], "mcp": ["POST", "JSON-RPC", "batch", "-32700", "-32600", "-32601", "SSE", "text/event-stream", "protocol version", "2026-07-28", "2025-03-26"], "sessions": ["Mcp-Session-Id", "legacy", "session limit", "initialize"], "end-session": ["DELETE", "close session"], "context": ["context document", "format=json", "entities"], "skill": ["SKILL.md", "skill guide", "Markdown"], "rate-limit": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After", "429", "rate_limited", "burst"] }, "covers": ["custom_components/phoenix_mcp/mcp_view.py", "custom_components/phoenix_mcp/skill_view.py", "custom_components/phoenix_mcp/rate_limiter.py"] }
---
Agents reach Phoenix through these routes with a Phoenix access key (`phx_...`), not a Home Assistant token. This page is for developers building an MCP client or checking one.

Related: [Connecting External Apps](guide:topic.connecting), [Access keys](guide:topic.security#keys)

## Routes {#routes}

```
POST   /api/phoenix-mcp
DELETE /api/phoenix-mcp
GET    /api/phoenix-mcp/context
GET    /api/phoenix-mcp/skill
```

Agents send MCP requests to the first route, read their key's context from the second, and fetch the skill guide from the last.

- POST and DELETE also work with a trailing slash, `/api/phoenix-mcp/`.
- `GET /api/phoenix-mcp` answers `405`, since the endpoint has no GET handler.
- While the kill switch is on, these routes don't serve agents.

## Sending the key {#bearer}

Send the key in the `Authorization` header as `Bearer phx_...`. The key is `phx_` followed by 64 characters. Never put it in the URL: a `token` or `access_token` query parameter is refused even when the header is right.

Every route but the skill checks the key, in this order:

| Status | `error` | When |
|---|---|---|
| 503 | `service_unavailable` | Phoenix is starting or shutting down. |
| 503 | `service_unavailable` | The kill switch is on. |
| 401 | `unauthorized` | The URL has a `token` or `access_token` query parameter. |
| 401 | `unauthorized` | The `Authorization` header doesn't start with `Bearer `. |
| 401 | `unauthorized` | The value isn't `phx_` followed by 64 characters. |
| 401 | `unauthorized` | No key has this value. |
| 401 | `unauthorized` | The key is revoked or has expired. An expired key moves to the archive. |
| 429 | `rate_limited` | The key is over its rate limit. See [Rate limits](guide:#rate-limit). |

Every `401` carries `WWW-Authenticate: Bearer realm="Phoenix MCP"`. A key that passes gets its `last_used_at` updated before the rate limit check.

Errors on these routes have a simpler body than the admin API's: `error` and `message`, sometimes with a `suggestions` array. They carry `X-Phoenix-Request-ID` but no `message_key` and no `Cache-Control`.

```json
{ "error": "unauthorized", "message": "Unauthorized." }
```

Related: [Errors](guide:topic.adminApi#errors)

## Browser origins {#origin}

The MCP endpoint refuses requests from web pages on other sites, on both POST and DELETE. A request whose `Origin` header isn't one of Home Assistant's own URLs answers `403` with `forbidden` and the message "Origin not allowed." Phoenix checks this before the key.

Desktop and command-line apps send no `Origin`, so this doesn't affect them. The context route doesn't check the origin.

## The MCP endpoint {#mcp}

`POST /api/phoenix-mcp` takes one JSON-RPC message, or a batch, of up to 1 MiB. Phoenix supports protocol revisions `2026-07-28` and `2025-03-26`. Revision `2025-03-26` is called legacy below.

| Status | Body | When |
|---|---|---|
| 200 | JSON-RPC result | A request succeeded. |
| 202 | None | The message was a notification. |
| 200 | JSON-RPC error `-32700` | The body is empty or isn't valid JSON. |
| 200 | JSON-RPC error `-32600` | The body isn't a JSON object or batch. |
| 200 | JSON-RPC error `-32601` | Legacy: the method doesn't exist. |
| 404 | Error | `2026-07-28`: the method doesn't exist. |
| 400 | JSON-RPC error `-32600` | A batch under `2026-07-28`, or a legacy batch that's too big. |
| 413 | `request_too_large` | The body is over 1 MiB. |
| 403 | `forbidden` | The `Origin` isn't allowed. |
| 503 | `service_unavailable` | Phoenix isn't ready yet. |

A protocol header that doesn't match the request answers JSON-RPC error `-32020`. An unsupported protocol version answers `-32022`.

To get the response as a stream, send `Accept: text/event-stream`. Phoenix sends the single response as one SSE `message` event, with a keepalive every 15 seconds while it works.

### Batches {#batches}

Only legacy requests may be batches. Phoenix refuses a batch with `400` and `-32600` when it has more than 50 items, more than the key's burst, or more than its per-minute limit.

Phoenix charges the whole batch against the rate limit before running it. When too little of the limit is left, the batch answers `429` with `rate_limited` and `Retry-After`, and nothing in it runs.

### Legacy sessions {#sessions}

Under the legacy revision, `initialize` returns an `Mcp-Session-Id` header. Send it on every later request.

- Phoenix holds at most 256 sessions in all and 8 per key.
- A session closes after one hour without use, or after 60 seconds if it never finishes initializing.
- When the session limit is reached, Phoenix closes the key's least recently used idle session.
- When none is idle, the request answers `429` with JSON-RPC error `-32000` and `Retry-After`.
- An unknown `Mcp-Session-Id` answers `404` with `-32000`. A missing one answers `400`.

When the key's own sessions are all busy, `Retry-After` is 5 seconds. Otherwise it's the time until the stalest session expires, at least 5 seconds.

### Ending a session {#end-session}

`DELETE /api/phoenix-mcp` closes a legacy session. It runs the same readiness, origin and key checks as POST, including the rate limit. The response is `204` with no body.

| Status | `error` | When |
|---|---|---|
| 400 | `invalid_request` | The `Mcp-Session-Id` header is missing. |
| 404 | `not_found` | The session doesn't exist, has expired or belongs to another key. |

## Context {#context}

`GET /api/phoenix-mcp/context` describes what the key can reach. It answers in plain text by default; add `?format=json` for JSON. It runs the key check and the rate limit, and every call goes in the audit log.

| Field | Type | Notes |
|---|---|---|
| `access_key_name` | string | |
| `pass_through` | boolean | |
| `persona` | string | |
| `notes` | string or null | The key's notes. |
| `entities` | array | Each has `entity_id`, `permission` (`READ` or `READ/WRITE`), `area_id` and, when set, `hint`. |
| `capability_flags` | object | The capability values that apply to the agent. |
| `rate_limit` | object | `requests_per_minute` and `burst_per_second`. |

A Home Companion key gets only `access_key_name`, `persona`, an empty `entities`, `capability_flags` and `message`.

## Skill {#skill}

`GET /api/phoenix-mcp/skill` answers with the Phoenix MCP skill guide as Markdown (`text/markdown`). It needs no key, isn't rate limited and holds nothing about your home. It's the same for everyone.

While Phoenix is starting or shutting down, or the kill switch is on, it answers `503` with the plain text "Service unavailable." Its responses carry no `X-Phoenix-Request-ID`.

Related: [What the agent is told](guide:topic.connecting#primer)

## Rate limits {#rate-limit}

Each key has two limits. `rate_limit_requests` counts requests over a sliding 60 seconds. `rate_limit_burst` counts requests in any one second; 0 turns the burst limit off. A `rate_limit_requests` of 0 turns both off.

Related: [{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

### Limit headers {#limit-headers}

Successful MCP responses tell the agent where it stands:

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 47
X-RateLimit-Reset: 1793612345
```

| Header | Meaning |
|---|---|
| `X-RateLimit-Limit` | The key's `rate_limit_requests`. |
| `X-RateLimit-Remaining` | Requests left in the current 60 seconds, after this one. A batch counts every item. |
| `X-RateLimit-Reset` | Unix time, in whole seconds, when the oldest request in the window drops out of it. |

These headers appear only on `200` JSON and SSE responses from `POST /api/phoenix-mcp`, batches included. Notifications, protocol errors, other errors, DELETE, context and skill don't get them. A key with rate limiting off gets none.

### When the limit is hit {#limit-hit}

The request answers `429` with `rate_limited` and a `Retry-After` header in seconds, at least 1. It's the time until enough old requests drop out of the minute or burst window. A `429` carries no `X-RateLimit` headers.

```json
{ "error": "rate_limited", "message": "Rate limit exceeded." }
```

Wait for `Retry-After` before you try again.

Related: [Key statistics](guide:topic.adminApiKeys#key-stats)
