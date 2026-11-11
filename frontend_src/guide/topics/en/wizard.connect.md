---
{ "id": "wizard.connect", "title": "Connecting an External App", "parent": "workspace.accessKeys", "order": 106, "covers": ["frontend_src/views/OnboardingWizard.tsx"] }
---
Choose your app to get the exact command or configuration, with this key's secret value filled in. Copy it now: Phoenix only stores a hash of the key, and can't show the value again. If you lose it, rotate the key to get a new one.

For ChatGPT, Claude or their phone apps, choose **{{label:wizard.methodOAuth}}**. Those apps sign in with OAuth: turn on **{{label:oauth.switchLabel}}** there, then follow your app's steps. Nothing secret is copied.

**{{label:wizard.testConnection}}** waits for the app's first request. **{{label:wizard.connectLater}}** finishes now and leaves the test for later.

This step watches the key while you set up the app. When an app signs in with this key, or an agent sends its first request, Phoenix says so and **{{label:wizard.next}}** skips the test. When the approval dialog lists more than one key, choose this one; the key whose sign-in was opened last is listed first.
