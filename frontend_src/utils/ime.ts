/**
 * Input method composition. While Chinese, Japanese or Korean text is being
 * composed, Enter picks a candidate and Escape cancels the composition: neither
 * belongs to the field or dialog around it. Most browsers flag such a key press
 * with isComposing; Safari ends the composition first and reports key code 229.
 */

interface ComposingKey {
  isComposing: boolean;
  keyCode: number;
}

/** Whether this key press, a native or a React event, belongs to an input method. */
export function isImeKey(e: ComposingKey | { nativeEvent: ComposingKey }): boolean {
  const key = "nativeEvent" in e ? e.nativeEvent : e;
  return key.isComposing || key.keyCode === 229;
}
