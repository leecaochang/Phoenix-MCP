---
{ "id": "topic.adminApi", "title": "Admin API", "parent": "topic.about", "order": 60, "keywords": ["API", "REST", "developer", "endpoint", "integration", "script", "curl"], "sectionKeywords": { "auth": ["authentication", "long-lived access token", "401", "403", "administrator", "Bearer"], "responses": ["envelope", "X-Phoenix-Request-ID", "Cache-Control", "204"], "errors": ["error code", "message_key", "message_params", "message_passthrough", "status code"], "bodies": ["413", "request body", "JSON", "pagination", "limit", "offset"], "ids": ["node id", "entity id", "domain"], "states": ["GREY", "YELLOW", "GREEN", "RED", "permission state"] }, "covers": ["custom_components/phoenix_mcp/admin/common.py", "custom_components/phoenix_mcp/admin/registry.py", "custom_components/phoenix_mcp/agent_chat_views.py", "custom_components/phoenix_mcp/provider_constants.py", "custom_components/phoenix_mcp/const.py"] }
---
The panel is built on Phoenix's admin API, and you can use it too: from a script, an automation tool or your own app. Everything the panel does, the API does. These pages are for developers.

Related: [Access Key Endpoints](guide:topic.adminApiKeys), [Approval and Change Endpoints](guide:topic.adminApiApprovals), [MESA Endpoints](guide:topic.adminApiMesa), [Agent Chat Endpoints](guide:topic.adminApiChat), [Settings and System Endpoints](guide:topic.adminApiSettings), [Agent Endpoints](guide:topic.agentEndpoints)

## Signing in {#auth}

Every admin endpoint lives under `/api/phoenix-mcp/admin` and needs a Home Assistant administrator. Send any administrator's token, such as a long-lived access token, as `Authorization: Bearer <token>`.

- Without a valid token, Home Assistant itself answers `401` before Phoenix runs, with no JSON body.
- A signed-in user who isn't an administrator gets `403` with error `forbidden`.
- While Phoenix is still starting, every endpoint except the catalog answers `503` with error `service_unavailable`.
- Phoenix access keys (`phx_...`) can't open these endpoints. They're for agents only.

The admin API keeps working while the kill switch is on.

Related: [The administrator side](guide:topic.security#admin)

## Responses {#responses}

A successful call answers `200` or `201` with a JSON body, or `204` with no body. Every response carries `X-Phoenix-Request-ID`, which the audit log and Home Assistant's log also record. Responses with a body add `Cache-Control: no-store`.

Every change you make through the API adds one entry to the audit log, under the administrator who made it. Reads aren't recorded.

## Errors {#errors}

An error answers with a status code and this body:

```json
{
  "error": "invalid_request",
  "message": "English description of the problem.",
  "message_key": "adminError.badJson",
  "message_params": {}
}
```

- `error` is one of the codes below. Code against it, not the message.
- `message_key` names the panel's translation of the message, and `message_params` fills it in. `message_params` appears only when the message has values.
- Text from outside Phoenix, such as an AI provider's own error, comes with `message_passthrough: true` instead of a key.

| Code | Usual status | Meaning |
|---|---|---|
| `invalid_request` | 400 | The request is malformed or a value is out of range. |
| `unauthorized` | 401 | Not signed in. Home Assistant normally answers first, as above. If Phoenix answers, `message_key` is `adminError.authRequired`. |
| `forbidden` | 403 | Signed in, but not an administrator. |
| `not_found` | 404 | The item doesn't exist. |
| `conflict` | 409 | The item's current state doesn't allow this, such as a name already taken. |
| `already_exists` | 409 | The item already exists. |
| `stale_version` | 409 | Another change landed first. Read again and retry. |
| `restore_unverified` | 409 | A restore was sent, but the result doesn't match. Check before retrying. |
| `precondition_failed` | 412 | A required confirmation or condition is missing. |
| `request_too_large` | 413 | The body is over 1 MiB. |
| `internal_error` | 500 | Something failed inside Phoenix. |
| `service_unavailable` | 503 | Phoenix is starting, or the feature is turned off. |

## Request bodies {#bodies}

Send a JSON object of up to 1 MiB. An empty body counts as `{}`. Phoenix reads the body as JSON whatever the Content-Type says.

| Problem | Status | `message_key` |
|---|---|---|
| Body over 1 MiB | 413 | `adminError.bodyTooLarge` |
| Body can't be read | 400 | `adminError.bodyUnreadable` |
| Not valid JSON | 400 | `adminError.badJson` |
| JSON that isn't an object | 400 | `adminError.bodyNotObject` |

Lists that page take `limit` and `offset`, both whole numbers of 0 or more. Each endpoint caps `limit`; a bad value answers `400` with `adminError.badPagination` or `adminError.paginationNonNegative`.

## IDs in paths {#ids}

- A domain is lower-case letters, digits and underscores, starting with a letter, such as `light`.
- An entity ID is a domain, a dot and lower-case letters, digits and underscores, such as `light.kitchen`.
- A device ID is Home Assistant's device registry ID.
- Any ID is at most 255 characters and can't contain `<`, `>`, `"`, `'`, `&` or `;`.

## Permission states {#states}

The API names the four permission states differently from the panel:

| API | Panel | Meaning |
|---|---|---|
| `GREY` | N | No setting of its own; follows the level above. |
| `YELLOW` | R | Read only. |
| `GREEN` | W | Read and control. |
| `RED` | D | Deny. |

Related: [How a Permission Is Decided](guide:topic.permissionResolution)
