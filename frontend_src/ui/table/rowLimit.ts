/**
 * Long lists in a phone window show a few rows at a time:
 * on a phone every window is as tall as its content, so a page of 25 to 100
 * rows pushed the windows below it far down the stack. The phone stack
 * provides the step; a list asks for its limit and grows it with Load More,
 * which shows rows already loaded before it fetches more. Outside the phone
 * stack a list has no limit unless it asks for a page size of its own.
 */

import { createContext, useCallback, useContext, useState } from "react";

/** How many more rows a phone list shows each time. */
export const PHONE_ROWS = 10;

/** The rows a list shows at a time in this window; null for no limit. */
export const RowLimitContext = createContext<number | null>(null);

export interface RowLimit {
  /** The most rows to show now; null for all of them. */
  limit: number | null;
  /** How many Load More adds; null outside a phone window. */
  step: number | null;
  /** Show one step more. */
  more: () => void;
  /** Go back to the first step, as when a filter changes what the list holds. */
  reset: () => void;
  /** Whether rows already loaded are still hidden: Load More shows those before fetching. */
  hidden: (loaded: number) => boolean;
}

/** A phone window's step applies first; `pageSize` is the step everywhere else, null for no limit. */
export function useRowLimit(pageSize: number | null = null): RowLimit {
  const step = useContext(RowLimitContext) ?? pageSize;
  const [steps, setSteps] = useState(1);
  const more = useCallback(() => setSteps((n) => n + 1), []);
  const reset = useCallback(() => setSteps(1), []);
  const limit = step === null ? null : step * steps;
  return { limit, step, more, reset, hidden: (loaded) => limit !== null && loaded > limit };
}

/** How many of `loaded` rows a list with `limit` shows. */
export const shownCount = (loaded: number, limit: number | null): number => (limit === null ? loaded : Math.min(loaded, limit));
