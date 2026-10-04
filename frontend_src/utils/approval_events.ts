/**
 * One subscriber for Phoenix approval events on the Home Assistant bus.
 *
 * An approval is shown in three places at once: the Approvals tab, the Agent
 * Chat bubble, and an HA persistent notification. Agent Chat has no `hass` of
 * its own and otherwise updates only from its SSE stream, which rides the
 * agent's turn resuming, so a Reject in the panel would leave the chat bubble
 * offering Approve and Reject until the model answered. The backend refuses the
 * second action either way (the store-lock claim answers 409); this keeps the
 * operator from being shown a control that cannot work.
 *
 * The panel and the injected Agent Chat window are SEPARATE bundles sharing no
 * module state, so both use this module rather than separate copies.
 *
 * `hass` is resolved from the top-level `<home-assistant>` element when the
 * caller has none. A connection without `subscribeEvents` yields no subscription
 * and never throws; callers keep their existing polling.
 */

/** The two events this module understands (`<domain>_approval_*` on the HA bus). */
const RESOLVED_EVENT = "phoenix_mcp_approval_resolved";
const CLAIMED_EVENT = "phoenix_mcp_approval_claimed";

type EventConnection = {
  subscribeEvents?: (cb: (ev: unknown) => void, event: string) => Promise<() => void>;
};

export interface ApprovalEventHandlers {
  /** An approval reached a terminal state (approved, rejected, failed, cancelled or expired). */
  onResolved?: (approvalId: string, status: string) => void;
  /**
   * An approval was claimed for execution, or released back to pending.
   *
   * Claimed: an admin's Approve is running its saved action right now, so no
   * surface should still offer Approve or Reject. Released: execution failed and
   * the approval is pending and actionable again, so this is a pair and not a
   * second kind of "resolved".
   */
  onClaimChanged?: (approvalId: string, claimed: boolean) => void;
}

function resolveConnection(hass: unknown): EventConnection | null {
  const source = hass
    ?? (document.querySelector("home-assistant") as { hass?: unknown } | null)?.hass;
  const conn = (source as { connection?: EventConnection } | null)?.connection;
  return conn?.subscribeEvents ? conn : null;
}

function eventData(ev: unknown): Record<string, unknown> {
  const data = (ev as { data?: unknown } | null)?.data;
  return (data && typeof data === "object") ? data as Record<string, unknown> : {};
}

/**
 * Subscribe to approval resolution and claim events. Returns an unsubscribe.
 *
 * Safe to call when the connection is unavailable: the returned function is then
 * a no-op, and nothing throws, so a caller never has to feature-detect.
 */
export function subscribeApprovalEvents(
  hass: unknown,
  handlers: ApprovalEventHandlers,
): () => void {
  const conn = resolveConnection(hass);
  if (!conn?.subscribeEvents) return () => { /* no connection: caller keeps polling */ };

  let cancelled = false;
  const unsubs: Array<() => void> = [];

  const subscribe = (event: string, handle: (data: Record<string, unknown>) => void) => {
    conn.subscribeEvents!((ev) => handle(eventData(ev)), event)
      .then((unsub) => { if (cancelled) unsub(); else unsubs.push(unsub); })
      .catch(() => { /* subscription unavailable: the caller's polling covers it */ });
  };

  if (handlers.onResolved) {
    subscribe(RESOLVED_EVENT, (data) => {
      const id = data.approval_id;
      if (typeof id === "string") {
        handlers.onResolved!(id, typeof data.status === "string" ? data.status : "");
      }
    });
  }
  if (handlers.onClaimChanged) {
    subscribe(CLAIMED_EVENT, (data) => {
      const id = data.approval_id;
      // Absent or wrong-shaped `claimed` is read as claimed, the safe direction:
      // it stops offering an action rather than offering one that cannot work.
      if (typeof id === "string") handlers.onClaimChanged!(id, data.claimed !== false);
    });
  }

  return () => { cancelled = true; unsubs.forEach((u) => u()); };
}
