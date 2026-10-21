/** Phoenix control icons, drawn from Lucide's outline set. */

import type { CSSProperties } from "react";
import type { LucideIcon } from "lucide-react";
import {
  ArrowDownLeft, ArrowUpRight, Ban, BookOpen, Check, ChevronDown, ChevronRight, CircleAlert, CircleCheck,
  CirclePause, Clock, Code, Columns2, Copy, Download, Ellipsis, ExternalLink, FilePenLine, GitBranch, History,
  Info, KeyRound, LayoutDashboard, ListChecks, Maximize2, MessagesSquare, Minimize2, Minus, OctagonAlert,
  PanelsTopLeft, Paperclip, Pencil, PictureInPicture2, RotateCw, Rows2, ScrollText, SearchCheck, Settings,
  Shield, SquareArrowDownLeft, SquareArrowOutUpRight, Star, Trash2, TriangleAlert, Upload, X,
} from "lucide-react";

// Named imports only, so each bundle carries just the glyphs it draws. Names
// describe the action or state, not the picture, so a glyph can change in one
// place. Entity and device icons supplied by Home Assistant are not drawn here.
const ICONS = {
  refresh: RotateCw,
  export: Upload,
  import: Download,
  edit: Pencil,
  delete: Trash2,
  probe: SearchCheck,
  externalLink: ExternalLink,
  popOut: SquareArrowOutUpRight,
  popIn: SquareArrowDownLeft,
  close: X,
  attach: Paperclip,
  settings: Settings,
  minimize: Minus,
  maximize: Maximize2,
  restore: Minimize2,
  copy: Copy,
  check: Check,
  outgoing: ArrowUpRight,
  incoming: ArrowDownLeft,
  error: CircleAlert,
  warning: TriangleAlert,
  denied: Ban,
  success: CircleCheck,
  pending: Clock,
  change: FilePenLine,
  info: Info,
  paused: CirclePause,
  routing: GitBranch,
  expand: ChevronDown,
  star: Star,
  code: Code,
  sideBySide: Columns2,
  stacked: Rows2,
  folded: ChevronRight,
  more: Ellipsis,
  float: PictureInPicture2,
  dock: PanelsTopLeft,
  accessKeys: KeyRound,
  approvals: ListChecks,
  changes: History,
  mesa: Shield,
  logs: ScrollText,
  agentChat: MessagesSquare,
  documentation: BookOpen,
  workspaceOptions: LayoutDashboard,
  killSwitch: OctagonAlert,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** A decorative glyph: the control around it carries the accessible name. */
export function Icon({ name, size = 16, className, style, filled = false }: {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
  filled?: boolean;
}) {
  const Glyph = ICONS[name];
  return (
    <Glyph
      size={size}
      strokeWidth={1.75}
      fill={filled ? "currentColor" : "none"}
      className={className}
      style={style}
      aria-hidden="true"
      focusable="false"
    />
  );
}
