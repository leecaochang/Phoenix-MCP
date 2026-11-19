---
{ "id": "wizard.accessKey", "title": "Naming the Access Key", "parent": "workspace.accessKeys", "order": 102, "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
The agent uses this access key to connect to Phoenix. Its name appears in the audit log and in approvals, so pick a name that says which agent uses it.

Use 3 to 32 letters, digits, underscores, or hyphens. Each key needs its own name. If you've run the wizard before, change the suggested one.

An expiry date ends the key automatically. Once the key exists, you can't change its expiry date. Select **{{label:wizard.createAccessKey}}** to create the key.

If you're connecting an external app, the key's value appears in the {{label:wizard.stepConnect}} step. With Agent Chat, Phoenix never shows it. To use the key in an app later, rotate it to get a value.
