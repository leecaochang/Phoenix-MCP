---
{ "id": "topic.adminApi", "title": "管理 API", "parent": "topic.about", "order": 60, "keywords": ["API", "REST", "开发者", "端点", "集成", "脚本", "curl"], "sectionKeywords": { "auth": ["身份验证", "长期访问令牌", "401", "403", "管理员", "Bearer"], "responses": ["信封", "X-Phoenix-Request-ID", "Cache-Control", "204"], "errors": ["错误代码", "message_key", "message_params", "message_passthrough", "状态代码"], "bodies": ["413", "请求体", "JSON", "分页", "limit", "offset"], "ids": ["节点 ID", "实体 ID", "域"], "states": ["GREY", "YELLOW", "GREEN", "RED", "权限状态"] } }
---
面板构建在 Phoenix 的管理 API 之上，你也可以使用它：从脚本、自动化工具或你自己的应用。面板能做的一切，API 都能做。这些页面面向开发者。

相关：[访问密钥端点](guide:topic.adminApiKeys)、[审批与变更端点](guide:topic.adminApiApprovals)、[MESA 端点](guide:topic.adminApiMesa)、[Agent Chat 端点](guide:topic.adminApiChat)、[设置与系统端点](guide:topic.adminApiSettings)、[智能体端点](guide:topic.agentEndpoints)

## 登录 {#auth}

每个管理端点都位于 `/api/phoenix-mcp/admin` 下，并且需要 Home Assistant 管理员。发送任何管理员的令牌，例如长期访问令牌，作为 `Authorization: Bearer <token>`。

- 没有有效令牌时，Home Assistant 本身会在 Phoenix 运行前返回 `401`，没有 JSON 正文。
- 已登录但不是管理员的用户会收到 `403`，错误为 `forbidden`。
- Phoenix 仍在启动时，除目录外的每个端点都会返回 `503`，错误为 `service_unavailable`。
- Phoenix 访问密钥（`phx_...`）无法打开这些端点。它们仅供智能体使用。

紧急停止开关开启时，管理 API 仍会工作。

相关：[管理员一侧](guide:topic.security#admin)

## 响应 {#responses}

成功的调用会返回 `200` 或 `201` 和 JSON 正文，或返回 `204` 且没有正文。每个响应都带有 `X-Phoenix-Request-ID`，审计日志和 Home Assistant 的日志也会记录它。带正文的响应还会添加 `Cache-Control: no-store`。

你通过 API 所做的每项更改都会以发起更改的管理员身份向审计日志添加一条条目。读取不会被记录。

## 错误 {#errors}

错误会返回状态代码和此正文：

```json
{
  "error": "invalid_request",
  "message": "English description of the problem.",
  "message_key": "adminError.badJson",
  "message_params": {}
}
```

- `error` 是下面代码之一。请针对它编写代码，而不是针对消息。
- `message_key` 指定面板对消息的翻译，`message_params` 填充它。`message_params` 仅在消息有值时出现。
- 来自 Phoenix 外部的文本，例如 AI 提供商自己的错误，会带有 `message_passthrough: true`，而不是键。

| 代码 | 通常状态 | 含义 |
|---|---|---|
| `invalid_request` | 400 | 请求格式错误，或某个值超出范围。 |
| `unauthorized` | 401 | 未登录。 |
| `forbidden` | 403 | 已登录，但不是管理员。 |
| `not_found` | 404 | 项目不存在。 |
| `conflict` | 409 | 项目的当前状态不允许此操作，例如名称已被占用。 |
| `already_exists` | 409 | 项目已存在。 |
| `stale_version` | 409 | 另一项更改先到达。请重新读取并重试。 |
| `restore_unverified` | 409 | 已发送还原，但结果不匹配。重试前请检查。 |
| `precondition_failed` | 412 | 缺少必需的确认或条件。 |
| `request_too_large` | 413 | 正文超过 1 MiB。 |
| `internal_error` | 500 | Phoenix 内部有内容失败。 |
| `service_unavailable` | 503 | Phoenix 正在启动，或该功能已关闭。 |

## 请求体 {#bodies}

发送最大 1 MiB 的 JSON 对象。空正文视为 `{}`。无论 Content-Type 是什么，Phoenix 都会把正文按 JSON 读取。

| 问题 | 状态 | `message_key` |
|---|---|---|
| 正文超过 1 MiB | 413 | `adminError.bodyTooLarge` |
| 正文无法读取 | 400 | `adminError.bodyUnreadable` |
| 不是有效 JSON | 400 | `adminError.badJson` |
| JSON 不是对象 | 400 | `adminError.bodyNotObject` |

分页列表接受 `limit` 和 `offset`，两者都是大于等于 0 的整数。每个端点都会限制 `limit`；错误的值会返回 `400` 和 `adminError.badPagination` 或 `adminError.paginationNonNegative`。

## 路径中的 ID {#ids}

- 域是小写字母、数字和下划线，以字母开头，例如 `light`。
- 实体 ID 是域、一个点，以及小写字母、数字和下划线，例如 `light.kitchen`。
- 设备 ID 是 Home Assistant 的设备注册表 ID。
- 任何 ID 最多 255 个字符，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;`。

## 权限状态 {#states}

API 对四种权限状态的命名与面板不同：

| API | 面板 | 含义 |
|---|---|---|
| `GREY` | N | 没有自己的设置；沿用上一级。 |
| `YELLOW` | R | 只读。 |
| `GREEN` | W | 读取和控制。 |
| `RED` | D | 拒绝访问。 |

相关：[权限如何判定](guide:topic.permissionResolution)
