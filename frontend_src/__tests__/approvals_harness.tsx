/**
 * Renders the Approvals workspace as the shell does: the three windows around
 * their shared state, with the Show choice held like panel data holds it.
 * Outside the shell the windows show their toolbar tools inline, so tests
 * press them by name.
 */

import { useCallback, useRef, useState, type ComponentProps } from "react";
import { render } from "@testing-library/react";
import { ApprovalsProvider, type ApprovalsScope } from "../shell/approvals";
import { ApprovalsListWindow, RequestDetailsWindow, ReviewWindow } from "../views/ApprovalsView";

export type HarnessProps = Omit<ComponentProps<typeof ApprovalsProvider>, "children" | "scope" | "onScopeChange"> & {
  scope?: ApprovalsScope;
  onScopeChange?: (scope: ApprovalsScope) => void;
};

function Harness({ scope: initial = "pending", onScopeChange, ...props }: HarnessProps) {
  const [scope, setScope] = useState<ApprovalsScope>(initial);
  const listener = useRef(onScopeChange);
  listener.current = onScopeChange;
  // Stable, like panel data's setter: the deep-link fetch depends on it.
  const change = useCallback((next: ApprovalsScope) => {
    setScope(next);
    listener.current?.(next);
  }, []);
  return (
    <ApprovalsProvider scope={scope} onScopeChange={change} {...props}>
      <section aria-label="Approvals window"><ApprovalsListWindow /></section>
      <section aria-label="Review window"><ReviewWindow /></section>
      <section aria-label="Request Details window"><RequestDetailsWindow /></section>
    </ApprovalsProvider>
  );
}

export function renderApprovals(props: HarnessProps = {}) {
  const utils = render(<Harness {...props} />);
  return { ...utils, update: (next: Partial<HarnessProps>) => utils.rerender(<Harness {...props} {...next} />) };
}

/** The list's rows, in their shown order. */
export const listRows = (): HTMLElement[] => [...document.querySelectorAll<HTMLElement>('section[aria-label="Approvals window"] tbody tr[data-row]')];

/** The tick boxes of the list's rows, in their shown order. */
export const pickBoxes = (): HTMLInputElement[] => [...document.querySelectorAll<HTMLInputElement>("tbody td.phx-table-pick input")];

/** The Review window's element. */
export const reviewWindow = (): HTMLElement => document.querySelector<HTMLElement>('section[aria-label="Review window"]')!;
