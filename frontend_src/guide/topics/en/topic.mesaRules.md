---
{ "id": "topic.mesaRules", "title": "How MESA Decides", "parent": "workspace.mesa", "order": 10, "keywords": ["MESA", "policy", "safety", "inheritance", "precedence"], "sectionKeywords": { "levels": ["inherit", "main device", "parent device", "scope"], "combining": ["most restrictive", "conflict", "loosen", "override"], "baseline": ["default", "unprofiled", "built-in", "deployment defaults"], "modes": ["advisory", "enforced", "off", "warn", "block"], "privacy": ["restricted", "sensitive", "deny"], "coverage": ["reads", "camera", "automations", "pass-through"], "not-in-editor": ["declared limits", "temporal constraints", "time window", "access roles", "import only", "calendar"], "renames": ["rename", "entity ID", "orphan", "move", "area"], "storage": ["corrupt", "damaged", "restore", "503"] }, "covers": ["custom_components/phoenix_mcp/mesa.py", "custom_components/phoenix_mcp/mesa_core/conflict.py", "custom_components/phoenix_mcp/mesa_core/inheritance.py", "custom_components/phoenix_mcp/mesa_core/profile.py", "custom_components/phoenix_mcp/mesa_core/enforcer.py"] }
---
MESA checks every action an agent takes on a device, after the access key has allowed it. A profile describes how careful to be with what it covers.

MESA is an open specification. Phoenix includes mesa-core, its reference implementation. Its source is on [GitHub](https://github.com/leecaochang/mesa-core).

## Levels {#levels}

A profile covers one of these levels. When an agent acts on an entity, MESA gathers every profile that covers it, most specific first:

- **{{label:mesa.scopeLabelEntity}}**: the entity itself.
- **{{label:mesa.scopeLabelDevice}}**: the device the entity belongs to, including entities the device gains later. It also covers the device's entities that are disabled in Home Assistant.
- **{{label:mesa.scopeLabelParentDevice}}**: for a device that's part of a larger one, the larger device's profile. A hub that a device connects through doesn't count.
- **{{label:mesa.scopeLabelArea}}**: the entity's area, or else its device's area.
- **{{label:mesa.scopeLabelIntegration}}**: the integration that created the entity, such as hue.
- **{{label:mesa.scopeLabelDomain}}**: every entity of one kind, such as every lock.

Add profiles for all of these in **{{label:workspace.profileEditor}}**, except the main device. A main device profile is a device profile seen from its parts.

## How profiles combine {#combining}

MESA combines profiles field by field, not profile by profile.

- **Control mode**: the most restrictive mode any level sets wins. A specific profile can't loosen a broader one, so an {{label:mesa.modeAutonomous}} lock entity under a {{label:mesa.modeProhibited}} lock domain stays {{label:mesa.modeProhibited}}. The entity override is the one exception.
- **Privacy level**: the most restrictive level wins, in the same way.
- **Triggers automations**: Likely at any level wins. Otherwise the most specific level wins.
- **Everything else**: the most specific level wins. A value that a person wrote or confirmed, or that the device's maker wrote, outranks an inferred one at any level.

The one way to loosen a control mode is an entity override with a written reason. It can turn {{label:mesa.modeConfirm}} into {{label:mesa.modeAutonomous}}, but never relaxes {{label:mesa.modeProhibited}} or {{label:mesa.modeReadOnly}}. {{label:workspace.profileEditor}} can't set it. It arrives only by import.

Related: [Effective Resolution](guide:window.mesaEditor#effective-resolution)

## The built-in baseline {#baseline}

An entity that no profile covers follows a built-in baseline: lights are {{label:mesa.modeAutonomous}}, locks and alarm panels are {{label:mesa.modeProhibited}}, and everything else is {{label:mesa.modeConfirm}}.

A profile changes that:

- A profile that sets a control mode replaces the baseline, even when it's looser. A {{label:mesa.modeConfirm}} profile on a lock makes the lock {{label:mesa.modeConfirm}} instead of {{label:mesa.modeProhibited}}.
- Only an entity, device, or domain profile can loosen a {{label:mesa.modeProhibited}} or {{label:mesa.modeReadOnly}} baseline. An area, main device, or integration profile can only make it stricter, so a lock in an {{label:mesa.modeAutonomous}} area stays {{label:mesa.modeProhibited}}.
- A profile that leaves control mode unset keeps the baseline, but anything {{label:mesa.modeAutonomous}} becomes {{label:mesa.modeConfirm}}. A tags-only profile on a light makes that light need confirmation.

So an {{label:mesa.modeAutonomous}} domain profile for locks makes every lock {{label:mesa.modeAutonomous}}, unless a stricter profile covers it. Check a profile's effect in [Effective Resolution](guide:window.mesaEditor#effective-resolution) before you rely on it.

Deployment defaults can replace the built-in baseline for unprofiled entities. They come from an imported archive. {{label:workspace.profileEditor}} can't edit them.

## Profiles that integrations ship {#integration-profiles}

A custom integration can include its own profile, a `mesa_profile.json` file in its folder. Each time Phoenix starts, it adds that file as the integration's profile. It doesn't ask you first.

Such a profile can loosen the baseline, except a {{label:mesa.modeProhibited}} or {{label:mesa.modeReadOnly}} one. An integration that marks its switches {{label:mesa.modeAutonomous}} makes them {{label:mesa.modeAutonomous}}, but its locks stay {{label:mesa.modeProhibited}}. In [Effective Resolution](guide:window.mesaEditor#effective-resolution), **{{label:mesa.colOrigin}}** shows developer for these values.

To override it, save your own profile for that integration. Phoenix leaves your profile in place and skips the integration's file.

## What happens when an action breaks a rule {#modes}

What MESA does depends on **{{label:settings.mesaModeLabel}}** in [Settings](guide:window.settingsSafety#mesa-mode), and on the profile's own **{{label:mesa.fieldEnforcement}}**.

- **{{label:settings.mesaModeOff}}**: MESA allows everything.
- **{{label:settings.mesaModeAdvisory}}** (default): {{label:mesa.modeProhibited}} actions and broken limits only warn the agent, and {{label:mesa.modeConfirm}} actions run without approval. {{label:mesa.modeReadOnly}} entities and privacy denials still block.
- **{{label:settings.mesaModeEnforced}}**: MESA blocks {{label:mesa.modeProhibited}} and {{label:mesa.modeReadOnly}} actions and broken limits. {{label:mesa.modeConfirm}} actions wait for your approval.

Related: [How an approval works](guide:topic.approvalLifecycle)

A profile set to **{{label:mesa.modeEnforced}}** is enforced while the setting is {{label:settings.mesaModeAdvisory}}. While the setting is {{label:settings.mesaModeOff}}, nothing is enforced.

When an action targets several entities, MESA drops the blocked ones and the action runs on the rest. A warning or a block reaches the agent with the reason.

A group is different. If MESA blocks one member, it blocks the whole group. If a member needs confirmation, the whole group waits for your approval. A Switch as X entity works the same way, through the switch behind it.

## Privacy {#privacy}

A **{{label:mesa.privRestricted}}** privacy level makes an {{label:mesa.modeAutonomous}} entity need confirmation. People are always at least **{{label:mesa.privSensitive}}**.

An imported profile can also deny access to certain personas. That denial blocks the action in every mode except {{label:settings.mesaModeOff}}, including camera images.

## What MESA covers {#coverage}

MESA applies the same way to every source: MCP clients, Agent Chat, the Voice Agent, Assist, and AI Task. It applies to pass-through access keys too.

It checks:

- Device actions, whichever command the agent uses.
- Renaming, enabling, disabling, and removing entities and devices, and moving them out of a profiled area or into one that would loosen them.
- Integration and helper changes, areas, and floors.
- Calendar and to-do changes, and notifications.
- Zigbee device changes, raw Z-Wave and ISY commands, `update.install`, ESPHome firmware installs, and ESPHome device actions. These must pass on every entity of the device and its child devices.
- An ESPHome file whose device Phoenix can't work out. This only warns in {{label:settings.mesaModeAdvisory}}, and is refused in {{label:settings.mesaModeEnforced}} once any ESPHome device is set up.

Phoenix can't work out the device of a file that takes its name from a package or a substitution. The same goes for a new file that the Device Builder doesn't list.

It doesn't check:

- Changes to automations, scripts, blueprints, scenes, dashboards, dashboard resources, labels, categories, or the Energy setup.
- Backups, restarts, YAML reloads, and log levels.
- ESPHome builds, and opening a Zigbee network to new devices.

Reading a state isn't checked, except camera images. Automations and scripts run inside Home Assistant, so their actions aren't checked again. To control when an agent starts one, give the automation or script its own profile.

## Renaming and moving {#renames}

When you rename an entity in Home Assistant, its profile follows it straight away. If Phoenix wasn't running at that moment, the profile follows the next time Phoenix starts or reloads.

If the new ID already has a profile, Phoenix keeps both. The new ID takes the stricter control mode, enforcement, and privacy of the two and the limits of both. The other profile shows as an orphan for you to review. An agent's rename works the same way, after MESA has checked the rename itself.

Moving something out of an area with a profile counts as a change, because it could lift a rule. Moving something into one counts as a change only when the area's profile would loosen its control mode, such as a switch moved into an {{label:mesa.modeAutonomous}} area. MESA checks the move against the control mode the entity has before it moves.

## If MESA can't read its profiles {#storage}

If the saved profiles are damaged, MESA doesn't start with an empty list. It stays off, protected changes pause, and [{{label:attention.title}}](guide:window.attention#problems) tells you.

The Home Assistant log says what to do: put the named copy back, or delete it to start with no profiles. Then reload Phoenix.

If Phoenix can't save a profile change, it undoes the change and shows an error, so the saved rules stay in force. A rename is the exception: the profile stays with the renamed entity, and {{label:attention.title}} tells you until a save works.

## What {{label:workspace.profileEditor}} can't set {#not-in-editor}

A profile can also hold fields that arrive only in an imported archive:

- Declared limits, such as a maximum temperature
- Time windows when stricter rules apply
- Privacy denials for certain personas
- The entity override

A limit also covers the same request sent another way. `homeassistant.turn_on` counts as the entity's own turn on. Every way of setting a light's brightness counts against a brightness limit.

`fan.turn_on`, `fan.set_percentage`, and a speed step count as one fan percentage. A thermostat's `temperature`, `target_temp_high`, and `target_temp_low` count against each other's limits. A light turned on with no brightness counts at its default light profile's brightness. Without such a profile, a limit can't see the level the light restores.

A limit doesn't reach a different action with a similar effect. A command that sends no value, such as a plain turn on, passes unless the limit says to refuse it.

A time window can follow a calendar. It applies while the calendar is on, and also when Phoenix can't tell.

Saving a profile in {{label:workspace.profileEditor}} replaces the whole profile with what the form shows, so it removes these fields.

Related: [{{label:mesa.importTitle}}](guide:window.mesaProfiles#import)
