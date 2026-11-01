---
{ "id": "wizard.access", "title": "Granting Access to One Light", "parent": "workspace.accessKeys", "order": 103 }
---
A new key can't reach any device until you grant it something. Grant one light you can see, so the test shows everything working.

Expand the light group and choose **W** on a light, for read and control. **{{label:wizard.next}}** stays greyed out until something is set to W; R isn't enough for the test.

A grant on a device covers its entities. A grant on the whole light domain covers every light, including ones added later.

If this Home Assistant has no lights, **{{label:wizard.showAllDevices}}** lists every domain. You can grant more later in the {{label:accessKeys.permissionsTree}}.
