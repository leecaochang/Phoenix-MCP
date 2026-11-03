---
{ "id": "topic.adminApiMesa", "title": "MESA 端点", "parent": "topic.adminApi", "order": 30, "keywords": ["MESA API", "策略 API", "语义策略", "隐私分类", "安全策略"], "sectionKeywords": { "shared": ["mesaUnavailable", "503", "审计日志", "租约", "mesa_request_lease", "mesa_release_lease"], "entity-profiles": ["实体策略", "cursor", "next_cursor", "origin"], "list-profiles": ["筛选", "标签", "区域", "inferred_ai", "has_more", "total_matched"], "read-profile": ["effective", "explanation", "resolved profile"], "save-profile": ["If-None-Match", "仅创建", "semantic_profile", "privacy_classification", "warnings", "mesaProfileExists"], "scope-profiles": ["设备策略", "区域策略", "集成策略", "域策略"], "picker-lists": ["device-options", "integration-options"], "defaults": ["deployment_defaults", "default_control_mode", "triggers_automations_domains", "domain_overrides", "autonomous", "confirm", "read_only", "prohibited"], "issues": ["词汇表", "canonical_tags", "canonical_roots", "触发问题", "孤立", "刷新"], "clear-orphans": ["孤立策略", "清理"], "portability": ["备份", "存档", "mesa_export", "format_version"], "import": ["on_conflict", "overwrite", "skip", "skipped_existing", "invalid"], "suggestions": ["忽略", "dismissed_suggestions", "恢复建议"] } }
---
这些端点读取和更改 MESA：各层级的策略、部署默认设置、标签词汇表、问题和建议，以及可移植存档。登录、错误正文和请求体如[管理 API](guide:topic.adminApi)所述。

相关：[错误](guide:topic.adminApi#errors)、[请求体](guide:topic.adminApi#bodies)、[MESA 如何决定](guide:topic.mesaRules)

## 适用于每个 MESA 端点 {#shared}

除 `device-options`、`integration-options` 和 `vocabulary` 外，每个 MESA 端点都需要 MESA 正在运行。未运行时，它们返回：

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 503 | `service_unavailable` | `adminError.mesaUnavailable` | MESA 未运行。 |

每次更改都会添加一条包含请求路径的审计日志条目。导出也会添加一条，尽管它不更改任何内容。

MESA 租约没有管理端点。Phoenix 把它们保存在内存中，智能体用自己的工具请求和释放它们。

## 实体策略 {#entity-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/profiles
GET    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
PUT    /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
DELETE /api/phoenix-mcp/admin/mesa/profiles/{entity_id}
```

使用它们查找、读取、保存和删除一个实体的 MESA 策略。

相关：[策略](guide:window.mesaProfiles)、[层级](guide:topic.mesaRules#levels)

### 列出实体策略 {#list-profiles}

列出与发送的每个筛选条件都匹配的已存储实体策略，一次一页。推断和未知来源也会包含。

| 字段 | 类型 | 说明 |
|---|---|---|
| `domain` | query, string | 仅列出此域中的实体。 |
| `tag` | query, string | 仅列出有效标签包含此标签的策略。 |
| `area` | query, string | 仅列出此区域中的实体。 |
| `origin` | query, string | `developer`、`user`、`hybrid`、`inferred_ai` 或 `unknown`。 |
| `limit` | query, integer | 默认 50。1 到 200 之外的值按最近的边界计算。 |
| `cursor` | query, string | 上一页的 `next_cursor`。第一页省略它。 |

响应为 `{ profiles, total_matched, has_more, next_cursor }`。`profiles` 中的每一项为 `{ entity_id, document }`。当 `has_more` 为 `true` 时，用 `cursor` 请求下一页。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.integerRequired` | `limit` 不是整数。`message_params.field` 为 `limit`。 |
| 400 | `invalid_request` | 无，`message_passthrough` | MESA 拒绝了游标或筛选条件。消息来自 MESA 本身。 |

### 读取实体策略 {#read-profile}

响应为 `{ entity_id, stored, effective, explanation }`：

- `stored` 是实体自己的策略文档，没有时为 `null`。
- `effective` 是合并所有层级后 MESA 应用的策略。
- `explanation` 是 MESA 对其如何得出该结果的说明。

相关：[策略如何组合](guide:topic.mesaRules#combining)

### 保存实体策略 {#save-profile}

保存实体的策略，替换它原有的策略。发送一个策略文档：

```json
{
  "semantic_profile": { },
  "privacy_classification": { }
}
```

你也可以发送一个裸语义策略。读取响应中的 `stored` 字段具有相同形状。Phoenix 会用当前模式版本给文档打上标记，如果文档未指定来源，则以来源 `user` 保存。

要在不覆盖现有策略的情况下创建策略，请发送头 `If-None-Match: *`。如果实体已有策略，则不保存任何内容，你会收到 `412`。

路径中的实体 ID 必须是一个域、一个点，然后是小写字母、数字和下划线。读取和删除使用相同形式。

响应为 `{ entity_id, stored, warnings }`。`warnings` 列出 MESA 为此实体发现的触发问题，形状见[问题和标签词汇表](guide:#issues)。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.badEntityId` | 实体 ID 格式错误。 |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | 你发送了 `If-None-Match: *`，但策略已存在。 |
| 400 | `invalid_request` | 无，`message_passthrough` | MESA 拒绝了文档。消息来自 MESA 本身。 |

### 删除实体策略 {#delete-profile}

移除实体自己的策略，使它再次遵循上层。响应为 `{ entity_id, "deleted": true }`，即使之前没有策略也是如此。

## 其他层级的策略 {#scope-profiles}

```
GET    /api/phoenix-mcp/admin/mesa/devices
GET    /api/phoenix-mcp/admin/mesa/devices/{device_id}
PUT    /api/phoenix-mcp/admin/mesa/devices/{device_id}
DELETE /api/phoenix-mcp/admin/mesa/devices/{device_id}
GET    /api/phoenix-mcp/admin/mesa/device-options
GET    /api/phoenix-mcp/admin/mesa/areas
GET    /api/phoenix-mcp/admin/mesa/areas/{area_id}
PUT    /api/phoenix-mcp/admin/mesa/areas/{area_id}
DELETE /api/phoenix-mcp/admin/mesa/areas/{area_id}
GET    /api/phoenix-mcp/admin/mesa/integrations
GET    /api/phoenix-mcp/admin/mesa/integrations/{integration}
PUT    /api/phoenix-mcp/admin/mesa/integrations/{integration}
DELETE /api/phoenix-mcp/admin/mesa/integrations/{integration}
GET    /api/phoenix-mcp/admin/mesa/integration-options
GET    /api/phoenix-mcp/admin/mesa/domains
GET    /api/phoenix-mcp/admin/mesa/domains/{domain}
PUT    /api/phoenix-mcp/admin/mesa/domains/{domain}
DELETE /api/phoenix-mcp/admin/mesa/domains/{domain}
```

用于覆盖设备、区域、集成或域的策略。它们的工作方式与实体策略相同，只是键不同。

| 层级 | 响应中的键 | 保存时的键格式 | 错误键 |
|---|---|---|---|
| 设备 | `device_id` | 最多 255 个字符，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;` | `adminError.nodeIdTooLong`、`adminError.nodeIdInvalidChars` |
| 区域 | `area_id` | 最多 255 个字符，不能包含 `<`、`>`、`"`、`'`、`&` 或 `;` | `adminError.nodeIdTooLong`、`adminError.nodeIdInvalidChars` |
| 集成 | `integration` | 一个小写字母，然后是小写字母、数字和下划线 | `adminError.integrationNameInvalid` |
| 域 | `domain` | 一个小写字母，然后是小写字母、数字和下划线 | `adminError.badDomain` |

相关：[路径中的 ID](guide:topic.adminApi#ids)

### 列表 {#scope-lists}

每个列表返回一个以其层级命名的数组，按键排序：`{ devices }`、`{ areas }`、`{ integrations }` 或 `{ domains }`。每一项为 `{ <key>, document }`，例如 `{ device_id, document }`。这些列表不分页。

### 读取、保存和删除 {#scope-profile}

- 读取返回 `{ <key>, stored }`，没有策略时 `stored` 为 `null`。只有实体读取会添加 `effective` 和 `explanation`。
- 保存接受与[保存实体策略](guide:#save-profile)相同的文档和 `If-None-Match: *` 头。它返回 `{ <key>, stored }`，不含 `warnings`。
- 删除返回 `{ <key>, "deleted": true }`，即使之前没有策略也是如此。
- 只有保存会检查键格式。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | 上表中该键的错误 | 键格式错误。仅限保存。 |
| 412 | `precondition_failed` | `adminError.mesaProfileExists` | 你发送了 `If-None-Match: *`，但策略已存在。 |
| 400 | `invalid_request` | 无，`message_passthrough` | MESA 拒绝了文档。消息来自 MESA 本身。 |

### 选择器列表 {#picker-lists}

这些列表列出你可以把策略授予什么，供你自己的应用中的选择器使用。它们在 MESA 关闭时也能工作。

- `device-options` 返回 `{ devices: [{ id, name }] }`，按名称排序。名称是用户给设备起的名字，否则是设备名称，否则是其 ID。
- `integration-options` 返回 `{ integrations: [{ id, name }] }`：每个创建过实体的集成，Phoenix MCP 本身除外。

## 部署默认设置 {#defaults}

```
GET    /api/phoenix-mcp/admin/mesa/defaults
PUT    /api/phoenix-mcp/admin/mesa/defaults
```

用于读取和设置 MESA 对任何层级都没有策略的实体应用什么。部署默认设置是三个设置，而不是策略文档。

相关：[内置基线](guide:topic.mesaRules#baseline)

### 读取默认设置 {#read-defaults}

MESA 自己的格式会再次包装设置，因此响应会嵌套两次 `deployment_defaults`。未存储默认设置时，外层为 `null`：

```json
{
  "deployment_defaults": {
    "deployment_defaults": {
      "default_control_mode": "confirm",
      "triggers_automations_domains": [],
      "domain_overrides": {}
    }
  }
}
```

### 设置默认设置 {#set-defaults}

发送 `{ "deployment_defaults": { ... } }`，或只发送内部对象，且只包含这些键：

| 字段 | 类型 | 说明 |
|---|---|---|
| `default_control_mode` | string | `autonomous`、`confirm`、`read_only` 或 `prohibited`。默认 `confirm`。 |
| `triggers_automations_domains` | 字符串数组 | 域，以字符串表示。 |
| `domain_overrides` | object | 以域为键。每个值是一个对象。 |

响应与读取具有相同形状。

## 问题和标签词汇表 {#issues}

```
GET    /api/phoenix-mcp/admin/mesa/vocabulary
GET    /api/phoenix-mcp/admin/mesa/issues
POST   /api/phoenix-mcp/admin/mesa/orphans/clear
```

用于读取 MESA 知道的标签、查找与自动化不一致的策略，以及清理已移除项目留下的策略。

### 词汇表 {#vocabulary}

返回 `{ canonical_tags, canonical_roots }`，两者都已排序。它在 MESA 关闭时也能工作。

### 问题、孤立项和建议 {#read-issues}

| 字段 | 类型 | 说明 |
|---|---|---|
| `refresh` | query, string | 省略它以使用已存储的列表。`suggestions` 仅重新计算建议。其他任何值会重新计算所有内容。 |

响应为 `{ issues, orphans, orphan_devices, orphan_areas, orphan_integrations, suggestions, dismissed_suggestions }`。

- 一个问题为 `{ entity_id, declared_value, automation_id, role, severity, recommendation }`。
- 建议具有相同形状，另加一个用于忽略或恢复它们的 `key`。
- 孤立项列表列出其实体、设备、区域或集成已不存在的策略。

### 清除孤立项 {#clear-orphans}

删除每个孤立的实体、设备、区域和集成策略。不发送正文。Phoenix 会先对照 Home Assistant 检查孤立项，然后准确删除那些策略。

响应为 `{ deleted: { entities, devices, areas, integrations }, count }`。

## 导入和导出 {#portability}

```
GET    /api/phoenix-mcp/admin/mesa/export
POST   /api/phoenix-mcp/admin/mesa/import
```

用于把所有 MESA 策略和部署默认设置复制到文件，再复制回来。

### 导出 {#export}

把所有策略返回为一个存档：

```json
{
  "mesa_export": {
    "format_version": "1.1",
    "exported_at": "...",
    "mesa_core_version": "...",
    "entities": {},
    "domains": {},
    "integrations": {},
    "areas": {},
    "devices": {},
    "deployment_defaults": {},
    "unreadable_profiles": []
  }
}
```

每个部分把策略的键映射到其文档。`deployment_defaults` 仅在存储了默认设置时出现。MESA 无法读取的已存储策略会被排除，其键列在 `unreadable_profiles` 中，后者仅在此时出现。

### 导入 {#import}

检查存档中的每条策略并保存有效的策略。Phoenix 绝不保存无效策略；它会报告该策略。之后它会重新计算孤立项和建议。

| 字段 | 类型 | 说明 |
|---|---|---|
| `archive` | object | 必填。一个导出文件，根处为 `mesa_export`，`format_version` 为 `1.0` 或 `1.1`。 |
| `on_conflict` | string | `skip`（默认）保留你已有的策略。`overwrite` 替换它们。 |

响应为 `{ imported, overwritten, skipped_existing, invalid }`：

- `imported` 和 `overwritten` 是计数。
- `skipped_existing` 列出因已存在而保留的策略。
- `invalid` 把每个被拒绝的策略映射到原因。
- 策略命名为 `<section>:<key>`，例如 `entities:light.kitchen`，或 `deployment_defaults`。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.mesaArchiveRequired` | 缺少 `archive`。 |
| 400 | `invalid_request` | `adminError.mesaConflictModeInvalid` | `on_conflict` 不是 `skip` 或 `overwrite`。 |
| 400 | `invalid_request` | 无，`message_passthrough` | 存档不是 MESA 导出文件，或其 `format_version` 不受支持。 |

## 建议 {#suggestions}

```
POST   /api/phoenix-mcp/admin/mesa/suggestions/dismiss
POST   /api/phoenix-mcp/admin/mesa/suggestions/restore
```

用于隐藏你不想要的策略建议，或恢复已忽略的建议。建议本身请从[问题、孤立项和建议](guide:#read-issues)读取。

相关：[建议](guide:window.mesaSuggestions)

### 忽略建议 {#dismiss-suggestion}

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 必填，不能为空。当前列表中某个建议的 `key`。 |

响应为 `{ dismissed, suggestions, dismissed_suggestions }`，其中 `dismissed` 是该键。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionKeyRequired` | `key` 缺失或为空。 |
| 404 | `not_found` | `adminError.suggestionNotCurrent` | 当前没有建议具有此键。 |

### 恢复建议 {#restore-suggestion}

为一个已忽略建议发送 `key`，或为全部发送 `all: true`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `key` | string | 一个已忽略建议的键。 |
| `all` | boolean | `true` 恢复每个已忽略建议。 |

响应为 `{ restored, suggestions, dismissed_suggestions }`。`restored` 是该键，或者使用 `all` 时是被清除的忽略数量。

| 状态 | `error` | `message_key` | 条件 |
|---|---|---|---|
| 400 | `invalid_request` | `adminError.suggestionRestoreTargetRequired` | `key` 和 `all: true` 都没有发送。 |
| 404 | `not_found` | `adminError.suggestionNotDismissed` | 该键未被忽略。 |
