---
{ "id": "topic.permissionResolution", "title": "权限如何判定", "parent": "window.permissions", "order": 1, "keywords": ["优先级", "继承", "解析", "为何被拒绝访问"], "sectionKeywords": { "examples": ["示例", "完整示例", "做法"], "indirect": ["间接控制", "自动化", "脚本", "场景", "触发", "!"], "always-blocked": ["阻止", "从不", "phoenix_mcp", "内部传感器"] } }
---
无论请求来自哪个应用或功能，Phoenix 都以相同方式判定每个请求。

## 顺序 {#order}

1. 不存在的实体为未找到。
2. Phoenix 自己的实体永远无法访问。
3. Home Companion 密钥只能访问其家用设备。
4. 直通密钥可以访问其他所有内容，或者如果你限制了它，只能访问向 Assist 公开的实体。
5. 否则 Phoenix 读取权限树：实体、其设备、它所属的主设备（如有），然后是它的域。

## 拒绝访问优先 {#deny}

该链中任何位置的 D 都会拒绝访问，即使实体本身设为 W。

## 最近的授权决定结果 {#nearest}

否则由最近的 R 或 W 决定。实体自身的设置优先于其设备，设备的设置优先于其域。如果每一层都是 N，结果为无访问权限。

## 示例 {#examples}

### 读取所有灯，控制一个房间 {#example-one-room}

把灯域设为 R，把客厅的灯设为 W。每盏灯都是只读，客厅的灯是读写，因为它自身的设置最近。

### 控制所有灯，除了一间卧室 {#example-except}

把灯域设为 W，把客卧的设备设为 D。其他每盏灯都是读写，客卧的灯是拒绝访问。

### 在你允许的设备上屏蔽一个传感器 {#example-one-sensor}

把媒体播放器的设备设为 W，把它的信号强度传感器设为 D。智能体可以控制播放器，但看不到该传感器。

## Assist 和语音智能体 {#household}

有人对 Assist 或语音智能体说话时，Phoenix 还会把结果限制为该 Home Assistant 用户可以看到和控制的内容。

相关：[家用扬声器可用的能力](guide:topic.tools#household)

## 已禁用的实体 {#registry-only}

已禁用或没有当前状态的实体，只能通过对其实体设备或域的授权来访问。

## 权限树显示什么 {#tree-label}

树中每个名称后的结果与权限树一致。在 Home Companion 密钥上，它还会显示 Home Companion 的限制：它无法访问的设备显示为无访问权限，传感器上的写入显示为读取。它不会考虑说话者。

## 通过自动化、脚本和场景控制 {#indirect}

自动化、脚本或场景以 Home Assistant 身份运行，而不是智能体。因此，对其中一个的 W 可以让智能体触发其自身密钥无法访问的操作。对门锁为 D 的密钥，仍可以通过运行会开锁的自动化来开锁。

R 用于触发是安全的：智能体可以看到这些实体，但无法运行它们。权限树会用“!”标记这些域。

要保护触发器，请为自动化、脚本或场景设置一个设为确认的 MESA 策略。之后每次运行都会等待你的审批，无论其操作做什么。[{{label:mesa.suggestTitle}}](guide:window.mesaSuggestions)会找出会触及风险设备的那些。

审批只覆盖请求时指定的确切实体及其任何组成员。如果组发生变化，智能体必须重新询问。Phoenix 不会检查场景或脚本随后做了什么。

相关：[自动化和脚本](guide:topic.tools#automations)

## 始终阻止 {#always-blocked}

无论权限树说什么，包括直通模式，任何密钥都无法访问 Phoenix 自己的实体，例如它的访问密钥传感器。属性中的机密也会被移除。

相关：[智能体永远看不到什么](guide:topic.security#hidden)
