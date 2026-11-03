---
{ "id": "window.permissionSummary", "title": "{{label:accessKeys.permissionSummary}}", "parent": "workspace.accessKeys", "order": 5, "keywords": ["概览", "规则", "授权"] }
---
列出此密钥[{{label:accessKeys.permissionsTree}}](guide:window.permissions)中设置的每条规则：每个设为 R、W 或 D 的域、设备或实体。保留为 N 的行不会列出，它们继承的内容也不会列出。

## 表格 {#summary-table}

显示每条规则的类型、名称、ID 和状态。当设备或域上的 D 覆盖该规则时，状态显示为拒绝。使用列标题的菜单来排序、移动或隐藏列，拖动标题边缘可调整列宽。

选择一行可在{{label:accessKeys.permissionsTree}}中显示它。实体行上的 MESA 按钮会打开其 MESA 策略。

## 空摘要 {#empty}

空摘要表示权限树中没有授予任何内容。不使用权限树的工具，例如读取配置或管理自动化，只要密钥的能力允许，仍然可以工作。

在直通密钥上，摘要会说明该密钥跳过权限树。
