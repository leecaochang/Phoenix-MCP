---
{ "id": "topic.windows", "title": "Working with Windows", "parent": "workspace.my", "order": 10, "keywords": ["layout", "arrange", "workspace", "window", "toolbar"], "sectionKeywords": { "toolbar": ["workspace buttons", "documentation", "tools"], "toolbar-options": ["icons only", "icons and text", "single row", "dock", "float toolbar", "collapse"], "title-bar": ["fold", "maximize", "close", "float", "dock window"], "window-menu": ["move left", "move right", "move up", "move down", "pin", "unpin", "resize with keyboard", "always show icons and text"], "arranging": ["drag", "resize", "border", "scroll"], "closing": ["show window", "reopen", "hidden"], "defaults": ["my default", "reset", "reset this workspace", "reset all workspaces", "layout"], "saved": ["sync", "other computer", "browser"], "phones": ["mobile", "narrow"], "keyboard": ["shortcuts", "context menu", "escape"] }, "covers": ["frontend_src/shell/Toolbar.tsx", "frontend_src/shell/Shell.tsx", "frontend_src/shell/dock/TitleBar.tsx", "frontend_src/shell/dock/controller.ts", "frontend_src/shell/storage.ts", "frontend_src/shell/PhoneStack.tsx"] }
---
Each workspace is a set of windows you can arrange as you like. Phoenix remembers the arrangement for you, on every computer you sign in from.

## The toolbar {#toolbar}

The buttons switch workspaces. Approvals shows how many requests are waiting. **{{label:shell.agentChat}}** opens Agent Chat, and **{{label:shell.documentation}}** turns the Help bar and the Guide on or off.

The second row holds the tools of the windows you have open, such as Filters or Refresh. A tool several windows share shows once and acts on all of them.

## {{label:workspace.toolbarOptions}} {#toolbar-options}

Open them from the toolbar's last button, or right-click the toolbar.

- **{{label:workspace.iconsOnly}}** (default) or **{{label:workspace.iconsAndText}}**.
- **{{label:workspace.singleRow}}** puts the tools on the same row as the workspace buttons while they fit, and goes back to two rows when they don't.
- Dock the toolbar along any edge, or **{{label:workspace.floatToolbar}}** to place it anywhere. You can also drag its grip to float it.
- **{{label:workspace.collapseToolbar}}** shrinks it to its grip.

## The title bar {#title-bar}

Drag a window by its title bar. The buttons beside the title fold it to its title bar, float it above the workspace, maximize it, and close it.

A floating window stays put while the workspace scrolls. **{{label:workspace.dockWindow}}** puts it back where it was.

## The window menu {#window-menu}

Open it from the title bar's menu button, or right-click the title bar.

- **{{label:workspace.moveLeft}}** and the other moves put the window past its neighbor on that side, without dragging.
- **{{label:workspace.resizeKeyboard}}**: Left and Right Arrow change the width, Up and Down the height. Press Escape when you're done.
- **{{label:workspace.pinTop}}** and **{{label:workspace.pinBottom}}** keep a window in a band that doesn't scroll. **{{label:workspace.unpin}}** returns it.
- **{{label:workspace.addToMy}}** copies it to [My Workspace](guide:workspace.my).
- **{{label:workspace.alwaysLabels}}** shows its tools with their names, even when the toolbar shows icons only.

## Arranging windows {#arranging}

Drop a window on another window's edge to put it beside, above or below it. Up to four windows fit side by side. Drag the border between two windows to resize them.

Access Keys and Settings scroll, and each window is as tall as its content. The other workspaces fit the screen, so their windows share its height.

## Closing and showing windows {#closing}

Closing a window hides it in this workspace. Show it again from **{{label:workspace.options}}**, and it returns to the place it had.

Help comes back from **{{label:help.showHelpBar}}** in the Documentation menu, and Needs Attention from **{{label:workspace.showAttention}}**. In My Workspace, closing a window removes it.

## Your arrangements {#defaults}

**{{label:workspace.options}}** also opens by right-clicking an empty part of the workspace.

- **{{label:workspace.saveMyDefault}}** keeps this workspace's arrangement, including table columns. **{{label:workspace.restoreMyDefault}}** brings it back.
- **{{label:workspace.restoreOriginal}}** puts this workspace back as Phoenix first arranged it, and resets its table columns. Your My Default stays.
- **{{label:workspace.restorePanelDefaults}}** does that for every workspace, and also resets the toolbar, Help and the Guide, and empties My Workspace. Every My Default stays.

## How arrangements are saved {#saved}

Your arrangement is saved as you work, for your Home Assistant user. Another computer picks it up the next time it loads Phoenix. Phones keep an arrangement of their own.

## On a phone {#phones}

Windows stack in one column you scroll, with the toolbar at the top. Fold a window from its title bar. Window menus, dragging and Workspace Options are desktop only.

## Keyboard {#keyboard}

Every menu opens with Enter, Space or Down Arrow. Use the arrow keys, Home and End to move through it, and Escape to close it. The keyboard's menu key opens the menu of the control in focus.
