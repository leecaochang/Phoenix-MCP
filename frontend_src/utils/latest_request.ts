/** Discard a response that a newer request has already superseded. */

import { useCallback, useRef } from "react";

/**
 * Return a function that starts a request and hands back an "is this still the
 * newest request?" check, one hook per view.
 *
 * A list is loaded from several places with no ordering between them (mount,
 * refresh, poll tick, HA event, filter change, "Load more"), so a slow request
 * started under the OLD filter can land after a newer one and put the old rows
 * back. Last STARTED wins, not last finished: a newer request is newer intent.
 * A superseded "Load more" is dropped rather than appended, because an
 * offset-zero refresh has already replaced the window its offset was computed
 * against. Start before awaiting, check before touching state:
 *
 *   const isLatest = useLatestRequest();
 *   // in the loader:
 *   const current = isLatest();
 *   const resp = await api.something();
 *   if (!current()) return;
 */
export function useLatestRequest(): () => () => boolean {
  const generation = useRef(0);
  return useCallback(() => {
    const mine = ++generation.current;
    return () => mine === generation.current;
  }, []);
}
