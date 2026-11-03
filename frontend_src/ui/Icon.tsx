/** Phoenix control icons, drawn from Lucide's outline set. */

import type { CSSProperties } from "react";
import type { LucideIcon } from "lucide-react";
import {
  Archive, ArrowDownLeft, ChartColumn, ArrowLeftRight, CheckCheck, FileClock, RotateCcw, CircleSlash, CircleX, ClockAlert, LoaderCircle, SquareCheckBig, Undo2, ArrowLeft, ArrowRight, ArrowUp, ArrowUpRight, Ban, BookOpen, Check, ChevronDown, ChevronRight, ChevronUp,
  CircleAlert, CircleCheck, CirclePause, Clock, Code, Columns2, Copy,
  Download, Ellipsis, Eraser, Eye, FileDiff, FileText, FunnelX, FilePenLine, GitBranch, GripHorizontal, GripVertical, History, Info, KeyRound, KeySquare,
  LayoutDashboard, Lightbulb, ListChecks, ListEnd, ListFilter, ListX, Maximize2, Menu, MessageSquarePlus, MessageSquareText, MessagesSquare, Minimize2, Minus, OctagonAlert, PanelBottom,
  PanelBottomClose, PanelBottomOpen, PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, PanelTopClose, PanelTopOpen, PanelsTopLeft, Paperclip, Pencil, PictureInPicture2, Plus, RotateCw, Rows2, Save, ScrollText, Search, SearchCheck,
  Settings, Shield, SlidersHorizontal, StepBack, StepForward, SquareArrowDownLeft, SquareArrowOutUpRight, Square, Star, Trash2, TriangleAlert, Upload,
  WandSparkles, X, Sun, SunMoon, Moon,
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
  grip: GripVertical,
  gripHorizontal: GripHorizontal,
  toolbarCloseTop: PanelTopClose,
  toolbarOpenTop: PanelTopOpen,
  toolbarCloseBottom: PanelBottomClose,
  toolbarOpenBottom: PanelBottomOpen,
  toolbarCloseLeft: PanelLeftClose,
  toolbarOpenLeft: PanelLeftOpen,
  toolbarCloseRight: PanelRightClose,
  toolbarOpenRight: PanelRightOpen,
  setupAgent: WandSparkles,
  archive: Archive,
  add: Plus,
  logDetails: PanelBottom,
  previous: ChevronUp,
  next: ChevronDown,
  keyActions: KeySquare,
  clearList: ListX,
  back: ArrowLeft,
  forward: ArrowRight,
  chatMenu: Menu,
  send: ArrowUp,
  stop: Square,
  sortUp: ChevronUp,
  sortDown: ChevronDown,
  filter: ListFilter,
  clearFilters: FunnelX,
  loadMore: ListEnd,
  processing: LoaderCircle,
  statusRejected: CircleX,
  statusExpired: ClockAlert,
  statusCancelled: CircleSlash,
  reject: X,
  undo: Undo2,
  selectMultiple: SquareCheckBig,
  approveSelected: CheckCheck,
  requestDetails: Info,
  restoreVersion: RotateCcw,
  itemHistory: FileClock,
  hint: MessageSquareText,
  hintAdd: MessageSquarePlus,
  save: Save,
  suggestions: Lightbulb,
  search: Search,
  recordingSettings: SlidersHorizontal,
  diff: FileDiff,
  preview: Eye,
  before: StepBack,
  after: StepForward,
  lineView: FileText,
  switchPreset: ArrowLeftRight,
  clearPermissions: Eraser,
  statistics: ChartColumn,
  themeLight: Sun,
  themeAuto: SunMoon,
  themeDark: Moon,
} satisfies Record<string, LucideIcon>;

export type IconName = keyof typeof ICONS;

/** A decorative glyph: the control around it carries the accessible name. Its `phx-icon-<name>` class lets a theme single it out. */
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
      className={className ? `phx-icon-${name} ${className}` : `phx-icon-${name}`}
      style={style}
      aria-hidden="true"
      focusable="false"
    />
  );
}
