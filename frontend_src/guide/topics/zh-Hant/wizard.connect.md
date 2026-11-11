---
{ "id": "wizard.connect", "title": "連線外部應用程式", "parent": "workspace.accessKeys", "order": 106 }
---
選擇你的應用程式，取得確切的命令或設定，其中已填入此金鑰的機密值。現在就複製：Phoenix 只儲存金鑰的雜湊，無法再次顯示該值。如果遺失，請輪換金鑰以取得新值。

對於 ChatGPT、Claude 或它們的手機應用程式，請選擇 **{{label:wizard.methodOAuth}}**。這些應用程式透過 OAuth 登入：在那裡開啟 **{{label:oauth.switchLabel}}**，然後依照你的應用程式的步驟操作。不會複製任何機密內容。

**{{label:wizard.testConnection}}** 會等待應用程式的第一個請求。**{{label:wizard.connectLater}}** 會現在完成，把測試留到之後。

這一步會在你設定應用程式時監看此金鑰。當應用程式使用此金鑰登入，或智慧體傳送第一個請求時，Phoenix 會提示你，**{{label:wizard.next}}** 會略過測試。如果核准對話方塊列出多個金鑰，請選擇這一個；最後開啟登入的金鑰會列在最前面。
