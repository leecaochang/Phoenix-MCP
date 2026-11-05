---
{"id": "topic.windows", "title": "使用窗口", "parent": "workspace.my", "order": 10, "keywords": ["布局", "排列", "工作区", "窗口", "工具栏"], "sectionKeywords": {"toolbar": ["工作区按钮", "帮助菜单", "工具"], "toolbar-options": ["仅图标", "图标和文字", "单行", "停靠", "浮动工具栏", "收起"], "title-bar": ["折叠", "最大化", "关闭", "浮动", "停靠窗口"], "window-menu": ["左移", "右移", "上移", "下移", "固定", "取消固定", "使用键盘调整大小", "始终显示图标和文字"], "arranging": ["拖动", "调整大小", "边框", "滚动"], "closing": ["显示窗口", "重新打开", "隐藏"], "defaults": ["我的默认布局", "重置", "重置此工作区", "重置所有工作区", "布局"], "saved": ["同步", "其他电脑", "浏览器"], "phones": ["手机", "窄屏", "平板", "上移", "下移", "加载更多", "行"], "keyboard": ["快捷键", "上下文菜单", "转义"], "guide": ["指南", "弹出", "移回", "单独窗口", "目录", "搜索", "搜索历史", "最近搜索"], "screen-reader": ["屏幕阅读器", "无障碍", "VoiceOver", "NVDA"]}}
---
每个工作区是一组你可以随意排列的窗口。Phoenix 会为你在你登录的每台电脑上记住排列方式。

## 工具栏 {#toolbar}

按钮用于切换工作区。审批会显示有多少请求在等待。**{{label:shell.agentChat}}** 会打开 Agent Chat，**{{label:shell.help}}** 会打开或关闭快速帮助和指南。

第二行是你已打开窗口的工具，例如筛选或刷新。多个窗口共用的工具只显示一次，但会作用于所有窗口。

## {{label:workspace.toolbarOptions}} {#toolbar-options}

从工具栏的最后一个按钮打开它们，或右键点击工具栏。

- **{{label:workspace.iconsAndText}}**（默认）或 **{{label:workspace.iconsOnly}}**。
- **{{label:workspace.singleRow}}** 在放得下时把工具与工作区按钮放在同一行，放不下时恢复为两行。
- 把工具栏停靠到任意边缘，或用 **{{label:workspace.floatToolbar}}** 把它放到任意位置。你也可以拖动它的手柄使其浮动。
- **{{label:workspace.collapseToolbar}}** 把它收缩到手柄。

## 标题栏 {#title-bar}

拖动标题栏可移动窗口。标题旁的按钮可将其折叠到标题栏、浮动到工作区上方、最大化或关闭。

工作区滚动时，浮动窗口保持不动。**{{label:workspace.dockWindow}}** 会把它放回原处。从其菜单中，移动项会让它在工作区中移动，**{{label:workspace.resizeKeyboard}}** 会调整它的大小。

## 窗口菜单 {#window-menu}

从标题栏的菜单按钮打开，或右键点击标题栏。

- **{{label:workspace.moveLeft}}** 和其他移动操作会把窗口移到该侧相邻窗口之外，无需拖动。
- **{{label:workspace.resizeKeyboard}}**：左右方向键更改宽度，上下方向键更改高度。完成后按 Escape。
- **{{label:workspace.pinTop}}** 和 **{{label:workspace.pinBottom}}** 会把窗口固定在不滚动的区域。**{{label:workspace.unpin}}** 会恢复它。
- **{{label:workspace.addToMy}}** 把它复制到[我的工作区](guide:workspace.my)。
- **{{label:workspace.alwaysLabels}}** 会显示其工具的名称，即使工具栏只显示图标。

## 指南 {#guide}

指南是覆盖所有工作区的一个窗口。它显示你正在使用的窗口对应的页面，选择一个控件会带你到介绍该控件的部分。使用顶部的后退和前进、目录和搜索来查找。

再次选择同一个控件，无论指南正在显示哪里，它都会回到该控件的部分。搜索框为空时，搜索会列出在此浏览器中最近的 10 次搜索；选择一项即可再次搜索。

第一次打开时，指南会显示[欢迎使用指南](guide:topic.welcome)，这是它功能的简短演示。之后它会打开你的页面，从第一页按 **{{label:guide.back}}** 仍可到达欢迎页。**{{label:workspace.restorePanelDefaults}}** 会再次先显示它。

从标题栏折叠、最大化和关闭它，或从其菜单用键盘移动和调整大小。在任何地方关闭它都会在所有地方关闭。在设置向导旁边，它没有这些按钮并保持打开，但你仍然可以拖动和调整大小。

**{{label:agentchat.popOut}}** 会把指南移到它自己的浏览器窗口，以便放在 Home Assistant 旁边。它会继续跟随你在面板中选择的内容。**{{label:agentchat.popIn}}** 会把它移回，关闭那个窗口也会关闭指南。

它只在 Phoenix 面板打开时保持弹出。重新加载后它会回到面板内。手机上不提供此功能，它位于设置向导旁边时也不提供。

## 排列窗口 {#arranging}

把窗口放到另一个窗口的边缘，可将它放在旁边、上方或下方。最多四个窗口可以并排。拖动两个窗口之间的边框可调整它们的大小。

访问密钥和设置会滚动，每个窗口的高度与其内容一样高。其他工作区适应屏幕，因此其窗口共享屏幕高度。

## 关闭和显示窗口 {#closing}

关闭窗口会在当前工作区隐藏它。可从 **{{label:workspace.options}}** 再次显示它，它会回到原来的位置。

快速帮助可从 **{{label:shell.help}}** 菜单恢复，需要关注可从 **{{label:workspace.showAttention}}** 恢复。在我的工作区中，关闭窗口会将其移除。

## 你的排列 {#defaults}

**{{label:workspace.options}}** 也可以通过右键点击工作区的空白处打开。

- **{{label:workspace.saveMyDefault}}** 会保留此工作区的排列，包括表格列。**{{label:workspace.restoreMyDefault}}** 会恢复它。
- **{{label:workspace.restoreOriginal}}** 会把此工作区恢复为 Phoenix 最初的排列，并重置其表格列。你的“我的默认布局”会保留。
- **{{label:workspace.restorePanelDefaults}}** 会对每个工作区执行此操作，还会重置工具栏、快速帮助和指南，并清空我的工作区。所有“我的默认布局”都会保留。

## 排列如何保存 {#saved}

你的排列会在你工作时保存，归属于你的 Home Assistant 用户。另一台电脑下次加载 Phoenix 时会采用它。手机保留自己的排列。

## 在手机上 {#phones}

手机把每个工作区的窗口显示为可滚动的一列，工具栏分两行位于顶部。把手机横过来时这一列保持不变。电脑或平板上的窄浏览器窗口也显示相同的一列。

每个标题栏都有折叠窗口、打开其菜单和关闭它的按钮。手机上不能拖动。请改用窗口菜单：

- **{{label:workspace.moveUp}}** 和 **{{label:workspace.moveDown}}** 会与列中相邻窗口交换位置。
- **{{label:workspace.addToMy}}** 和 **{{label:workspace.alwaysLabels}}** 的用法与电脑上相同。
- **{{label:agentchat.maximize}}** 会让窗口填满屏幕，**{{label:agentchat.restore}}** 会恢复那一列。

**{{label:workspace.options}}**，即工具栏的最后一个按钮，会再次显示已关闭的窗口，并保存、还原或重置你的排列。

长列表一次显示 10 行。**{{label:common.loadMore}}** 会显示接下来的 10 行。

指南在列的底部打开。快速帮助和通知历史在手机上不可用。

## 键盘 {#keyboard}

F6 会移动到下一个区域：工具栏、按你看到的顺序排列的每个窗口、指南和 Agent Chat。Shift+F6 会反向移动。回到某个区域会回到你上次在该区域的位置，Tab 仍在其中的控件之间移动。

每个菜单都可以用 Enter、空格或下方向键打开。使用方向键、Home 和 End 在其中移动，按 Escape 关闭。键盘的菜单键会打开焦点所在控件的菜单。

当你所在的控件消失时，例如关闭窗口或决定请求后，焦点会移到该窗口或取代它的窗口。Delete 和 Backspace 不会关闭窗口。

## 使用屏幕阅读器 {#screen-reader}

面板可以配合屏幕阅读器使用。窗口、字段和按钮都有你所用语言的名称，只显示图标的按钮也不例外。

Phoenix 会播报焦点之外发生的变化。停止输入后，它会说出筛选或搜索后剩下多少行。错误会立即读出，并一直保留到你关闭。在选择多项时，每勾选一项都会读出已勾选的请求数量。

在其他位置出现的结果（例如批量审批完成或导入完成）也会读出。按 F6 进入快速帮助时，会保留你刚离开的控件的文本。

行可选的表格只占一个 Tab 位置。之后用方向键、Home 和 End 在行之间移动。在权限中，方向键只在级别之间移动；按 Enter 或空格才会设置，所以经过时不会改变任何内容。
