/**
 * Whether the page Home Assistant shows is a dashboard.
 *
 * The injected bundles load on every page. They harvest the card catalog only on
 * a dashboard, where Home Assistant has already imported the dashboard resources,
 * so a configuration page never runs a resource it would not load itself. Kept
 * free of imports so the always-loaded Agent Chat bootstrap stays small.
 */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function onDashboardPage(hass: any, path: string = window.location.pathname): boolean {
  const urlPath = path.split("/").filter(Boolean)[0];
  if (!urlPath) return false;
  return hass?.panels?.[urlPath]?.component_name === "lovelace";
}
