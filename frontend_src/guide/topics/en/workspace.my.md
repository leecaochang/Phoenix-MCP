---
{ "id": "workspace.my", "title": "My Workspace", "order": 70, "keywords": ["favorites", "personal", "custom layout"], "sectionKeywords": { "adding": ["add to my workspace", "copy"], "removing": ["close", "remove"], "scrolling": ["vertical scrolling", "taller"], "chat": ["agent chat", "dock", "float agent chat"], "phones": ["mobile"] }, "covers": ["frontend_src/shell/Shell.tsx", "frontend_src/shell/workspaces.tsx", "frontend_src/shell/chatDock.tsx"] }
---
My Workspace gathers the windows you use most from the other workspaces. It starts empty.

Related: [Working with Windows](guide:topic.windows)

## Adding a window {#adding}

Select **{{label:workspace.addToMy}}** in a window's menu. The window also stays in its own workspace. Both copies show the same selected record. Filters, columns, and sizes are separate in each copy.

The first window fills the workspace. Each next one goes beside or under the others, a row at a time.

## Removing a window {#removing}

Closing a window here removes it. To bring it back, add it again from its own workspace. **{{label:workspace.restoreOriginal}}** removes every window.

My Default here keeps the arrangement and the windows in it, so restoring it brings back windows you removed since.

## {{label:workspace.verticalScrolling}} {#scrolling}

Off by default, so the windows share the screen's height. Turn it on from any window's menu here to let the workspace grow taller and scroll. Each window then gets back the height it had before.

With it on, drag the border under a window to make it taller.

## {{label:agentchat.title}} {#chat}

To dock {{label:agentchat.title}} here, select **{{label:workspace.addToMy}}** in its own menu. While another workspace is shown, {{label:agentchat.title}} floats over it. It docks again when you come back here. If you closed it meanwhile, it stays closed. To open it, select **{{label:shell.agentChat}}** in its empty window here. **{{label:agentchat.floatChat}}** undocks it. Closing it here closes {{label:agentchat.title}}.

## On a phone {#phones}

To add a window here, select **{{label:workspace.addToMy}}** in its menu. To remove it, close it here. {{label:agentchat.title}} can't be added on a phone. A phone keeps its own My Workspace, apart from the one on a computer.
