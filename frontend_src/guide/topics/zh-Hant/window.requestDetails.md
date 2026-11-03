---
{ "id": "window.requestDetails", "title": "{{label:workspace.requestDetails}}", "parent": "workspace.approvals", "order": 3, "keywords": ["參數", "原始", "結果", "錯誤"] }
---
審核中所選請求的技術記錄。它隨工作區在審核下方開啟；如果關閉了它，可從 **{{label:workspace.options}}** 重新開啟。

每個部分都可以摺疊，並會為你選擇的下一個請求保持摺疊或展開狀態。

## {{label:approvals.colRequest}} {#request}

存取金鑰、工具、能力、請求發出以及過期或決定的時間，以及其狀態。已決定的請求還會顯示結束原因。

## {{label:approvals.detailTabArgs}} {#raw-args}

智慧體傳送的參數。看起來像機密的值會被隱藏，Phoenix 自己的內部參數也會隱藏。預設為摺疊。

## {{label:approvals.detailTabResult}} {#result}

已決定請求的操作傳回內容，包括任何錯誤。

## 一小時之後 {#pruned}

請求決定一小時後，Phoenix 會清除其參數、更改和結果。請求會連同結果保留在歷史中，但審核和這些部分沒有內容可顯示。
