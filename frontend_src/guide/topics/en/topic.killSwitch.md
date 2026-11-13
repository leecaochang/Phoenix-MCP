---
{ "id": "topic.killSwitch", "title": "What the Kill Switch Stops", "parent": "window.settingsSafety", "order": 1, "keywords": ["emergency", "stop", "disable", "503", "panic"], "covers": ["frontend_src/components/KillSwitch.tsx", "custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/request_authority.py"] }
---
The kill switch cuts off every way an AI can reach your home through Phoenix. It doesn't undo actions that already happened.

## What stops {#stops}

- External apps connected over MCP are refused. Requests already running stop at their next check.
- Agent Chat closes, and can't be opened again.
- A running Agent Chat, Voice or AI Task turn sends nothing more to its AI model. A Voice or AI Task reply still arriving is cut off within a second.
- The Voice Agent only replies that it's unavailable.
- The AI Task entity becomes unavailable.
- Assist Tool Provider offers no tools.
- Approving a waiting request cancels it instead. A configuration recovery review is the exception: it still runs.
- Apps signed in with OAuth are paused, and no app can start a new sign-in. Approved sign-ins work again once the switch is off.
- Open sign-in periods and sign-ins waiting for approval are dropped, so an app that was signing in starts again afterwards.

## What keeps working {#keeps}

This panel keeps working, so you can review what happened and turn the switch off again. You can still restore a change in Changes and edit MESA profiles, and the in-context profile buttons stay. You can still see and remove the apps signed in to a key.

## After a restart {#restart}

If the switch is on when Home Assistant starts, Phoenix doesn't open its connections at all. Turning the switch off opens them, without a restart.

Related: [Emergency kill switch](guide:window.settingsSafety#kill-switch)
