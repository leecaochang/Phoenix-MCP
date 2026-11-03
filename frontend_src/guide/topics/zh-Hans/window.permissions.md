---
{ "id": "window.permissions", "title": "{{label:accessKeys.permissionsTree}}", "parent": "workspace.accessKeys", "order": 4, "keywords": ["实体", "设备", "域", "授权", "范围"], "sectionKeywords": { "states": ["读取", "写入", "拒绝访问", "N R W D"], "select-by": ["区域", "标签", "集成", "房间"], "hints": ["实体提示", "备注"], "clear-all": ["重置权限"], "pass-through": ["限定范围模式"] } }
---
决定密钥可以访问哪些设备和实体，以及它只能读取还是也可以控制。新密钥在你在权限树中授予内容之前无法访问任何东西。

## 设置访问权限 {#states}

每行有四个按钮：

- **N**：没有自己的设置。该行沿用上一级。
- **R**：只读。
- **W**：读取并控制。
- **D**：拒绝访问。它会覆盖其上和其下的所有授权。

每次更改都会立即保存。再次点击当前按钮可回到 N，或在多行上拖动以一次设置多项。

使用键盘时，每行的四个按钮只占一个 Tab 位置。用方向键在 N、R、W 和 D 之间移动，然后按 Enter 或空格设置当前所在的按钮。仅移动不会更改任何内容。

每个名称后的文字显示结果：读写、只读、拒绝访问、无访问权限或未找到。

相关：[权限如何判定](guide:topic.permissionResolution)

## 权限树 {#tree}

域位于顶部，其设备和实体在下方。没有设备的实体归在 **{{label:perms.deviceless}}** 下，由部件组成的设备会把它们嵌套显示在下面。

对域或设备的授权也会覆盖之后添加给它的实体；**{{label:perms.dynamicBadge}}** 徽标表示这一点。“!”标记的是 W 会延伸得比看起来更远的域，例如 homeassistant、backup、automation、script 和 scene。

标记为“{{label:perms.missingBadge}}”的行，是对不在 Home Assistant 已启用实体列表中的实体的授权。末尾的“{{label:perms.unmatchedDevicesTitle}}”列出对已不存在设备的授权；把它们设为 N 即可移除。

实体、设备、区域或标签发生变化时，权限树会自行更新。

## 查找内容 {#search}

在筛选框中输入，可按名称或 ID 查找域、设备或实体。匹配会忽略大小写和重音符号，也会忽略空格、点、下划线和连字符。

## {{label:selectBy.menu}} {#select-by}

把权限树缩小到一个区域、标签或集成。它只改变你看到的内容；绝不会授予任何权限。选择重置可再次显示所有内容。

## 实体提示 {#hints}

提示会告诉智能体某个实体的情况，例如它是哪盏灯。**{{label:perms.addHint}}** 会出现在设为 R、W 或 D 的实体上。

选择提示是仅用于此密钥还是用于所有密钥，输入最多 200 个字符，然后选择**保存**。保存空文本会移除提示。密钥自己的提示优先于适用于所有密钥的提示。

提示只是建议，绝不授予访问权限。智能体及其模型提供商会收到它们，因此绝不要在提示中填写机密。把实体设回 N 会删除此密钥自己的提示。

## MESA 策略 {#mesa-link}

行上的 **+** 或 **MESA** 按钮会打开该域、设备或实体的 MESA 策略。

相关：[MESA](guide:workspace.mesa)

## {{label:workspace.clearAllPermissions}} {#clear-all}

在你确认后，工具栏上的 **{{label:workspace.clearAllPermissions}}** 会移除此密钥的所有授权。它还会删除此密钥自己的提示；适用于所有密钥的提示会保留。

## 直通密钥 {#pass-through}

直通密钥不使用权限树。**{{label:accessKeys.switchToScoped}}** 会立即为它重新启用权限树。

相关：[直通模式会开放什么](guide:topic.passThrough)
