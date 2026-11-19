---
{ "id": "wizard.test", "title": "Testing the Connection", "parent": "workspace.accessKeys", "order": 107, "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
Send the suggested prompt from your app. As soon as Phoenix sees any request from the app, **{{label:wizard.next}}** becomes available. Often the app's own startup request is enough.

If nothing arrives, check that the app has loaded its new configuration. Some apps need a restart. Also check that the address and key match the {{label:wizard.stepConnect}} step.

A {{label:personas.home_companion.label}} key answers the app with the {{label:settings.voiceCard}}'s account. If no account is selected yet, this step says so. The app connects, but its requests fail until you select one.
