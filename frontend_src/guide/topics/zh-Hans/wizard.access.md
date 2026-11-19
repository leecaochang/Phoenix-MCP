---
{ "id": "wizard.access", "title": "为一盏灯授予访问权限", "parent": "workspace.accessKeys", "order": 103 }
---
新密钥在你授予它内容之前无法访问任何设备。请授予一盏你能看到的灯，好让测试能证明一切正常。

**light** 分组列出你的灯设备，以及不属于任何设备的灯。展开它，再展开包含你那盏灯的设备，或用筛选框找到这盏灯。

每行有四个按钮：N 沿用上一级，R 读取，W 读取并控制，D 拒绝访问。在一盏灯上选择 **W**。在把某项设为 W 之前，**{{label:wizard.next}}** 会一直灰显。测试只设 R 不够。

相关：[设置访问权限](guide:window.permissions#states)

对设备的授权会覆盖其下的实体。对整个灯域的授权会覆盖每一盏灯，包括之后添加的灯。

如果 Home Assistant 中没有灯，这一步会说明情况，并在消息旁显示 **{{label:wizard.showAllDevices}}**。它会列出所有域，你可以改为授予其他设备。之后还可以在{{label:accessKeys.permissionsTree}}中授予更多权限。
