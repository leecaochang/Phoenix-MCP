---
{ "id": "topic.permissionResolution", "title": "權限如何判定", "parent": "window.permissions", "order": 1, "keywords": ["優先順序", "繼承", "解析", "為何被拒絕存取"], "sectionKeywords": { "examples": ["範例", "完整範例", "做法"], "indirect": ["間接控制", "自動化", "腳本", "場景", "觸發", "!"], "always-blocked": ["阻止", "從不", "phoenix_mcp", "內部感測器"] } }
---
無論請求來自哪個應用程式或功能，Phoenix 都以相同方式判定每個請求。

## 順序 {#order}

1. 不存在的實體為未找到。
2. Phoenix 自己的實體永遠無法存取。
3. Home Companion 金鑰只能存取其家用裝置。
4. 直通金鑰可以存取其他所有內容，或者如果你限制了它，只能存取向 Assist 公開的實體。
5. 否則 Phoenix 會讀取權限樹：實體、其裝置、它所屬的主裝置（如有），然後是它的域。

## 拒絕存取優先 {#deny}

該鏈中任何位置的 D 都會拒絕存取，即使實體本身設為 W。

含有 D 成員的群組也會被拒絕，因為對群組的操作會觸及該成員。在範圍更大的請求中（例如整個分區），Phoenix 會略過該群組，只對其餘實體執行操作。場景、腳本和自動化不算作群組。

## 最近的授權決定結果 {#nearest}

否則由最近的 R 或 W 決定。實體自身的設定優先於其裝置，裝置的設定優先於其域。如果每一層都是 N，結果為無存取權限。

## 範例 {#examples}

### 讀取所有燈，控制一個房間 {#example-one-room}

把燈域設為 R，把客廳的燈設為 W。每盞燈都是唯讀，客廳的燈是讀寫，因為它自身的設定最近。

### 控制所有燈，除了一間臥室 {#example-except}

把燈域設為 W，把客臥的裝置設為 D。其他每盞燈都是讀寫，客臥的燈是拒絕存取。

### 在你允許的裝置上封鎖一個感測器 {#example-one-sensor}

把媒體播放器的裝置設為 W，把它的訊號強度感測器設為 D。智慧體可以控制播放器，但看不到該感測器。

## Assist 和語音智慧體 {#household}

有人對 Assist 或語音智慧體說話時，Phoenix 還會把結果限制為該 Home Assistant 使用者可以看到和控制的內容。

相關：[家用揚聲器可用的能力](guide:topic.tools#household)

## 已停用的實體 {#registry-only}

已停用或沒有目前狀態的實體，只能透過對其裝置或域的授權來存取。

## 權限樹顯示什麼 {#tree-label}

樹中每個名稱後的結果與權限樹一致。在 Home Companion 金鑰上，它還會顯示 Home Companion 的限制：它無法存取的裝置顯示為無存取權限，感測器上的寫入顯示為讀取。它不會考慮說話者。

## 透過自動化、腳本和場景控制 {#indirect}

自動化、腳本或場景以 Home Assistant 身分執行，而不是智慧體。因此，對其中一個的 W 可以讓智慧體觸發其自身金鑰無法存取的操作。對門鎖為 D 的金鑰，仍可以透過執行會開鎖的自動化來開鎖。

R 用於觸發是安全的：智慧體可以看到這些實體，但無法執行它們。權限樹會用「!」標記這些域。

若要保護觸發器，請為自動化、腳本或場景設定一個設為確認的 MESA 策略。當{{label:settings.mesaModeLabel}}或該策略為{{label:settings.mesaModeEnforced}}時，每次執行都會等待你的核准，無論其操作做什麼。在{{label:settings.mesaModeAdvisory}}下，執行會直接進行。[{{label:mesa.suggestTitle}}](guide:window.mesaSuggestions)會找出會觸及風險裝置的那些。

核准只涵蓋請求時指定的確切實體及其任何群組成員。如果群組變更，智慧體必須重新詢問。Phoenix 不會檢查場景或腳本隨後做了什麼。

相關：[自動化和腳本](guide:topic.tools#automations)

## 始終阻止 {#always-blocked}

無論權限樹說什麼，包括直通模式，任何金鑰都無法存取 Phoenix 自己的實體，例如它的存取金鑰感測器。屬性中的機密也會被移除。

相關：[智慧體永遠看不到什麼](guide:topic.security#hidden)
