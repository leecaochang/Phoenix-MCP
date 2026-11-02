---
{ "id": "workspace.my", "title": "My Workspace", "order": 70, "keywords": ["favorites", "personal", "custom layout"], "sectionKeywords": { "adding": ["add to my workspace", "copy"], "removing": ["close", "remove"], "scrolling": ["vertical scrolling", "taller"], "chat": ["agent chat", "dock", "float agent chat"], "phones": ["mobile"] }, "covers": ["frontend_src/shell/Shell.tsx", "frontend_src/shell/workspaces.tsx", "frontend_src/shell/chatDock.tsx"] }
---
My Workspace gathers the windows you use most from the other workspaces. It starts empty.

Related: [Working with Windows](guide:topic.windows)

## Adding a window {#adding}

Choose **{{label:workspace.addToMy}}** in a window's menu. The window stays in its own workspace too, and both copies show the same chosen record. Filters, columns and sizes are separate in each copy.

The first window fills the workspace. Each next one goes beside or under the others, a row at a time.

## Removing a window {#removing}

Closing a window here removes it. To bring it back, add it again from its own workspace. **{{label:workspace.restoreOriginal}}** removes every window.

My Default here keeps the arrangement and the windows in it, so restoring it brings back windows you removed since.

## {{label:workspace.verticalScrolling}} {#scrolling}

Off by default, so the windows share the screen's height. Turn it on from any window's menu here to let the workspace grow taller and scroll. Each window then gets back the height it had before.

With it on, drag the border under a window to make it taller.

## Agent Chat {#chat}

Choose **{{label:workspace.addToMy}}** in Agent Chat's own menu to dock it here. While another workspace is shown, Chat floats over it, and it docks again when you come back here. If you closed it meanwhile, it stays closed; **{{label:shell.agentChat}}** in its window opens it. **{{label:agentchat.floatChat}}** undocks it, and closing it here closes Chat.

## On a phone {#phones}

Choose **{{label:workspace.addToMy}}** in a window's menu to add it here, and close it here to remove it. Agent Chat can't be added on a phone. A phone keeps its own My Workspace, apart from the one on a computer.
