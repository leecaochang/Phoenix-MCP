/**
 * The phone stack's arrangement for one workspace: the order the user put the
 * windows in, which are folded, which are closed, and which always show icons
 * and text. A workspace without one shows its default: every window in the
 * workspace's own order, folded where the workspace folds it, and closed where
 * the desktop arrangement leaves it closed. Saved in the mobile record, so a
 * phone keeps its own arrangement apart from the desktop's.
 */

export interface PhoneLayout {
  /** Window ids in the order the user arranged them; windows not listed keep their default place. */
  order?: string[];
  folded?: string[];
  closed?: string[];
  /** Windows whose buttons always show icons and text. */
  labels?: string[];
  /** In My Workspace's saved default only: the windows it held. */
  windows?: string[];
}

/** The most ids one list keeps; more than every window there is, so only damage is cut off. */
const MAX_IDS = 64;

const ids = (raw: unknown): string[] | undefined => {
  if (!Array.isArray(raw)) return undefined;
  const out = raw.filter((id): id is string => typeof id === "string" && /^[A-Za-z][A-Za-z0-9]{0,40}$/.test(id));
  return [...new Set(out)].slice(0, MAX_IDS);
};

/** A saved phone arrangement, with anything unreadable left out. */
export function readPhoneLayout(raw: unknown): PhoneLayout {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<keyof PhoneLayout, unknown>;
  const out: PhoneLayout = {};
  for (const key of ["order", "folded", "closed", "labels", "windows"] as const) {
    const list = ids(r[key]);
    if (list) out[key] = list;
  }
  return out;
}

/**
 * `present` in the user's order: the windows the saved order lists come in
 * that order, and any other window follows the window it comes after by
 * default, so a window that appears later (Presets, say) takes its usual place.
 */
export function phoneOrder(present: readonly string[], order: readonly string[] | undefined): string[] {
  if (!order?.length) return [...present];
  const out = order.filter((id) => present.includes(id));
  present.forEach((id, i) => {
    if (out.includes(id)) return;
    let at = 0;
    for (let j = i - 1; j >= 0; j--) {
      const k = out.indexOf(present[j]);
      if (k >= 0) { at = k + 1; break; }
    }
    out.splice(at, 0, id);
  });
  return out;
}

/**
 * The whole order `all` after moving `id` past its neighbor above or below
 * among `shown`, the windows on screen; null when it has none that way.
 * Closed windows keep their places.
 */
export function movedOrder(all: readonly string[], shown: readonly string[], id: string, by: -1 | 1): string[] | null {
  const at = shown.indexOf(id);
  const neighbor = shown[at + by];
  if (at < 0 || neighbor === undefined) return null;
  const out = [...all];
  const i = out.indexOf(id);
  const j = out.indexOf(neighbor);
  if (i < 0 || j < 0) return null;
  [out[i], out[j]] = [out[j], out[i]];
  return out;
}

/** `list` with `id` in it or out of it. */
export function withMember(list: readonly string[] | undefined, id: string, on: boolean): string[] {
  const rest = (list ?? []).filter((w) => w !== id);
  return on ? [...rest, id] : rest;
}
