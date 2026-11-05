/**
 * Home Assistant's option to close the connection of a page left hidden for
 * some minutes also aborts the page's requests and removes the panel. A running
 * Agent Chat turn is one such request, and losing it cancels the turn and any
 * approval it holds, so the option is set aside for as long as a turn runs.
 *
 * The event that sets it is the one the profile page's own switch sends, and
 * Home Assistant stores the value, so the release also runs on pagehide: a
 * closed tab must not leave the operator's choice changed. Home Assistant reads
 * the option when a page becomes hidden, which is why nothing is left to undo
 * when a turn ends on a hidden page. A crash runs no pagehide, so a hold also
 * keeps a timestamp beating in this browser, and the next page to load gives the
 * option back once the beat has stopped.
 */

type HomeAssistantElement = HTMLElement & { hass?: { suspendWhenHidden?: boolean } };

const HOLD_STORAGE_KEY = "phoenix_mcp_suspend_hold";
const HOLD_BEAT_MS = 30_000;
// A hidden page's timers can slow to one a minute.
const HOLD_STALE_MS = 180_000;

function writeHold(): void {
  try { localStorage.setItem(HOLD_STORAGE_KEY, String(Date.now())); } catch { /* storage blocked: skip */ }
}

function clearHold(): void {
  try { localStorage.removeItem(HOLD_STORAGE_KEY); } catch { /* storage blocked: skip */ }
}

function setSuspend(app: HomeAssistantElement, suspend: boolean): void {
  app.dispatchEvent(new CustomEvent("hass-suspend-when-hidden", { detail: { suspend } }));
}

/** Give the option back after a page that held it stopped without releasing. */
export function releaseAbandonedHold(): void {
  let held: number;
  try {
    held = Number(localStorage.getItem(HOLD_STORAGE_KEY));
  } catch {
    return;
  }
  if (!held) return;
  const age = Date.now() - held;
  if (age < HOLD_STALE_MS) {
    window.setTimeout(releaseAbandonedHold, HOLD_STALE_MS - age);
    return;
  }
  clearHold();
  const app = document.querySelector<HomeAssistantElement>("home-assistant");
  if (app?.hass?.suspendWhenHidden === false) setSuspend(app, true);
}

/** Hold the page against suspension; the returned function ends the hold. */
export function keepPageAwakeWhileHidden(): () => void {
  const app = document.querySelector<HomeAssistantElement>("home-assistant");
  if (!app?.hass || app.hass.suspendWhenHidden === false) return () => {};
  setSuspend(app, false);
  writeHold();
  const beat = window.setInterval(writeHold, HOLD_BEAT_MS);
  const release = () => {
    window.removeEventListener("pagehide", release);
    window.clearInterval(beat);
    clearHold();
    if (app.hass?.suspendWhenHidden === false) setSuspend(app, true);
  };
  window.addEventListener("pagehide", release);
  return release;
}
