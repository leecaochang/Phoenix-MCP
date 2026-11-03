---
{ "id": "topic.windows", "title": "Working with Windows", "parent": "workspace.my", "order": 10, "keywords": ["layout", "arrange", "workspace", "window", "toolbar"], "sectionKeywords": { "toolbar": ["workspace buttons", "help menu", "documentation", "tools"], "toolbar-options": ["icons only", "icons and text", "single row", "dock", "float toolbar", "collapse"], "title-bar": ["fold", "maximize", "close", "float", "dock window"], "window-menu": ["move left", "move right", "move up", "move down", "pin", "unpin", "resize with keyboard", "always show icons and text"], "arranging": ["drag", "resize", "border", "scroll"], "closing": ["show window", "reopen", "hidden"], "defaults": ["my default", "reset", "reset this workspace", "reset all workspaces", "layout"], "saved": ["sync", "other computer", "browser"], "phones": ["mobile", "narrow", "tablet", "move up", "move down", "load more", "rows"], "keyboard": ["shortcuts", "context menu", "escape"], "guide": ["guide", "pop out", "pop in", "separate window", "contents", "search"] }, "covers": ["frontend_src/shell/Toolbar.tsx", "frontend_src/shell/Shell.tsx", "frontend_src/shell/dock/TitleBar.tsx", "frontend_src/shell/dock/controller.ts", "frontend_src/shell/storage.ts", "frontend_src/shell/PhoneStack.tsx", "frontend_src/shell/phoneLayout.ts", "frontend_src/ui/table/rowLimit.ts", "frontend_src/shell/dock/", "frontend_src/shell/areaFocus.ts", "frontend_src/shell/sync.ts", "frontend_src/shell/toolbarFit.ts", "frontend_src/shell/tools.ts", "frontend_src/shell/windowTools.tsx", "frontend_src/shell/Reconnecting.tsx", "frontend_src/shell/guide.tsx", "frontend_src/shell/guideTarget.ts", "frontend_src/guide/search.ts", "frontend_src/utils/connection.ts", "frontend_src/utils/list_search.ts", "frontend_src/ui/ActionButton.tsx", "frontend_src/ui/Menu.tsx", "frontend_src/ui/InlineConfirm.tsx", "frontend_src/ui/table/settings.ts", "frontend_src/ui/popup.ts"] }
---
Each workspace is a set of windows you can arrange as you like. Phoenix remembers the arrangement for you, on every computer you sign in from.

## The toolbar {#toolbar}

The buttons switch workspaces. Approvals shows how many requests are waiting. **{{label:shell.agentChat}}** opens Agent Chat, and **{{label:shell.help}}** turns Quick Help and the Guide on or off.

The second row holds the tools of the windows you have open, such as Filters or Refresh. A tool several windows share shows once and acts on all of them.

## {{label:workspace.toolbarOptions}} {#toolbar-options}

Open them from the toolbar's last button, or right-click the toolbar.

- **{{label:workspace.iconsOnly}}** (default) or **{{label:workspace.iconsAndText}}**.
- **{{label:workspace.singleRow}}** puts the tools on the same row as the workspace buttons while they fit, and goes back to two rows when they don't.
- Dock the toolbar along any edge, or **{{label:workspace.floatToolbar}}** to place it anywhere. You can also drag its grip to float it.
- **{{label:workspace.collapseToolbar}}** shrinks it to its grip.

## The title bar {#title-bar}

Drag a window by its title bar. The buttons beside the title fold it to its title bar, float it above the workspace, maximize it, and close it.

A floating window stays put while the workspace scrolls. **{{label:workspace.dockWindow}}** puts it back where it was. From its menu, the Move items nudge it across the workspace, and **{{label:workspace.resizeKeyboard}}** sizes it.

## The window menu {#window-menu}

Open it from the title bar's menu button, or right-click the title bar.

- **{{label:workspace.moveLeft}}** and the other moves put the window past its neighbor on that side, without dragging.
- **{{label:workspace.resizeKeyboard}}**: Left and Right Arrow change the width, Up and Down the height. Press Escape when you're done.
- **{{label:workspace.pinTop}}** and **{{label:workspace.pinBottom}}** keep a window in a band that doesn't scroll. **{{label:workspace.unpin}}** returns it.
- **{{label:workspace.addToMy}}** copies it to [My Workspace](guide:workspace.my).
- **{{label:workspace.alwaysLabels}}** shows its tools with their names, even when the toolbar shows icons only.

## The Guide {#guide}

The Guide is one window over every workspace. It shows the page for the window you're working in, and selecting a control takes it to the section about that control. Use Back and Forward, Contents and Search at the top to find your way.

The first time it opens, the Guide shows [Welcome to the Guide](guide:topic.welcome), a short demo of what it does. After that it opens on your page, and **{{label:guide.back}}** from the first page still reaches Welcome. **{{label:workspace.restorePanelDefaults}}** shows it first again.

Fold, maximize and close it from its title bar, or move and resize it with the keyboard from its menu. Closing it anywhere closes it everywhere. Beside the setup wizard it has none of these buttons and stays open, but you can still drag and resize it.

**{{label:agentchat.popOut}}** moves the Guide to a browser window of its own, so it can sit beside Home Assistant. It keeps following what you select in the panel. **{{label:agentchat.popIn}}** brings it back, and closing that window closes the Guide.

It stays popped out only while the Phoenix panel is open. After a reload it's back inside the panel. It isn't offered on a phone, or while it sits beside the setup.

## Arranging windows {#arranging}

Drop a window on another window's edge to put it beside, above or below it. Up to four windows fit side by side. Drag the border between two windows to resize them.

Access Keys and Settings scroll, and each window is as tall as its content. The other workspaces fit the screen, so their windows share its height.

## Closing and showing windows {#closing}

Closing a window hides it in this workspace. Show it again from **{{label:workspace.options}}**, and it returns to the place it had.

Quick Help comes back from the **{{label:shell.help}}** menu, and Needs Attention from **{{label:workspace.showAttention}}**. In My Workspace, closing a window removes it.

## Your arrangements {#defaults}

**{{label:workspace.options}}** also opens by right-clicking an empty part of the workspace.

- **{{label:workspace.saveMyDefault}}** keeps this workspace's arrangement, including table columns. **{{label:workspace.restoreMyDefault}}** brings it back.
- **{{label:workspace.restoreOriginal}}** puts this workspace back as Phoenix first arranged it, and resets its table columns. Your My Default stays.
- **{{label:workspace.restorePanelDefaults}}** does that for every workspace, and also resets the toolbar, Quick Help and the Guide, and empties My Workspace. Every My Default stays.

## How arrangements are saved {#saved}

Your arrangement is saved as you work, for your Home Assistant user. Another computer picks it up the next time it loads Phoenix. Phones keep an arrangement of their own.

## On a phone {#phones}

A phone shows each workspace's windows in one column you scroll, with the toolbar in two rows across the top. The column stays when you turn the phone sideways. A narrow browser window on a computer or tablet shows the same column.

Each title bar has buttons to fold the window, open its menu and close it. There's no dragging on a phone. Use the window menu instead:

- **{{label:workspace.moveUp}}** and **{{label:workspace.moveDown}}** swap the window with its neighbor in the column.
- **{{label:workspace.addToMy}}** and **{{label:workspace.alwaysLabels}}** work as they do on a computer.
- **{{label:agentchat.maximize}}** fills the screen with the window, and **{{label:agentchat.restore}}** brings the column back.

**{{label:workspace.options}}**, the toolbar's last button, shows closed windows again and saves, restores or resets your arrangement.

Long lists show 10 rows at a time. **{{label:common.loadMore}}** shows the next 10.

The Guide opens at the bottom of the column. Quick Help and Notification History aren't available on a phone.

## Keyboard {#keyboard}

F6 moves to the next area: the toolbar, each window in the order you see them, the Guide and Agent Chat. Shift+F6 moves back. Coming back to an area returns you to where you were in it, and Tab still moves between the controls inside it.

Every menu opens with Enter, Space or Down Arrow. Use the arrow keys, Home and End to move through it, and Escape to close it. The keyboard's menu key opens the menu of the control in focus.

When the control you're on goes away, such as a window you close or a request you decide, focus moves to that window or the one in its place. Delete and Backspace never close a window.
