---
{ "id": "topic.killSwitch", "title": "What the Kill Switch Stops", "parent": "window.settingsSafety", "order": 1, "keywords": ["emergency", "stop", "disable", "503", "panic"], "covers": ["frontend_src/components/KillSwitch.tsx", "custom_components/phoenix_mcp/helpers.py", "custom_components/phoenix_mcp/request_authority.py"] }
---
The kill switch cuts off every way an AI can reach your home through Phoenix. It doesn't undo actions that already happened.

## What stops {#stops}

- External apps connected over MCP are refused. Requests already running stop at their next check.
- Agent Chat closes, and can't be opened again.
- The Voice Agent only replies that it's unavailable.
- The AI Task entity becomes unavailable.
- Assist Tool Provider offers no tools.
- Approving a waiting request cancels it instead.

## What keeps working {#keeps}

This panel keeps working, so you can review what happened and turn the switch off again. You can still edit MESA profiles, and the in-context profile buttons stay.

## After a restart {#restart}

If the switch is on when Home Assistant starts, Phoenix doesn't open its connections at all. Turning the switch off opens them, without a restart.

Related: [Emergency kill switch](guide:window.settingsSafety#kill-switch)
