---
{ "id": "window.approvals", "title": "{{label:shell.tabApprovals}}", "parent": "workspace.approvals", "order": 1, "keywords": ["请求", "队列", "待处理"], "sectionKeywords": { "select-multiple": ["批处理", "全部批准", "批量"], "show": ["历史", "筛选", "已批准", "已拒绝", "已过期"] } }
---
智能体发出的请求。选择一个请求，即可在[{{label:workspace.review}}](guide:window.review)中查看它。列表会自行更新，无需刷新。

## {{label:approvals.show}} {#show}

要列出哪些请求：**{{label:approvals.tabPending}}**（默认）、**{{label:approvals.tabHistory}}**（所有已决定的请求）、**{{label:approvals.scopeAll}}**，或单一结果。你的选择会保存在此浏览器中。

如果你在查看历史时有新请求到达，再次打开工作区会切回待处理。

## 搜索 {#search}

在已加载的请求中，按访问密钥、工具、标题或原因查找请求。

## 列表 {#list}

每行显示请求及其状态。更多列，例如访问密钥、工具和时间，默认隐藏；可从列标题的菜单中显示。最新的请求在最前。

使用方向键在列表中移动；审核会跟随。正在被某人批准的请求显示为 **{{label:approvals.processing}}**。

## {{label:approvals.selectMultiple}} {#select-multiple}

一次批准多个待处理请求。在工具栏上选择 **{{label:approvals.selectMultiple}}**，勾选请求，然后选择显示已选数量的批准按钮。列出至少两个待处理请求时可用。

Phoenix 会从最新开始逐个批准，并在第一个未成功的请求处停止。其余请求保持待处理并保持勾选。一批最多包含 50 个请求，因此全选只会勾选前 50 个。已过期的请求无法勾选。**{{label:approvals.cancelSelection}}** 会退出而不批准。

仅对你已经非常了解的请求使用它；每个请求仍会完全按照审核显示的内容运行。

## {{label:common.loadMore}} {#load-more}

加载接下来的 50 个请求。
