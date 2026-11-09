---
{"id": "topic.windows", "title": "使用視窗", "parent": "workspace.my", "order": 10, "keywords": ["佈局", "排列", "工作區", "視窗", "工具列"], "sectionKeywords": {"toolbar": ["工作區按鈕", "說明選單", "工具"], "toolbar-options": ["僅圖示", "圖示和文字", "單行", "停駐", "浮動工具列", "收起"], "title-bar": ["摺疊", "最大化", "關閉", "浮動", "停駐視窗"], "window-menu": ["向左移動", "向右移動", "向上移動", "向下移動", "固定", "取消固定", "使用鍵盤調整大小", "一律顯示圖示和文字"], "arranging": ["拖曳", "調整大小", "邊框", "捲動"], "closing": ["顯示視窗", "重新開啟", "隱藏"], "defaults": ["我的預設佈局", "重設", "重設此工作區", "重設所有工作區", "佈局"], "saved": ["同步", "其他電腦", "瀏覽器"], "phones": ["手機", "窄版", "平板", "向上移動", "向下移動", "載入更多", "列"], "keyboard": ["快速鍵", "內容選單", "轉義"], "guide": ["指南", "彈出", "移回", "個別視窗", "目錄", "搜尋", "搜尋記錄", "最近搜尋"], "screen-reader": ["螢幕閱讀器", "無障礙", "VoiceOver", "NVDA"]}}
---
每個工作區是一組你可以隨意排列的視窗。Phoenix 會在你登入的每台電腦上記住排列方式。

## 工具列 {#toolbar}

按鈕用於切換工作區。核准會顯示有多少請求正在等待。**{{label:shell.agentChat}}** 會開啟 Agent Chat，**{{label:shell.help}}** 會開啟或關閉快速說明和指南。

第二列是你已開啟視窗的工具，例如篩選或重新整理。多個視窗共用的工具只會顯示一次，但會作用於所有視窗。

## {{label:workspace.toolbarOptions}} {#toolbar-options}

從工具列的最後一個按鈕開啟它們，或在工具列上按右鍵。

- **{{label:workspace.iconsAndText}}**（預設）或 **{{label:workspace.iconsOnly}}**。
- **{{label:workspace.singleRow}}** 在放得下時把工具與工作區按鈕放在同一列，放不下時恢復為兩列。
- **{{label:workspace.singleColumn}}** 在工具列停駐在側邊或直立浮動時起同樣作用：用一欄代替兩欄。它與 {{label:workspace.singleRow}} 是同一個選項，所以工具列轉向後仍然保留。
- 把工具列停駐到任意邊緣，或用 **{{label:workspace.floatToolbar}}** 將它放到任意位置。你也可以拖曳它的握把使其浮動。
- **{{label:workspace.collapseToolbar}}** 將浮動的工具列收起到握把。停駐的工具列不會收起。收起後，選單只提供 **{{label:workspace.expandToolbar}}**。

## 標題列 {#title-bar}

拖曳標題列可移動視窗。標題旁的按鈕可將它摺疊到標題列、浮動到工作區上方、最大化或關閉。

工作區捲動時，浮動視窗會保持不動。**{{label:workspace.dockWindow}}** 會將它放回原處。從其選單中，移動項目會讓它在工作區中移動，**{{label:workspace.resizeKeyboard}}** 會調整它的大小。

## 視窗選單 {#window-menu}

從標題列的選單按鈕開啟，或在標題列上按右鍵。

- **{{label:workspace.moveLeft}}** 和其他移動操作會把視窗移到該側相鄰視窗之外，無需拖曳。
- **{{label:workspace.resizeKeyboard}}**：左右方向鍵變更寬度，上下方向鍵變更高度。完成後按 Escape。
- **{{label:workspace.pinTop}}** 和 **{{label:workspace.pinBottom}}** 會把視窗固定在不會捲動的區域。**{{label:workspace.unpin}}** 會恢復它。
- **{{label:workspace.addToMy}}** 將它複製到[我的工作區](guide:workspace.my)。
- **{{label:workspace.alwaysLabels}}** 會顯示其工具的名稱，即使工具列只顯示圖示。

## 指南 {#guide}

指南是涵蓋所有工作區的一個視窗。它會顯示你正在使用的視窗所對應的頁面，選擇一個控制項會帶你到介紹該控制項的章節。使用頂端的返回和前進、目錄和搜尋來尋找。

再次選擇同一個控制項，無論指南正在顯示哪裡，它都會回到該控制項的章節。搜尋框為空白時，搜尋會列出在此瀏覽器中最近的 10 次搜尋；選擇一項即可再次搜尋。

第一次開啟時，指南會顯示[歡迎使用指南](guide:topic.welcome)，這是它功能的簡短示範。之後它會開啟你的頁面，從第一頁按 **{{label:guide.back}}** 仍可到達歡迎頁。**{{label:workspace.restorePanelDefaults}}** 會再次先顯示它。

從標題列摺疊、最大化和關閉它，或從其選單用鍵盤移動和調整大小。在任何地方關閉它都會在所有地方關閉。在設定精靈旁邊，它沒有這些按鈕並保持開啟，但你仍然可以拖曳和調整大小。

**{{label:agentchat.popOut}}** 會把指南移到它自己的瀏覽器視窗，以便放在 Home Assistant 旁邊。它會繼續跟隨你在面板中選擇的內容。**{{label:agentchat.popIn}}** 會把它移回，關閉那個視窗也會關閉指南。

它只在 Phoenix 面板開啟時保持彈出。重新載入後它會回到面板內。手機上不提供此功能，它位於設定精靈旁邊時也不提供。

## 排列視窗 {#arranging}

把視窗放到另一個視窗的邊緣，可將它放在旁邊、上方或下方。最多四個視窗可以並排。拖曳兩個視窗之間的邊框可調整它們的大小。

存取金鑰和設定會捲動，每個視窗的高度與其內容一樣高。其他工作區會配合螢幕，因此其視窗共用螢幕高度。

## 關閉和顯示視窗 {#closing}

關閉視窗會在目前工作區隱藏它。可從 **{{label:workspace.options}}** 再次顯示它，它會回到原來的位置。

快速說明可從 **{{label:shell.help}}** 選單恢復，需要關注可從 **{{label:workspace.showAttention}}** 恢復。在我的工作區中，關閉視窗會將它移除。

## 你的排列 {#defaults}

**{{label:workspace.options}}** 也可以在空白處按右鍵開啟。

- **{{label:workspace.saveMyDefault}}** 會保留此工作區的排列，包括表格欄。**{{label:workspace.restoreMyDefault}}** 會恢復它。
- **{{label:workspace.restoreOriginal}}** 會把此工作區恢復為 Phoenix 最初的排列，並重設其表格欄。你的「我的預設佈局」會保留。
- **{{label:workspace.restorePanelDefaults}}** 會對每個工作區執行此操作，還會重設工具列、快速說明和指南，並清空我的工作區。所有「我的預設佈局」都會保留。

## 排列如何儲存 {#saved}

你的排列會在你工作時儲存，歸屬於你的 Home Assistant 使用者。另一台電腦下次載入 Phoenix 時會採用它。手機保有自己的排列。

## 在手機上 {#phones}

手機把每個工作區的視窗顯示為可捲動的一欄，工具列分成兩列位於頂端。把電話橫放時這一欄保持不變。電腦或平板上的窄版瀏覽器視窗也顯示相同的一欄。

每個標題列都有摺疊視窗、開啟其選單和關閉它的按鈕。手機上不能拖曳。請改用視窗選單：

- **{{label:workspace.moveUp}}** 和 **{{label:workspace.moveDown}}** 會與欄中相鄰視窗交換位置。
- **{{label:workspace.addToMy}}** 和 **{{label:workspace.alwaysLabels}}** 的用法與電腦上相同。
- **{{label:agentchat.maximize}}** 會讓視窗填滿螢幕，**{{label:agentchat.restore}}** 會恢復那一欄。

**{{label:workspace.options}}**，即工具列的最後一個按鈕，會再次顯示已關閉的視窗，並儲存、還原或重設你的排列。

長清單一次顯示 10 列。**{{label:common.loadMore}}** 會顯示接下來的 10 列。

指南會在欄的底部開啟。快速說明和通知歷史在手機上無法使用。

## 鍵盤 {#keyboard}

F6 會移動到下一個區域：工具列、依你看到的順序排列的每個視窗、指南和 Agent Chat。Shift+F6 會反向移動。回到某個區域會回到你上次在該區域的位置，Tab 仍會在其中控制項之間移動。

每個選單都可以用 Enter、空格或下方向鍵開啟。使用方向鍵、Home 和 End 在其中移動，按 Escape 關閉。鍵盤的選單鍵會開啟焦點所在控制項的選單。

當你所在的控制項消失時，例如關閉視窗或決定請求後，焦點會移到該視窗或取代它的視窗。Delete 和 Backspace 不會關閉視窗。

## 使用螢幕閱讀器 {#screen-reader}

面板可以搭配螢幕閱讀器使用。視窗、欄位和按鈕都有你所用語言的名稱，只顯示圖示的按鈕也不例外。

Phoenix 會播報焦點之外發生的變化。停止輸入後，它會說出篩選或搜尋後剩下多少列。錯誤會立即讀出，並一直保留到你關閉。在選取多項時，每勾選一項都會讀出已勾選的請求數量。

在其他位置出現的結果（例如批次核准完成或匯入完成）也會讀出。按 F6 進入快速說明時，會保留你剛離開的控制項的文字。

列可選取的表格只佔一個 Tab 位置。之後用方向鍵、Home 和 End 在列之間移動。在權限中，方向鍵只在層級之間移動；按 Enter 或空白鍵才會設定，所以經過時不會改變任何內容。
