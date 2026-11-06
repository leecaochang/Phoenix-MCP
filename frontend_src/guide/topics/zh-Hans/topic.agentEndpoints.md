---
{ "id": "topic.agentEndpoints", "title": "智能体端点", "parent": "topic.adminApi", "order": 60, "keywords": ["MCP 端点", "JSON-RPC", "智能体 API", "phx_", "Streamable HTTP"], "sectionKeywords": { "routes": ["405", "尾随斜杠", "紧急停止开关"], "bearer": ["Authorization", "Bearer", "401", "WWW-Authenticate", "未授权", "建议"], "origin": ["Origin", "浏览器", "403", "CORS"], "mcp": ["POST", "JSON-RPC", "批处理", "-32700", "-32600", "-32601", "SSE", "text/event-stream", "协议版本", "2026-07-28", "2025-03-26"], "sessions": ["Mcp-Session-Id", "旧版", "会话限制", "initialize"], "end-session": ["DELETE", "关闭会话"], "context": ["上下文文档", "format=json", "实体"], "skill": ["SKILL.md", "技能指南", "Markdown"], "rate-limit": ["X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset", "Retry-After", "429", "rate_limited", "突发"] } }
---
智能体使用 Phoenix 访问密钥（`phx_...`）通过这些路由访问 Phoenix，而不是 Home Assistant 令牌。本页面向构建或检查 MCP 客户端的开发者。

相关：[连接外部应用](guide:topic.connecting)、[访问密钥](guide:topic.security#keys)

## 路由 {#routes}

```
POST   /api/phoenix-mcp
DELETE /api/phoenix-mcp
GET    /api/phoenix-mcp/context
GET    /api/phoenix-mcp/skill
```

智能体向第一个路由发送 MCP 请求，从第二个路由读取其密钥的上下文，并从最后一个路由获取技能指南。

- POST 和 DELETE 也支持带尾随斜杠的 `/api/phoenix-mcp/`。
- `GET /api/phoenix-mcp` 返回 `405`，因为该端点没有 GET 处理器。
- 紧急停止开关开启时，这些路由不会服务智能体。

## 发送密钥 {#bearer}

在 `Authorization` 头中把密钥作为 `Bearer phx_...` 发送。密钥是 `phx_` 后跟 64 个字符。绝不要把它放在 URL 中：即使头正确，`token` 或 `access_token` 查询参数也会被拒绝。

除技能外的每个路由都会按此顺序检查密钥：

| 状态 | `error` | 条件 |
|---|---|---|
| 503 | `service_unavailable` | Phoenix 正在启动或关闭。 |
| 503 | `service_unavailable` | 紧急停止开关已开启。 |
| 401 | `unauthorized` | URL 带有 `token` 或 `access_token` 查询参数。 |
| 401 | `unauthorized` | `Authorization` 头不以 `Bearer ` 开头。 |
| 401 | `unauthorized` | 值不是 `phx_` 后跟 64 个字符。 |
| 401 | `unauthorized` | 没有密钥具有此值。 |
| 401 | `unauthorized` | 密钥已被吊销或已过期。已过期的密钥会移到归档。 |
| 429 | `rate_limited` | 密钥超过其速率限制。参见[速率限制](guide:#rate-limit)。 |

每个 `401` 都带有 `WWW-Authenticate: Bearer realm="Phoenix MCP"`。通过检查的密钥会在速率限制检查前更新其 `last_used_at`。

这些路由上的错误正文比管理 API 更简单：`error` 和 `message`，有时带一个 `suggestions` 数组。它们带有 `X-Phoenix-Request-ID`，但没有 `message_key`，也没有 `Cache-Control`。

```json
{ "error": "unauthorized", "message": "Unauthorized." }
```

相关：[错误](guide:topic.adminApi#errors)

## 浏览器来源 {#origin}

MCP 端点在 POST 和 DELETE 上都会拒绝来自其他站点网页的请求。`Origin` 头不是 Home Assistant 自己的 URL 之一的请求会返回 `403`、`forbidden` 和消息 "Origin not allowed."。Phoenix 会在检查密钥之前检查这一点。

桌面和命令行应用不发送 `Origin`，因此不受影响。上下文路由不检查来源。

## MCP 端点 {#mcp}

`POST /api/phoenix-mcp` 接受一条 JSON-RPC 消息，或一个批处理，最大 1 MiB。Phoenix 支持协议修订 `2026-07-28` 和 `2025-03-26`。修订 `2025-03-26` 在下文称为旧版。

| 状态 | 正文 | 条件 |
|---|---|---|
| 200 | JSON-RPC 结果 | 请求成功。 |
| 202 | 无 | 消息是通知。 |
| 400 | JSON-RPC 错误 `-32700` | 正文为空或不是有效 JSON。 |
| 400 | JSON-RPC 错误 `-32600` | 正文不是 JSON 对象或批处理。 |
| 200 | JSON-RPC 错误 `-32601` | 旧版：方法不存在。 |
| 404 | 错误 | `2026-07-28`：方法不存在。 |
| 400 | JSON-RPC 错误 `-32600` | `2026-07-28` 下的批处理，过大或为空的旧版批处理，或不含请求且有消息被拒绝的旧版批处理。 |
| 413 | `request_too_large` | 正文超过 1 MiB。 |
| 403 | `forbidden` | `Origin` 不被允许。 |
| 503 | `service_unavailable` | Phoenix 尚未就绪。 |

与请求不匹配的协议头会返回 JSON-RPC 错误 `-32020`。不支持的协议版本会返回 `-32022`。

要以流方式获取响应，请发送 `Accept: text/event-stream`。Phoenix 会把单个响应作为一条 SSE `message` 事件发送，并在工作时每 15 秒发送保活。

### 批处理 {#batches}

只有旧版请求可以是批处理。当批处理超过 50 项、超过密钥的突发上限，或超过其每分钟限制时，Phoenix 会以 `400` 和 `-32600` 拒绝它。

Phoenix 在运行前会按整个批处理计入速率限制。当剩余限制太少时，批处理会返回 `429`、`rate_limited` 和 `Retry-After`，且其中没有任何内容运行。

### 旧版会话 {#sessions}

在旧版修订下，`initialize` 返回 `Mcp-Session-Id` 头。在之后每个请求中发送它。

- Phoenix 总共最多保留 256 个会话，每个密钥最多 8 个。
- 会话在无使用一小时后关闭；如果从未完成初始化，则在 60 秒后关闭。
- 达到会话限制时，Phoenix 会关闭该密钥最近最少使用的空闲会话。
- 当没有空闲会话时，请求返回 `429`、JSON-RPC 错误 `-32000` 和 `Retry-After`。
- 未知的 `Mcp-Session-Id` 返回 `404` 和 `-32000`。缺失时返回 `400`。

当密钥自己的会话都忙时，`Retry-After` 为 5 秒。否则它是直到最旧会话过期的时间，至少 5 秒。

### 结束会话 {#end-session}

`DELETE /api/phoenix-mcp` 关闭旧版会话。它运行与 POST 相同的就绪、来源和密钥检查，包括速率限制。响应为 `204`，没有正文。

| 状态 | `error` | 条件 |
|---|---|---|
| 400 | `invalid_request` | 缺少 `Mcp-Session-Id` 头。 |
| 404 | `not_found` | 会话不存在、已过期或属于另一个密钥。 |

## 上下文 {#context}

`GET /api/phoenix-mcp/context` 描述密钥可以访问什么。默认以纯文本返回；添加 `?format=json` 获取 JSON。它会运行密钥检查和速率限制，每次调用都会进入审计日志。

| 字段 | 类型 | 说明 |
|---|---|---|
| `access_key_name` | string | |
| `pass_through` | boolean | |
| `persona` | string | |
| `notes` | string 或 null | 密钥的备注。 |
| `entities` | array | 每个有 `entity_id`、`permission`（`READ` 或 `READ/WRITE`）、`area_id`，以及设置后的 `hint`。 |
| `capability_flags` | object | 适用于智能体的能力值。 |
| `rate_limit` | object | `requests_per_minute` 和 `burst_per_second`。 |

Home Companion 密钥只会收到 `access_key_name`、`persona`、空的 `entities`、`capability_flags` 和 `message`。

## 技能 {#skill}

`GET /api/phoenix-mcp/skill` 以 Markdown（`text/markdown`）返回 Phoenix MCP 技能指南。它不需要密钥，不受速率限制，也不包含关于你家的任何内容。它对所有人都是相同的。

Phoenix 正在启动或关闭，或紧急停止开关开启时，它返回 `503` 和纯文本 "Service unavailable."。其响应不带有 `X-Phoenix-Request-ID`。

相关：[智能体会收到什么](guide:topic.connecting#primer)

## 速率限制 {#rate-limit}

每个密钥有两个限制。`rate_limit_requests` 统计滑动 60 秒内的请求。`rate_limit_burst` 统计任意一秒内的请求；0 会关闭突发限制。`rate_limit_requests` 为 0 会同时关闭两者。

相关：[{{label:accessKeys.cardRateLimiting}}](guide:window.keySettings#rate-limiting)

### 限制头 {#limit-headers}

成功的 MCP 响应会告诉智能体它的状况：

```http
X-RateLimit-Limit: 60
X-RateLimit-Remaining: 47
X-RateLimit-Reset: 1793612345
```

| 头 | 含义 |
|---|---|
| `X-RateLimit-Limit` | 密钥的 `rate_limit_requests`。 |
| `X-RateLimit-Remaining` | 当前 60 秒内，在本次请求之后剩余的请求数。批处理会计入每一项。 |
| `X-RateLimit-Reset` | Unix 时间，以整秒为单位，表示窗口中最旧的请求离开窗口的时间。 |

这些头只出现在 `POST /api/phoenix-mcp` 的 `200` JSON 和 SSE 响应中，包括批处理。通知、协议错误、其他错误、DELETE、上下文和技能不会收到它们。关闭速率限制的密钥不会收到任何头。

### 达到限制时 {#limit-hit}

请求返回 `429`、`rate_limited` 和一个以秒为单位的 `Retry-After` 头，至少为 1。它是直到足够多的旧请求离开分钟或突发窗口的时间。`429` 不带有 `X-RateLimit` 头。

```json
{ "error": "rate_limited", "message": "Rate limit exceeded." }
```

在重试前等待 `Retry-After`。

相关：[密钥统计](guide:topic.adminApiKeys#key-stats)
