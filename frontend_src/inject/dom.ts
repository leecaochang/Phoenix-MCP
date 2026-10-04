/* eslint-disable @typescript-eslint/no-explicit-any */
/**
 * Pure DOM helpers for the injector, separated so they can be unit-tested without
 * the entry module's load-time side effects.
 *
 * HA-version-specific row identities and insertion anchors live here. Verify
 * these adapters against upstream templates when HA changes its config pages.
 */

export const ENTITY_ID_RE = /^[a-z_]+\.[a-z0-9_]+$/;
export const BTN_CLASS = "phx-mesa-inject-btn";
// id of the per-table <style> injected to widen the icon column. Lives here so
// isSelfMutation can recognise it as one of the injector's own nodes.
export const WIDEN_STYLE_ID = "phx-mesa-col-widen";

// URL prefixes whose data-table rows are profilable entity rows. The
// /config/entities list covers every entity (including person.*), so it is the
// broadest surface; the per-domain pages are for in-context convenience.
export const ENTITY_PAGE_PREFIXES = [
  "/config/entities",
  "/config/automation",
  "/config/script",
  "/config/scene",
  "/config/helpers",
  "/config/person",
];

export function onEntityPage(path: string = window.location.pathname): boolean {
  return ENTITY_PAGE_PREFIXES.some((p) => path.startsWith(p));
}

// The three subpage surfaces, all keyed from the URL rather than the DOM, which
// makes them far less fragile than the data-table adapter below: a detail page's
// path is stable in a way its markup is not.
const AREA_DETAIL_RE = /^\/config\/areas\/area\/([^/]+)/;
const DEVICE_DETAIL_RE = /^\/config\/devices\/device\/([^/]+)/;
const INTEGRATION_DETAIL_RE = /^\/config\/integrations\/integration\/([^/]+)/;

/** The area_id from an area detail page URL, or null when not on one. */
export function areaIdFromPath(path: string = window.location.pathname): string | null {
  const m = path.match(AREA_DETAIL_RE);
  return m ? decodeURIComponent(m[1]) : null;
}

/** The device_id from a device detail page URL, or null when not on one. */
export function deviceIdFromPath(path: string = window.location.pathname): string | null {
  const m = path.match(DEVICE_DETAIL_RE);
  return m ? decodeURIComponent(m[1]) : null;
}

/** The integration domain from an integration detail page URL, or null. */
export function integrationDomainFromPath(path: string = window.location.pathname): string | null {
  const m = path.match(INTEGRATION_DETAIL_RE);
  return m ? decodeURIComponent(m[1]) : null;
}

/**
 * Which scope owns which detail page, in the order the scan tries them.
 *
 * Pure data, and here rather than in the entry module so it can be tested: a
 * scope pointed at another scope's extractor still type-checks, and the result
 * is a button that never appears or appears with the wrong level, which nothing
 * else would report.
 */
export const SUBPAGE_SURFACES: {
  scope: "device" | "area" | "integration";
  keyFromPath: (path?: string) => string | null;
  samplePath: string;
}[] = [
  { scope: "device", keyFromPath: deviceIdFromPath, samplePath: "/config/devices/device/abc123" },
  { scope: "area", keyFromPath: areaIdFromPath, samplePath: "/config/areas/area/kitchen" },
  { scope: "integration", keyFromPath: integrationDomainFromPath, samplePath: "/config/integrations/integration/hue" },
];

/**
 * Where to insert the control: in the same cell as the entity icon
 * (including ha-entity-id-icon), right after it. The icon is the anchor in
 * the leftmost entity/name column on every HA config table, so co-locating with
 * it keeps the control in the right column regardless of how the name renders.
 *
 * Returns null when there is no icon yet. HA renders row icons asynchronously
 * (virtualized rows), so injecting before the icon exists would drop the control
 * at the cell's start with the icon rendering after it. Refusing here makes a
 * later scan retry once the icon is in.
 */
export function nameInsertionPoint(row: HTMLElement): { parent: HTMLElement; before: Node | null } | null {
  const firstCell = row.querySelector<HTMLElement>('[role="cell"]:not(.mdc-data-table__cell--checkbox)');
  const icon = firstCell?.querySelector<HTMLElement>("ha-state-icon, ha-icon, ha-entity-id-icon, ha-domain-icon, ha-svg-icon");
  const iconCell = icon ? icon.closest<HTMLElement>('[role="cell"]') : null;
  if (icon && iconCell) {
    // Automation icons have a wrapper containing state badges. insertBefore
    // requires a direct child of the cell, not a sibling inside that wrapper.
    let anchor = icon;
    while (anchor.parentElement && anchor.parentElement !== iconCell) anchor = anchor.parentElement;
    return { parent: iconCell, before: anchor.nextSibling };
  }
  return null;
}

/** Resolve People list entries by HA's stable person id, never their name. */
export function personEntityId(
  row: HTMLElement,
  states: Record<string, { attributes?: { id?: unknown } }>,
): string | null {
  const badge = row.querySelector("ha-person-badge") as (HTMLElement & { person?: { id?: unknown } }) | null;
  const id = badge?.person?.id;
  if (typeof id !== "string" || !id) return null;
  const matches = Object.entries(states).filter(([key, state]) =>
    key.startsWith("person.") && ENTITY_ID_RE.test(key) && state.attributes?.id === id,
  );
  return matches.length === 1 ? matches[0][0] : null;
}

/** Is this node injected by this module (the +/MESA button or the column-width style)? */
function isOurNode(n: Node): boolean {
  const el = n as HTMLElement;
  return el.nodeType === 1 && (el.classList?.contains(BTN_CLASS) || el.id === WIDEN_STYLE_ID);
}

/**
 * True when every mutation in the batch was caused by the injector's own DOM,
 * so reacting to it would feed an endless rescan -> repaint -> rescan loop.
 *
 * The per-table observer fires on any childList change in the table's shadow
 * root, including the injector's own writes: inserting the button, swapping its
 * glyph in applyButtonState, and appending the width <style>. applyButtonState
 * skips redundant writes, but a real state change (right after an add or delete)
 * still mutates the DOM and would ping-pong with HA's own row re-render until
 * the page is reloaded. Filtering own mutations here breaks the injector's half
 * of that loop.
 *
 * A record that REMOVES the button is NOT self: HA reclaimed the cell, and a
 * later scan should re-add it.
 */
export function isSelfMutation(records: MutationRecord[]): boolean {
  for (const r of records) {
    const target = r.target as HTMLElement;
    // Glyph/text churn on the injected button (applyButtonState swapping + and check).
    if (target?.nodeType === 1 && target.classList?.contains(BTN_CLASS)) continue;
    // The button or width-style being ADDED by the injector. Pure additions only;
    // a removal means HA dropped the node, which must be reacted to.
    if (
      r.removedNodes.length === 0 &&
      r.addedNodes.length > 0 &&
      Array.from(r.addedNodes).every(isOurNode)
    ) {
      continue;
    }
    return false; // a foreign mutation: worth a rescan
  }
  return true;
}

/** Collect all elements matching `selector`, piercing open shadow roots. */
export function deepQueryAll(selector: string, root: Document | ShadowRoot | Element = document): Element[] {
  const out: Element[] = [];
  const stack: (Document | ShadowRoot | Element)[] = [root];
  while (stack.length) {
    const node = stack.pop()!;
    let descendants: Element[] = [];
    try {
      descendants = Array.from((node as Element).querySelectorAll("*"));
    } catch {
      continue;
    }
    for (const el of descendants) {
      if (el.matches?.(selector)) out.push(el);
      const sr = (el as HTMLElement).shadowRoot;
      if (sr) stack.push(sr);
    }
  }
  return out;
}

/**
 * Read an entity_id for one data-table row.
 *
 * HA's ha-data-table stores each row's configured id as a `.rowId` property on
 * the `role="row"` element (the same property its own row-click handler reads).
 * On the entity picker pages that id is the entity_id. The shape is validated, so
 * a non-entity table (numeric id, config-entry id, etc.) yields null and is skipped.
 * Returns null when no plausible entity_id is found, which makes the whole feature
 * self-disable on a DOM it does not recognise.
 */
export function extractEntityId(row: HTMLElement): string | null {
  const prop = (row as any).rowId;
  if (typeof prop === "string" && ENTITY_ID_RE.test(prop)) return prop;
  for (const attr of ["data-row-id", "data-id", "data-entity-id"]) {
    const v = row.getAttribute(attr);
    if (v && ENTITY_ID_RE.test(v)) return v;
  }
  return null;
}
