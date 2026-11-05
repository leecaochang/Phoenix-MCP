/**
 * Home Assistant's own handler for this event stores the value, so the fixture
 * does the same to it: each hold must leave the operator's choice as it found it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { keepPageAwakeWhileHidden, releaseAbandonedHold } from "../utils/page_suspend";

type App = HTMLElement & { hass: { suspendWhenHidden?: boolean } };
let app: App;

function mountHomeAssistant(suspendWhenHidden?: boolean): void {
  app = document.createElement("home-assistant") as App;
  app.hass = { suspendWhenHidden };
  app.addEventListener("hass-suspend-when-hidden", (event) => {
    app.hass.suspendWhenHidden = (event as CustomEvent<{ suspend: boolean }>).detail.suspend;
  });
  document.body.appendChild(app);
}

beforeEach(() => mountHomeAssistant(true));
afterEach(() => { app.remove(); localStorage.clear(); vi.useRealTimers(); });

describe("keeping the page awake for a running turn", () => {
  it("sets the option aside and restores it", () => {
    const release = keepPageAwakeWhileHidden();
    expect(app.hass.suspendWhenHidden).toBe(false);
    release();
    expect(app.hass.suspendWhenHidden).toBe(true);
  });

  it("restores it when the page goes away before the turn ends", () => {
    keepPageAwakeWhileHidden();
    window.dispatchEvent(new Event("pagehide"));
    expect(app.hass.suspendWhenHidden).toBe(true);
  });

  it("treats an unset option as on, as Home Assistant does", () => {
    app.remove();
    mountHomeAssistant(undefined);
    const release = keepPageAwakeWhileHidden();
    expect(app.hass.suspendWhenHidden).toBe(false);
    release();
    expect(app.hass.suspendWhenHidden).toBe(true);
  });

  it("leaves an operator who turned the option off as they chose", () => {
    app.remove();
    mountHomeAssistant(false);
    keepPageAwakeWhileHidden()();
    expect(app.hass.suspendWhenHidden).toBe(false);
  });

  it("does nothing outside Home Assistant", () => {
    app.remove();
    expect(() => keepPageAwakeWhileHidden()()).not.toThrow();
    mountHomeAssistant(true);
  });
});

describe("a page that stopped while holding the option", () => {
  const abandoned = () => {
    keepPageAwakeWhileHidden();
    expect(app.hass.suspendWhenHidden).toBe(false);
  };

  it("gets it back on a later load", () => {
    vi.useFakeTimers();
    abandoned();
    vi.clearAllTimers();
    vi.advanceTimersByTime(600_000);
    releaseAbandonedHold();
    expect(app.hass.suspendWhenHidden).toBe(true);
    expect(localStorage.getItem("phoenix_mcp_suspend_hold")).toBeNull();
  });

  it("gets it back after the beat runs out when the reload came straight away", () => {
    vi.useFakeTimers();
    abandoned();
    vi.clearAllTimers();
    releaseAbandonedHold();
    expect(app.hass.suspendWhenHidden).toBe(false);
    vi.advanceTimersByTime(180_000);
    expect(app.hass.suspendWhenHidden).toBe(true);
  });

  it("does not take a hold from a page that is still beating", () => {
    vi.useFakeTimers();
    const release = keepPageAwakeWhileHidden();
    vi.advanceTimersByTime(600_000);
    releaseAbandonedHold();
    expect(app.hass.suspendWhenHidden).toBe(false);
    release();
    expect(app.hass.suspendWhenHidden).toBe(true);
    expect(localStorage.getItem("phoenix_mcp_suspend_hold")).toBeNull();
  });

  it("changes nothing with no hold on record", () => {
    app.remove();
    mountHomeAssistant(false);
    releaseAbandonedHold();
    expect(app.hass.suspendWhenHidden).toBe(false);
  });
});
