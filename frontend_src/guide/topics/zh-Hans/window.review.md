---
{ "id": "window.review", "title": "{{label:workspace.review}}", "parent": "workspace.approvals", "order": 2, "keywords": ["差异", "比较", "决定"], "sectionKeywords": { "reject": ["原因", "拒绝请求"], "change": ["更改前", "更改后", "预览", "代码编辑器", "并排"] } }
---
准确显示所选请求会更改什么，并让你做出决定。已决定的请求会保留在这里，并显示其结果。

## 剩余时间 {#context}

标题下方的行显示请求的状态、它的访问密钥，以及待处理请求过期前的剩余时间。已过期的请求无法批准，因此只提供 **{{label:approvals.reject}}**，它会把请求以已过期状态关闭。

## {{label:approvals.approve}} {#approve}

立即运行请求，不再询问。Phoenix 会先检查它是否仍被允许：密钥必须仍然有效，其能力不能是拒绝访问，紧急停止开关必须关闭。否则请求会在不运行的情况下关闭。

如果操作失败，请求会以失败结束，并且不会再提供。如果你仍然想要该更改，请让智能体重试。

相关：[你批准后会发生什么](guide:topic.approvalLifecycle#approve)

## {{label:approvals.reject}} {#reject}

拒绝请求。**{{label:approvals.reject}}** 会打开一个用于填写可选原因的框，智能体会收到该原因，以便调整下一次提议。按 Enter 或选择 **{{label:approvals.confirmReject}}** 拒绝，或选择 **{{label:approvals.cancelReject}}** 退出。

你输入的原因也会同步到 Agent Chat 的拒绝按钮。

## 更改 {#change}

更改的显示方式取决于请求做什么：

- 配置和 YAML：**{{label:common.before}}** 和 **{{label:common.after}}** 并排显示。在 **{{label:changes.lineViewShort}}** 和 **{{label:changes.codeEditorShort}}** 之间切换，或把两侧上下堆叠。
- 仪表盘、卡片和能源仪表盘：**{{label:common.diff}}** 或 **{{label:common.preview}}**，后者会绘制结果。
- 对设备的操作：操作、其目标，以及 MESA 允许、暂缓或阻止的内容。
- 实体和设备注册表的更改：先说明此更改为何需要你的批准，再显示更改内容以及使用该目标的项目。
- 删除待办事项：每个事项及其状态，并提示删除后无法恢复。

这些视图选择会保存在此浏览器中，变更也会使用它们。

## 在请求间切换 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 可在不离开审核的情况下在列表中移动。
