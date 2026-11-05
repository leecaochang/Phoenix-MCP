---
{ "id": "window.changes", "title": "{{label:shell.tabChanges}}", "parent": "workspace.changes", "order": 1, "keywords": ["历史", "列表", "版本"], "sectionKeywords": { "filters": ["类型", "操作", "搜索"] } }
---
Phoenix 记录的每次更改，最新的在前。选择一个更改，即可在[{{label:workspace.comparison}}](guide:window.comparison)中比较它。智能体进行更改时，列表会自行更新。

## {{label:logs.filters}} {#filters}

工具栏上的 **{{label:logs.filters}}** 会显示筛选条件：类型、操作（创建、编辑、删除或回滚）以及搜索框。筛选条件会查看所有已记录的更改，而不只是屏幕上的行；搜索按项目、类型、操作、发起者和摘要匹配。

更改筛选条件或搜索内容会清除所选的更改。关闭筛选会停止应用它们；再次打开时会恢复原样。**{{label:logs.clearFilters}}** 会重置这三项。

## 列表 {#list}

每行显示更改的时间、项目、操作、类型以及发起者：访问密钥，或 **{{label:changes.byAdmin}}** 表示从此面板进行的还原。

从列标题的菜单排序、移动和隐藏列，拖动标题边缘可调整列宽。列表下方的计数包含尚未加载的更改。

## {{label:common.loadMore}} {#load-more}

加载接下来的 100 条更改。
