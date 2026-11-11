---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["notes", "instructions", "prompt"], "announce-all": ["tool list", "troubleshooting"], "inline-wait": ["approval", "timeout", "pending"], "assist-exposure": ["exposed", "pass-through"], "oauth-sign-in": ["OAuth", "ChatGPT", "Claude", "connector", "sign in", "phone app"], "oauth-allow-new": ["countdown", "window", "10 minutes", "stop"], "oauth-sign-ins": ["signed-in apps", "sign out", "remove app"], "rate-limiting": ["throttle", "requests per minute"] }, "covers": ["frontend_src/views/AccessKeyDetail.tsx", "frontend_src/components/AccessKeyNotes.tsx", "frontend_src/components/RateLimitConfig.tsx", "frontend_src/components/AccessKeyOAuth.tsx"] }
---
More settings for the selected key, on three tabs. **{{label:accessKeys.cardGuidance}}** holds notes for its agents. **{{label:accessKeys.cardAgentBehavior}}** sets how its tools are offered, how long requests wait for approval, and its rate limits. **{{label:accessKeys.tabOAuth}}** lets apps sign in with the key.

## {{label:accessKeys.cardGuidance}} {#guidance}

Notes Phoenix adds to the instructions of every agent that uses this key, such as what rooms and devices are called or who uses the agent. Up to 2,000 characters. Select **Save** to keep them, or **{{label:accessKeys.notesDiscard}}**.

Guidance is advice. It never grants access, and an agent may ignore it. Agents and their model providers receive it, so never put passwords or other secrets here.

When you change it, connected agents get the new guidance with their next tool result. Phoenix hides entity IDs the key can't see, and removes text that looks like a secret.

## {{label:accessKeys.cardAgentBehavior}} {#agent-behavior}

### {{label:accessKeys.announceAllLabel}} {#announce-all}

Offers the agent every Phoenix tool, even ones this key can't use. Calls to those tools are still refused. Off by default; use it for troubleshooting only, since it costs the model more and causes failed calls.

### {{label:accessKeys.inlineWaitLabel}} {#inline-wait}

How long a request that needs approval waits for your decision before the app gets a "pending" answer: Off (default), or 30 to 180 seconds. With Off, the app hears straight away that the request is waiting in Approvals.

This applies to external apps only. Agent Chat waits up to 15 minutes for your decision. The Voice Agent, Assist and AI Task report that the change is waiting and carry on. If the key already has a request waiting, a new one doesn't wait.

### {{label:accessKeys.assistExposureLabel}} {#assist-exposure}

Limits a pass-through key to the entities exposed to Assist in Home Assistant. Off by default. It appears only for pass-through keys; scoped keys use the permissions tree instead.

## {{label:accessKeys.tabOAuth}} {#oauth-sign-in}

Lets apps that take only a server address, such as ChatGPT, Claude and their phone apps, use this key without you pasting it anywhere. Off by default. Agents that send the key keep working either way.

When you turn on **{{label:oauth.switchLabel}}**, Phoenix shows **{{label:oauth.address}}**. Add a custom connector in the app with that address and leave any client ID empty. The app opens Home Assistant, where you approve the sign-in.

Apps need to reach Home Assistant over HTTPS from the internet. If Home Assistant has no HTTPS address, Phoenix says so and won't turn sign-in on. If you opened Phoenix over plain HTTP, the switch is unavailable and Phoenix links to its HTTPS address. Open Phoenix there to set up and approve sign-ins.

Turning it off signs out every app that uses OAuth with this key. Rotating or revoking the key does the same. The kill switch pauses signed-in apps until you clear it.

Related: [Apps that sign in with OAuth](guide:topic.connecting#oauth)

### {{label:oauth.allowNew}} {#oauth-allow-new}

An app can start a sign-in only for 10 minutes after you allow it. Turning the switch on starts the first 10 minutes. A countdown shows the time left.

Approving a sign-in ends the period, and Phoenix shows which app signed in. Select **{{label:oauth.stopWindow}}** to end it early. Select **{{label:oauth.allowNew}}** before connecting another app, or when an app asks you to sign in again. A sign-in link that arrives while no period is open does nothing.

The countdown is the same everywhere Phoenix shows it, including the connect instructions and the approval dialog.

### {{label:oauth.signInsTitle}} {#oauth-sign-ins}

The apps approved for this key, with where each returns to and when it was last used. A signed-in app stays signed in for as long as the key lasts. Select **{{label:oauth.remove}}**, then **{{label:common.confirm}}**, to sign out one you no longer use; a click anywhere else cancels. A key can have 10 signed-in apps.

Deleting or disconnecting the app on its own side doesn't always tell Phoenix. An app that does tell Phoenix disappears from this list; otherwise it stays until you remove it here. The last-used time shows which ones have stopped.

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

Rate limits are on the **{{label:accessKeys.cardAgentBehavior}}** tab. They apply to external apps only. Agent Chat, the Voice Agent, Assist and AI Task are never limited. Each field saves when you leave it.

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

The maximum requests the key can make in any minute. Set to 0 to turn off rate limiting. New keys start at 60.

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

The maximum requests in any one second. Set to 0 for no per-second limit. New keys start at 10. Greyed out while requests per minute is 0. The messages an app sends when it opens or closes a connection don't count here, only toward the minute.
