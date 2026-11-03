---
{ "id": "wizard.access", "title": "Granting Access to One Light", "parent": "workspace.accessKeys", "order": 103, "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
A new key can't reach any device until you grant it something. Grant one light you can see, so the test shows everything working.

Select the triangle beside **light** to open the light group. It lists your light devices, and any lights that belong to no device. Select the triangle beside a device to see its light entities. To skip the browsing, type your light's name in the filter box above the list.

Each row has four buttons: N follows the level above, R reads, W reads and controls, and D denies ([more about them](guide:window.permissions#states)). Choose **W** on a light. **{{label:wizard.next}}** stays greyed out until something is set to W; R isn't enough for the test.

A grant on a device covers its entities. A grant on the whole light domain covers every light, including ones added later.

If Home Assistant has no lights, the step says so, and **{{label:wizard.showAllDevices}}** appears beside the message. It lists every domain, so you can grant another device instead. You can grant more later in the {{label:accessKeys.permissionsTree}}.
