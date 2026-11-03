---
{ "id": "wizard.access", "title": "为一盏灯授予访问权限", "parent": "workspace.accessKeys", "order": 103 }
---
新密钥在你授予它内容之前无法访问任何设备。请授予一盏你能看到的灯，好让测试能证明一切正常。

选择 **light** 旁的三角形以展开灯分组。它会列出你的灯设备，以及不属于任何设备的灯。选择设备旁的三角形可查看其灯实体。不想逐层浏览，可以在列表上方的筛选框中输入灯的名称。

每行有四个按钮：N 沿用上一级，R 读取，W 读取并控制，D 拒绝访问（[更多说明](guide:window.permissions#states)）。在一盏灯上选择 **W**。在把某项设为 W 之前，**{{label:wizard.next}}** 会一直灰显；测试只设 R 不够。

对设备的授权会覆盖其下的实体。对整个灯域的授权会覆盖每一盏灯，包括之后添加的灯。

如果 Home Assistant 中没有灯，这一步会说明情况，并在消息旁显示 **{{label:wizard.showAllDevices}}**。它会列出所有域，你可以改为授予其他设备。之后还可以在{{label:accessKeys.permissionsTree}}中授予更多权限。
