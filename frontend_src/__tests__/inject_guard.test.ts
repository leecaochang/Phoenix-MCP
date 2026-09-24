import { afterEach, describe, expect, it, vi } from "vitest";
import { blockInjectSession, injectSessionBlocked, startInjectSession, subscribeInjectCompatibility } from "../inject/compatibility";

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).__phxMesaInjectCompatibility;
});

describe("injector session compatibility", () => {
  it("retains a failure across same-build loads and resets only for a newer build", () => {
    const changed = vi.fn();
    const unsubscribe = subscribeInjectCompatibility(changed);
    startInjectSession("1.0.1");
    blockInjectSession("1.0.1");
    expect(injectSessionBlocked()).toBe(true);
    startInjectSession("1.0.1");
    startInjectSession("1.0.0");
    expect(injectSessionBlocked()).toBe(true);
    expect(changed).toHaveBeenCalledTimes(2);
    startInjectSession("1.0.2");
    expect(injectSessionBlocked()).toBe(false);
    blockInjectSession("1.0.1");
    expect(injectSessionBlocked()).toBe(false);
    unsubscribe();
    blockInjectSession("1.0.2");
    expect(changed).toHaveBeenCalledTimes(3);
  });

});
