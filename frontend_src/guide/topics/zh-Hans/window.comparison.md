---
{ "id": "window.comparison", "title": "{{label:workspace.comparison}}", "parent": "workspace.changes", "order": 2, "keywords": ["差异", "更改前", "更改后"], "sectionKeywords": { "restore": ["撤销", "回滚", "还原"], "views": ["代码编辑器", "并排", "堆叠", "预览"] } }
---
所选更改，即该项目在此更改前后的样子。标题下方的行说明更改是什么、类型、发起者和时间，随后是简短摘要。

## 在更改间切换 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 会在你选择更改的列表中移动：变更列表或项目历史。

## 视图 {#views}

前后内容会逐行比较，删除的行显示为红色，添加的行显示为绿色。在 **{{label:changes.lineViewShort}}** 和 **{{label:changes.codeEditorShort}}** 之间切换，也在 **{{label:changes.stackedShort}}** 和 **{{label:changes.sideBySideShort}}** 之间切换。

仪表盘更改还可以显示为 **{{label:common.preview}}**，绘制更改前或更改后的仪表盘。这些选择会保存在此浏览器中，并与审批共享。

## {{label:changes.restore}} {#restore}

把项目恢复到此次更改之前（**{{label:changes.restoreBefore}}**）或之后（**{{label:changes.restoreAfter}}**）的状态。还原以你的管理员权限运行，因此无需审批。

灰显的选项在这里无法使用，原因会显示在其下方。例如，不存在的一侧、项目最新更改的更改后、已删除的实体，或包含 !secret 等 YAML 标签的版本。即使确认后，如果还原不再适应当前项目，仍可能被拒绝。

相关：[还原可以放回什么](guide:topic.configurationHistory#restore)

### 确认 {#confirm}

选择一侧会要求你就地确认。选择 **{{label:changes.confirmRestoreButton}}**，或选择 **{{label:changes.cancelRestore}}** 退出。

如果你确认时项目已有更新的更改，Phoenix 不会还原任何内容并会告知你，因此请先检查更新的更改。服务器也会检查这一点，因此最后一刻到达的更改也会被捕获。

### 还原之后 {#after-restore}

还原会记录为一次新更改，即回滚，并成为所选更改。要撤销它，请还原该回滚的更改前。
