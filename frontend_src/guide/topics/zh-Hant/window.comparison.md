---
{ "id": "window.comparison", "title": "{{label:workspace.comparison}}", "parent": "workspace.changes", "order": 2, "keywords": ["差異", "更改前", "更改後"], "sectionKeywords": { "restore": ["復原", "回滾", "還原"], "views": ["程式碼編輯器", "並排", "堆疊", "預覽"] } }
---
所選變更，即該項目在此變更前後的樣子。標題下方的列說明變更是什麼、類型、發起者和時間，隨後是簡短摘要。

## 在變更間切換 {#step}

**{{label:workspace.previousRecord}}** 和 **{{label:workspace.nextRecord}}** 會在你選擇變更的清單中移動：變更清單或項目歷史。

## 檢視 {#views}

前後內容會逐行比較，刪除的列顯示為紅色，新增的列顯示為綠色。在 **{{label:changes.lineViewShort}}** 和 **{{label:changes.codeEditorShort}}** 之間切換，也在 **{{label:changes.stackedShort}}** 和 **{{label:changes.sideBySideShort}}** 之間切換。

儀表板變更也可以顯示為 **{{label:common.preview}}**，繪製變更前或變更後的儀表板。這些選擇會儲存在此瀏覽器中，並與核准共用。

## {{label:changes.restore}} {#restore}

把項目恢復到此變更之前（**{{label:changes.restoreBefore}}**）或之後（**{{label:changes.restoreAfter}}**）的狀態。還原以你的管理員權限執行，因此無需核准。

顯示為灰色的選項在這裡無法使用，原因會顯示在其下方。例如，不存在的一側、項目最新變更的更改後、已刪除的實體，或包含 !secret 等 YAML 標籤的版本。即使確認後，如果還原不再符合目前項目，仍可能被拒絕。

相關：[還原可以放回什麼](guide:topic.configurationHistory#restore)

### 確認 {#confirm}

選擇一側會要求你就地確認。選擇 **{{label:changes.confirmRestoreButton}}**，或選擇 **{{label:changes.cancelRestore}}** 退出。

如果你確認時項目已有更新的變更，Phoenix 不會還原任何內容並會告知你，因此請先檢查更新的變更。伺服器也會檢查這一點，因此最後一刻到達的變更也會被捕捉。

### 還原之後 {#after-restore}

還原會記錄為一次新變更，即回滾，並成為所選變更。若要復原它，請還原該回滾的更改前。
