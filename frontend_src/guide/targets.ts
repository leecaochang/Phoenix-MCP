/**
 * Guide sections for controls drawn from a list (user, 2026-11-01): each
 * persona and each capability has its own section on its window's page, its
 * anchor the key in dashes, so selecting one in the panel opens its section.
 * The Guide's contract test checks that every persona and capability has one.
 */

/** `read_only` is `window.persona#read-only`. */
export const personaGuide = (persona: string): string => `window.persona#${persona.replace(/_/g, "-")}`;

/** `cap_config_read` is `window.capabilities#config-read`. */
export const capabilityGuide = (cap: string): string => `window.capabilities#${cap.replace(/^cap_/, "").replace(/_/g, "-")}`;
