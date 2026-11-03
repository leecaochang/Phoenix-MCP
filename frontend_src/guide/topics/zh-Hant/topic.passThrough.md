---
{ "id": "topic.passThrough", "title": "直通模式會開放什麼", "parent": "window.accessKeys", "order": 2, "keywords": ["直通模式", "不受限制", "完整存取", "限定範圍"] }
---
直通金鑰會略過權限樹：它可以讀取和控制所有實體，Phoenix 自己的實體除外。請僅對可以放心託付整個家的智慧體使用。

## 會開啟什麼 {#allows}

除非你把這些能力設為確認，直通模式還會允許以下能力：

- {{label:caps.cap_config_read.label}}、{{label:caps.cap_template_render.label}}、{{label:caps.cap_search.label}} 和 {{label:caps.cap_registry_read.label}}。
- {{label:caps.cap_traces.label}}、{{label:caps.cap_diagnostics.label}}、{{label:caps.cap_broadcast.label}} 和 {{label:caps.cap_service_response.label}}。

{{label:caps.cap_diagnostics.label}} 和 {{label:caps.cap_service_response.label}} 可能暴露系統的敏感詳細資料。

## 什麼仍由你控制 {#keeps}

其他所有能力都保留各自的設定。包括物理控制、重新啟動、通知、攝影機影像、系統日誌、檔案、ESPHome YAML 以及所有設定變更。確認仍是確認。

門鎖、警報控制面板、捲簾和閥門仍然需要 {{label:caps.cap_physical_control.label}}。敏感屬性，例如攝影機串流位址，仍會被移除。

## 縮小範圍 {#limit}

[{{label:workspace.keySettings}}](guide:window.keySettings#assist-exposure)中的 **{{label:accessKeys.assistExposureLabel}}** 會把金鑰限制為向 Assist 公開的實體。Home Companion 金鑰完全不能使用直通模式。

## 關閉它 {#off}

在[{{label:accessKeys.permissionsTree}}](guide:window.permissions#pass-through)視窗中選擇 **{{label:accessKeys.switchToScoped}}**。金鑰會再次使用其儲存的權限樹。
