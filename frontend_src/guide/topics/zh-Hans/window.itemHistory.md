---
{ "id": "window.itemHistory", "title": "{{label:workspace.itemHistory}}", "parent": "workspace.changes", "order": 3, "keywords": ["版本", "时间线", "较早"] }
---
所选项目的每条已记录更改，最新的在前，并标记所选的那条。选择一个更改进行比较，**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 随后会在此列表中移动。

当项目有不止一个更改时，它会自行在变更列表下方打开。如果你关闭了它，它会保持关闭；可从 **{{label:workspace.options}}** 重新打开。

Phoenix 会保留每个项目最新的 20 次更改。实体重命名后，其更改会移到新的 ID，因此历史仍保留在一个列表中。
