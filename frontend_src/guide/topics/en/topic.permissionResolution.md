---
{ "id": "topic.permissionResolution", "title": "How a Permission Is Decided", "parent": "window.permissions", "order": 1, "keywords": ["precedence", "inheritance", "resolve", "why denied"], "sectionKeywords": { "examples": ["example", "worked example", "recipe"], "indirect": ["indirect control", "automation", "script", "scene", "trigger", "!"], "always-blocked": ["blocked", "never", "phoenix_mcp", "internal sensors"] }, "covers": ["custom_components/phoenix_mcp/policy_engine.py", "custom_components/phoenix_mcp/device_semantics.py"] }
---
Phoenix decides every request the same way, whichever app or feature sends it.

## The order {#order}

1. An entity that doesn't exist is NOT FOUND.
2. Phoenix's own entities are never reachable.
3. A Home Companion key reaches only its household devices.
4. A pass-through key reaches everything else, or only Assist-exposed entities if you limited it.
5. Otherwise Phoenix reads the tree: the entity, its device, the main device it's part of (if any), then its domain.

## Deny wins {#deny}

A D anywhere in that chain denies, even when the entity itself is set to W.

A group with a D member is refused too, because acting on the group would reach that member. In a wider request, such as a whole area, Phoenix leaves the group out and acts on the rest. A Switch as X entity counts as a group holding the switch behind it. Scenes, scripts and automations don't count as groups.

## The nearest grant decides {#nearest}

Otherwise the nearest R or W decides. An entity's own setting beats its device's, and a device's beats its domain's. If every level is N, the answer is NO ACCESS.

## Examples {#examples}

### Read every light, control one room {#example-one-room}

Set the light domain to R and the living room light to W. Every light is READ, and the living room light is WRITE, because its own setting is nearest.

### Control every light except one bedroom {#example-except}

Set the light domain to W and the guest bedroom's device to D. Every other light is WRITE, and the bedroom's lights are DENY.

### Block one sensor on a device you allow {#example-one-sensor}

Set the media player's device to W and its signal strength sensor to D. The agent controls the player but can't see that sensor.

## Assist and the Voice Agent {#household}

When someone speaks to Assist or the Voice Agent, Phoenix also limits the result to what that Home Assistant user may see and control.

Related: [Capabilities for household speakers](guide:topic.tools#household)

## Disabled entities {#registry-only}

An entity that's disabled, or has no current state, is visible only through a grant on its device or domain.

## What the tree shows {#tree-label}

The result after each name in the tree follows the tree. On a Home Companion key it also shows the companion's limits: devices it can't reach show No Access, and Write on a sensor shows Read. It doesn't take the person speaking into account.

## Control through automations, scripts and scenes {#indirect}

An automation, script or scene runs as Home Assistant, not as the agent. So W on one lets an agent trigger actions its own key can't reach. A key with D on a door lock can still unlock it by running an automation that does.

R is safe for triggering: the agent can see these entities but can't run them. The tree marks these domains with "!".

To guard a trigger, give the automation, script or scene a MESA profile set to Confirm. When {{label:settings.mesaModeLabel}} or that profile is {{label:settings.mesaModeEnforced}}, each run waits for your approval, whatever its actions do. Under {{label:settings.mesaModeAdvisory}}, the run goes ahead. [{{label:mesa.suggestTitle}}](guide:window.mesaSuggestions) finds the ones that reach risky devices.

An approval covers the exact entities, and any group members, it was requested for. If the group changes, the agent must ask again. Phoenix doesn't check what a scene or script then does.

An entity the agent names in a run's inputs is different: it [needs the same access as a target](guide:topic.tools#actions). Phoenix sees only what an input names outright, so a script that builds an entity ID from parts gets past this.

Generic Thermostat and Generic Hygrostat helpers also act as Home Assistant. Phoenix doesn't look through them to the switch they drive.

Related: [Automations and scripts](guide:topic.tools#automations)

## Always blocked {#always-blocked}

Whatever the tree says, and with pass-through too, no key can reach Phoenix's own entities, such as its access key sensors. Secrets in attributes are removed as well.

Related: [What agents never see](guide:topic.security#hidden)
