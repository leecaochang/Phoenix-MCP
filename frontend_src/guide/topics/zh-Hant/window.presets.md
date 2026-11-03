---
{ "id": "window.presets", "title": "{{label:accessKeys.cardPresets}}", "parent": "workspace.accessKeys", "order": 7, "keywords": ["快照", "設定組合", "切換設定"] }
---
預設是此金鑰已儲存的一組設定。在預設之間切換可快速變更金鑰，例如一個謹慎的日常預設和一個範圍較廣的維護預設。當[{{label:workspace.general}}](guide:window.settingsGeneral#presets)中的 **{{label:settings.presetsLabel}}** 開啟時，此視窗會出現。

預設包含金鑰的角色、能力、權限樹、直通模式、Assist 公開範圍、工具宣告、核准等待和速率限制。它不包含金鑰的名稱或指引。

每個金鑰最多可以有 8 個預設。正在使用的預設標記為 **{{label:accessKeys.presetActive}}**。**{{label:accessKeys.presetModified}}** 表示金鑰自該預設上次儲存以來已變更。

## {{label:accessKeys.presetSwitchTo}} {#switch-to}

把金鑰的目前設定儲存到目前的預設，然後套用你選擇的預設。會先顯示變更內容清單，供你確認。如果預設會開啟直通模式，也要確認這一點。

切換會告知已連線的智慧體其工具已變更，並重新開始金鑰的速率限制計數。

## {{label:accessKeys.presetRevertToSaved}} {#revert}

在你確認後，捨棄自目前預設儲存以來所做的變更。

## {{label:accessKeys.presetSaveCurrent}} {#save-current}

把金鑰的目前設定儲存為新預設，並使其成為目前預設。先輸入最多 40 個字元的名稱。你原來所在的預設仍會保留上次儲存的狀態。

## 重新命名和刪除 {#rename-delete}

就地編輯預設名稱；按 Enter 儲存或按 Esc 取消。**{{label:accessKeys.delete}}** 會在你確認後刪除預設。你不能刪除目前預設。
