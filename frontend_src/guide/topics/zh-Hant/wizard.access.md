---
{ "id": "wizard.access", "title": "為一盞燈授予存取權限", "parent": "workspace.accessKeys", "order": 103 }
---
新金鑰在你授予它內容之前，無法存取任何裝置。請授予一盞你看得到的燈，讓測試能證明一切正常。

選擇 **light** 旁的三角形以展開燈分組。它會列出你的燈裝置，以及不屬於任何裝置的燈。選擇裝置旁的三角形可查看其燈實體。不想逐層瀏覽，可以在清單上方的篩選框中輸入燈的名稱。

每一列有四個按鈕：N 沿用上一層，R 讀取，W 讀取並控制，D 拒絕存取（[更多說明](guide:window.permissions#states)）。在一盞燈上選擇 **W**。在把某項設為 W 之前，**{{label:wizard.next}}** 會一直顯示為灰色；測試只設 R 並不夠。

對裝置的授權會涵蓋其下的實體。對整個燈域的授權會涵蓋每一盞燈，包括之後新增的燈。

如果 Home Assistant 中沒有燈，這一步會說明情況，並在訊息旁顯示 **{{label:wizard.showAllDevices}}**。它會列出所有域，你可以改為授予其他裝置。之後還可以在{{label:accessKeys.permissionsTree}}中授予更多權限。
