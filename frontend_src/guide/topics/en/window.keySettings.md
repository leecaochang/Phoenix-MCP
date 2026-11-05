---
{ "id": "window.keySettings", "title": "{{label:workspace.keySettings}}", "parent": "workspace.accessKeys", "order": 6, "sectionKeywords": { "guidance": ["notes", "instructions", "prompt"], "announce-all": ["tool list", "troubleshooting"], "inline-wait": ["approval", "timeout", "pending"], "assist-exposure": ["exposed", "pass-through"], "rate-limiting": ["throttle", "requests per minute"] }, "covers": ["frontend_src/views/AccessKeyDetail.tsx", "frontend_src/components/AccessKeyNotes.tsx", "frontend_src/components/RateLimitConfig.tsx"] }
---
More settings for the selected key: guidance for its agents, how its tools are offered, how long requests wait for approval, and rate limits.

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

## {{label:accessKeys.cardRateLimiting}} {#rate-limiting}

Rate limits apply to external apps only. Agent Chat, the Voice Agent, Assist and AI Task are never limited. Each field saves when you leave it.

### {{label:accessKeys.rateRequestsLabel}} {#rate-requests}

The maximum requests the key can make in any minute. Set to 0 to turn off rate limiting. New keys start at 60.

### {{label:accessKeys.rateBurstLabel}} {#rate-burst}

The maximum requests in any one second. Set to 0 for no per-second limit. New keys start at 10. Greyed out while requests per minute is 0.
