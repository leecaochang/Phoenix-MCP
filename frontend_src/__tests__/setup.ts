import "@testing-library/jest-dom/vitest";

import en from "../../custom_components/phoenix_mcp/catalogs/en.json";
import { primeTranslations } from "../i18n";

// The panel fetches its strings from Phoenix's own admin API at boot. Tests
// issue no requests, so prime the real catalog synchronously: assertions on
// English text keep working untouched, and a key that is missing from en.json
// fails loudly in whichever test renders it.
primeTranslations(en.panel);

import { beforeEach } from "vitest";
import { setAgentCliOwner } from "../utils/agentcli_state";
import { __setAwayGrace } from "../utils/connection";
beforeEach(() => setAgentCliOwner("test-admin"));
// A failed request counts as Home Assistant being away at once here; the grace that keeps a
// page reload from showing the notice has its own tests in reconnecting.test.tsx.
beforeEach(() => __setAwayGrace(0));

// jsdom has no ResizeObserver; Dockview and the workspace shell observe sizes.
// Nothing resizes in jsdom, so an observer that never fires is accurate.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
}
