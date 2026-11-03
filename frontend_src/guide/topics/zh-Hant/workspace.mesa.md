---
{ "id": "workspace.mesa", "title": "MESA 工作區", "order": 40, "keywords": ["MESA", "安全", "策略", "控制模式"] }
---
MESA 是 Phoenix 的裝置安全層。策略會告訴 MESA 對某個裝置或實體要有多謹慎：讓智慧體自由操作、先詢問你，或完全不讓它們碰。MESA 會在每個存取金鑰允許的範圍之上生效。

- **{{label:workspace.mesaProfiles}}** 列出所有策略。選擇一個即可開啟，或選擇 **{{label:mesa.addProfile}}** 建立新策略。可在 MESA 操作選單中匯入和匯出策略。
- **{{label:workspace.profileEditor}}** 顯示所選策略。修改後儲存。
- **{{label:mesa.suggestTitle}}** 列出可能需要策略的裝置和自動化。在你儲存策略之前，不會有任何變更。
- **{{label:workspace.advisory}}** 僅在 MESA 有發現要顯示時出現。

MESA 執行的嚴格程度由[設定](guide:window.settingsSafety#mesa-mode)中的 **{{label:settings.mesaModeLabel}}** 決定。在預設的 {{label:settings.mesaModeAdvisory}} 模式下，大多數規則只會警告智慧體。

相關：[MESA 如何決定](guide:topic.mesaRules)、[Home Assistant 頁面上的策略按鈕](guide:window.settingsGeneral#inject)
