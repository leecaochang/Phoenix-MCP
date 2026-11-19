---
{ "id": "window.agentChat", "title": "{{label:agentchat.title}}", "parent": "workspace.my", "order": 5, "keywords": ["chat", "agent", "assistant", "conversation", "Shift+A"], "sectionKeywords": { "opening": ["open", "shortcut", "Shift+A", "every page", "pop out"], "window": ["minimize", "maximize", "move", "resize", "dock", "close"], "options": ["verbose", "timestamps", "footer", "text size"], "connection": ["access key", "provider", "model", "thinking", "temperature", "account", "Anthropic", "Claude", "DeepSeek", "OpenAI", "ChatGPT", "GPT", "Gemini", "Google", "Grok", "xAI", "Groq", "Kimi", "Moonshot", "Meta", "Llama", "MiniMax", "Mistral AI", "NVIDIA NIM", "Xiaomi MiMo", "Tencent Cloud TokenHub", "OpenCode", "Ollama", "OpenAI-Compatible", "llama.cpp", "Bionic", "LM Studio", "vLLM", "LocalAI", "OpenRouter", "Infron", "Together AI", "Cerebras", "Cloudflare Workers AI", "Microsoft Azure", "Azure", "Azure AI Foundry", "Foundry", "Azure OpenAI", "Workers AI", "Fireworks AI", "Alibaba Cloud Model Studio", "Qwen", "DashScope", "Z.ai", "Zhipu", "GLM"], "conversation": ["send", "stop", "steps", "working", "copy", "tokens", "usage"], "check-in": ["paused", "continue", "steps before check-in"], "memory-warning": ["long message", "context", "cut off", "send anyway", "new chat", "memory"], "images": ["attach", "image", "photo", "paste", "picture"], "approvals": ["approve", "reject", "review", "waiting"], "clear": ["clear chat history", "/clear", "new conversation"], "privacy": ["stored", "browser", "sent to the provider", "history"] }, "covers": ["frontend_src/components/AgentCliWindow.tsx", "frontend_src/inject/agentchat.ts", "frontend_src/utils/agentcli_state.ts", "frontend_src/utils/chat_attachments.ts", "custom_components/phoenix_mcp/agent_chat.py", "custom_components/phoenix_mcp/agent_turn.py", "custom_components/phoenix_mcp/agent_dispatch.py", "custom_components/phoenix_mcp/agent_chat_views.py", "custom_components/phoenix_mcp/llm/", "custom_components/phoenix_mcp/provider_constants.py", "frontend_src/inject/AgentChatWindow.tsx", "frontend_src/utils/agentchat_review.ts", "frontend_src/utils/dialog_presence.ts", "frontend_src/utils/agentchat_shortcut.ts", "frontend_src/utils/chat_notice.ts"] }
---
Talk to an AI agent about your home. It acts through one of your access keys and can do only what that key allows. Phoenix checks and logs every action like any other.

Related: [{{label:settings.agentcliCard}} settings](guide:window.settingsChat), [{{label:workspace.aiProviders}}](guide:window.settingsProviders)

## Opening Agent Chat {#opening}

Select **{{label:shell.agentChat}}** on the toolbar. It appears once you have an access key and an AI provider account. Shift+A also opens and closes it, unless you're typing in a field. It needs keyboard shortcuts turned on in your Home Assistant profile.

With **{{label:settings.agentcliGlobalLabel}}** on (the default), Agent Chat stays open as you move around Home Assistant. With it off, Agent Chat opens only in the Phoenix panel. Leaving the panel ends a reply in progress.

While the kill switch is on, Chat closes and can't be opened.

## The window {#window}

Drag it by its title bar and resize it from any edge. Edges near the side of the screen snap to it. **{{label:agentchat.minimize}}** shrinks it to a small bar you can move, and **{{label:agentchat.maximize}}** fills the screen.

While a Phoenix dialog is open, such as a sign-in to approve, floating Agent Chat steps off the screen so it can't cover the dialog. It comes back when the dialog closes, with the conversation as it was.

**{{label:agentchat.popOut}}** moves Chat to a browser window of its own, and the conversation carries on there. On a phone, Chat opens full screen.

On a computer, you can also dock Chat in [My Workspace](guide:workspace.my#chat). Closing Chat doesn't clear the conversation.

## {{label:agentchat.options}} {#options}

The menu button in the title bar:

- **{{label:agentchat.connection}}** shows the [connection fields](guide:#connection).
- **{{label:agentchat.verbose}}** shows each tool the agent calls, its result, and the model's reasoning when it shares it. Off by default.
- **{{label:agentchat.showTimestamps}}** adds the time under each message. Off by default.
- **{{label:agentchat.showFooter}}** shows the footer with text size and token use. On by default.
- **{{label:agentchat.decreaseTextSize}}** and **{{label:agentchat.increaseTextSize}}** change the size of the conversation's text.

These choices are kept in this browser.

## {{label:agentchat.connection}} {#connection}

The line under the title shows the access key, provider, and model in use. Select its triangle to change them.

- **{{label:agentchat.accessKey}}**: the agent works with this key's permissions.
- **{{label:agentchat.provider}}**: the AI provider account that runs the agent.
- **{{label:agentchat.model}}**: the model to use, from that account's list.
- **{{label:agentchat.thinking}}** and **{{label:agentchat.temperature}}**: shown only when the model offers them. {{label:agentchat.thinking}} starts at the model's own level. It offers {{label:agentchat.levelOff}} only where the model can stop thinking. Leave **{{label:agentchat.temperature}}** empty for the provider's default.

For some providers, Phoenix learns a model's thinking levels only by checking its options. If you select a model that was never checked, Phoenix [checks it](guide:window.settingsProviders#check-options) once.

Changing the access key or provider starts a new conversation. While a reply is running, you can't change the key, provider, or model.

## Talking to the agent {#conversation}

Press Enter to send, or Shift+Enter for a new line. While the agent works, a line shows what it's doing and how long it has waited for the provider. **{{label:agentchat.stop}}** ends the reply and puts your message back in the box.

If the connection drops during a reply, a grey notice tells you. Your message comes back only if nothing had arrived yet, because the agent might already have acted.

If the provider says you're over its rate limit and when to try again, Agent Chat waits and retries for up to a minute. The status line shows it retrying, and **{{label:agentchat.stop}}** still works. The Home Companion persona and the Voice Agent don't wait: they say the provider's rate limit was reached, so you can try again after a minute.

Copy a message with its copy button. The footer shows the tokens used in this conversation, and how much of the model's context the last request took.

## Pausing to check in {#check-in}

After a number of steps without your input, the agent pauses and asks whether to continue. A step is one request to the model plus the tools it asked for. Select **{{label:agentchat.continue}}** to let it go on.

Approving or rejecting an action resets the count. Set the number in [{{label:settings.agentcliStepsLabel}}](guide:window.settingsChat#chat-steps).

## Long messages {#memory-warning}

When sending a message would push your earlier messages out of [{{label:settings.agentcliMemoryLabel}}](guide:window.settingsChat#chat-memory), Phoenix asks first and says how many the model would stop seeing.

- **{{label:agentchat.sendAnyway}}** sends it. The earliest messages stay on screen, but the model no longer sees them.
- **{{label:agentchat.sendInNewChat}}** clears this conversation and sends the message as the first of a new one.
- **{{label:agentchat.editMessage}}** closes the warning so you can shorten the message. Changing the message closes it too.

A conversation that grows slowly past the limit doesn't ask. A notice tells you once that the oldest messages are no longer sent.

## Images {#images}

Paste an image, drop it on the window, or select **{{label:agentchat.attachImage}}**. Each message takes up to four images. Phoenix shrinks large photos before sending them. After shrinking, an image can be up to 2 MiB, and a message's images up to 4 MiB in total.

Images can't be sent with a Home Companion key, or to a model that only reads text. They aren't kept after a reload, so the agent can't see them again later.

## Approvals in the chat {#approvals}

When an action needs your approval, a card appears in the conversation. Select **{{label:agentchat.approve}}** or **{{label:agentchat.reject}}**, or **{{label:agentchat.review}}** to see the full change first in Approvals.

The agent waits up to 15 minutes. Approving in Approvals or from a notification updates the card too. Stopping, closing Agent Chat, or reloading cancels actions that are still waiting.

Related: [How an approval works](guide:topic.approvalLifecycle)

## {{label:agentchat.clearHistory}} {#clear}

Starts a new conversation straight away, without asking. Typing `/clear` does the same. The Conversation Log keeps what it already recorded.

## What's kept, and where {#privacy}

There's one conversation per browser for each Home Assistant user, kept in this browser, so it's still there after a reload. Tabs share it. The newest messages, within [{{label:settings.agentcliMemoryLabel}}](guide:window.settingsChat#chat-memory), and tool results are sent to the provider with each message. Phoenix keeps no copy on the server, except in the Conversation Log when that's on.

If two tabs run a reply at the same moment, the one that finishes last is the one kept. While another tab is running a reply, this tab says so above the message box.

Phoenix deletes a conversation after 30 days without use, and when you delete core data in this browser. Other browsers keep theirs.

While **{{label:settings.agentcliGlobalLabel}}** or **{{label:settings.injectLabel}}** is on, Phoenix also deletes it when someone who isn't an administrator signs in on this browser. With both off, Phoenix doesn't load for that person, so the conversation stays.

Home Assistant closes the connection of a page left hidden for 5 minutes. This cuts off a running reply. For long replies, keep the page in view, or turn off **Suspend background connections** in your Home Assistant profile. When Agent Chat is popped out, the main page behind it counts too.

Related: [What leaves your home](guide:topic.security#leaves-home)

Only administrators can use Agent Chat.
