/** Whether the window a view is drawn in can be seen; background polling pauses when it cannot. */

import { createContext, useContext } from "react";

/** True outside the workspace shell, so a view rendered on its own behaves as before. */
export const WindowVisibleContext = createContext(true);

export function useWindowVisible(): boolean {
  return useContext(WindowVisibleContext);
}
