/**
 * A workspace's display choices, saved with its arrangement: the windows
 * whose buttons always show their labels whatever the toolbar's display mode,
 * and, in My Workspace, whether the workspace scrolls vertically or fits its
 * windows to the screen, with the heights the windows had before they were
 * fitted, so turning scrolling back on restores them.
 */

export interface DisplaySnapshot {
  /** Windows that always show icons and text. */
  labels?: string[];
  /** The workspace scrolls vertically (My Workspace). */
  scrolls?: boolean;
  /** While it fits: each window's height from before it was fitted. */
  scrollHeights?: Record<string, number>;
}

export class DisplayStore {
  private labeled = new Set<string>();
  private scrolling = false;
  private heights: Record<string, number> = {};
  private listeners = new Set<() => void>();
  private version = 0;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  getVersion = (): number => this.version;

  /** Whether a window always shows icons and text. */
  hasLabels = (id: string): boolean => this.labeled.has(id);

  setLabels(id: string, on: boolean): void {
    if (on === this.labeled.has(id)) return;
    if (on) this.labeled.add(id); else this.labeled.delete(id);
    this.changed();
  }

  get scrolls(): boolean { return this.scrolling; }

  /** @internal The controller also changes how the workspace lays its windows out. */
  setScrolls(on: boolean): void {
    if (on === this.scrolling) return;
    this.scrolling = on;
    this.changed();
  }

  /** Each window's height from before the workspace was fitted to the screen. */
  get scrollHeights(): Readonly<Record<string, number>> { return this.heights; }

  /** @internal Set when the workspace is fitted, cleared when it scrolls again. */
  setScrollHeights(heights: Record<string, number>): void {
    this.heights = { ...heights };
  }

  /** The choices to save, for the windows in `saved`. */
  snapshot(saved: ReadonlySet<string>): DisplaySnapshot {
    const labels = [...this.labeled].filter((id) => saved.has(id));
    const heights = Object.entries(this.heights).filter(([id]) => saved.has(id));
    return {
      ...(labels.length ? { labels } : {}),
      ...(this.scrolling ? { scrolls: true } : {}),
      ...(heights.length ? { scrollHeights: Object.fromEntries(heights) } : {}),
    };
  }

  /** Load saved choices; `known` drops windows that no longer exist. */
  restore(state: DisplaySnapshot | undefined, known: (id: string) => boolean): void {
    this.labeled = new Set((Array.isArray(state?.labels) ? state.labels : []).filter((id) => typeof id === "string" && known(id)));
    this.scrolling = state?.scrolls === true;
    this.heights = Object.fromEntries(Object.entries(state?.scrollHeights ?? {})
      .filter(([id, h]) => known(id) && typeof h === "number" && Number.isFinite(h) && h > 0 && h < 100000));
    this.changed();
  }

  private changed(): void {
    this.version++;
    for (const listener of this.listeners) listener();
  }
}
