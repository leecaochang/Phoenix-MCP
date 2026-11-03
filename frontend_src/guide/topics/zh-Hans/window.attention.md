---
{ "id": "window.attention", "title": "{{label:attention.title}}", "parent": "topic.windows", "order": 22, "keywords": ["未知结果", "已中断", "被阻止的写入", "审核"], "sectionKeywords": { "actions": ["检查之前的操作", "未知", "日历"], "review": ["标记为已审核", "在审计日志中显示", "被阻止", "写入被拒绝"], "problems": ["MESA 无法启动", "日历历史"], "window": ["显示需要关注", "关闭"] } }
---
Phoenix 自己无法解决的事情。有内容需要检查时，窗口会出现在工作区上方；列表为空时它会消失。它每 15 秒检查一次新项目。

## 结果未知的操作 {#actions}

操作已到达 Home Assistant，但 Phoenix 始终不知道它是否生效。这会在对话被切断或 Home Assistant 在操作中途重启时发生。项目会注明访问密钥、工具以及开始时间。

在你审核它之前，该访问密钥无法再进行任何更改。结果未知的日历更改也会以同样方式列出。

## 审核操作 {#review}

在 Home Assistant 中检查操作是否发生。对于智能体的操作，**{{label:attention.showInAudit}}** 会在该请求处打开审计日志。

然后选择 **{{label:attention.continue}}**，勾选 **{{label:attention.checked}}** 并确认。Phoenix 不会重复或撤销该操作。密钥可以再次进行更改，审计日志会记录你的审核。

## 需要修复的问题 {#problems}

- **{{label:attention.mesaTitle}}**：MESA 启动失败，因此受保护的更改被暂停。请检查 Home Assistant 日志，然后重新加载 Phoenix。
- **{{label:attention.calendar_storageTitle}}**：日历更改会暂停，直到 Phoenix 能再次读取其保存的日历历史。

问题修复后，这些会自行消失。

## 关闭窗口 {#window}

关闭窗口会在当前工作区隐藏它，直到出现它未显示过的项目。工作区选项中的 **{{label:workspace.showAttention}}** 会把它带回来，重置当前工作区或所有工作区也会如此。在手机上，请从标题栏关闭它。
