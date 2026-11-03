---
{ "id": "window.permissions", "title": "{{label:accessKeys.permissionsTree}}", "parent": "workspace.accessKeys", "order": 4, "keywords": ["實體", "裝置", "域", "授權", "範圍"], "sectionKeywords": { "states": ["讀取", "寫入", "拒絕存取", "N R W D"], "select-by": ["分區", "分類", "整合", "房間"], "hints": ["實體提示", "備註"], "clear-all": ["重設權限"], "pass-through": ["限定範圍模式"] } }
---
決定金鑰可以存取哪些裝置和實體，以及它只能讀取還是也可以控制。新金鑰在你在權限樹中授予內容之前，無法存取任何東西。

## 設定存取權限 {#states}

每一列有四個按鈕：

- **N**：沒有自己的設定。該列沿用上一層。
- **R**：唯讀。
- **W**：讀取並控制。
- **D**：拒絕存取。它會覆蓋其上和其下的所有授權。

每次變更都會立即儲存。再次點選目前按鈕可回到 N，或在多列上拖曳以一次設定多項。

每個名稱後的文字會顯示結果：讀寫、唯讀、拒絕存取、無存取權限或未找到。

相關：[權限如何判定](guide:topic.permissionResolution)

## 權限樹 {#tree}

域位於頂端，其裝置和實體在下方。沒有裝置的實體會歸在 **{{label:perms.deviceless}}** 下，由部件組成的裝置會將它們巢狀顯示在下面。

對域或裝置的授權也會涵蓋之後新增給它的實體；**{{label:perms.dynamicBadge}}** 徽章表示這一點。「!」標記的是 W 會延伸得比看起來更遠的域，例如 homeassistant、backup、automation、script 和 scene。

標記為「{{label:perms.missingBadge}}」的列，是對不在 Home Assistant 已啟用實體清單中的實體的授權。末尾的「{{label:perms.unmatchedDevicesTitle}}」列出對已不存在裝置的授權；把它們設為 N 即可移除。

實體、裝置、分區或分類變更時，權限樹會自行更新。

## 尋找內容 {#search}

在篩選框中輸入，可依名稱或 ID 尋找域、裝置或實體。比對會忽略大小寫和重音符號，也會忽略空格、點、底線和連字號。

## {{label:selectBy.menu}} {#select-by}

把權限樹縮小到一個分區、分類或整合。它只改變你看到的內容；絕不會授予任何權限。選擇重設可再次顯示所有內容。

## 實體提示 {#hints}

提示會告訴智慧體某個實體的情況，例如它是哪盞燈。**{{label:perms.addHint}}** 會出現在設為 R、W 或 D 的實體上。

選擇提示是僅用於此金鑰還是用於所有金鑰，輸入最多 200 個字元，然後選擇儲存。儲存空文字會移除提示。金鑰自己的提示優先於適用於所有金鑰的提示。

提示只是建議，絕不授予存取權限。智慧體及其模型提供者會收到它們，因此絕不要在提示中填寫機密。把實體設回 N 會刪除此金鑰自己的提示。

## MESA 策略 {#mesa-link}

列上的 **+** 或 **MESA** 按鈕會開啟該域、裝置或實體的 MESA 策略。

相關：[MESA](guide:workspace.mesa)

## {{label:workspace.clearAllPermissions}} {#clear-all}

在你確認後，工具列上的 **{{label:workspace.clearAllPermissions}}** 會移除此金鑰的所有授權。它還會刪除此金鑰自己的提示；適用於所有金鑰的提示會保留。

## 直通金鑰 {#pass-through}

直通金鑰不使用權限樹。**{{label:accessKeys.switchToScoped}}** 會立即為它重新啟用權限樹。

相關：[直通模式會開放什麼](guide:topic.passThrough)
