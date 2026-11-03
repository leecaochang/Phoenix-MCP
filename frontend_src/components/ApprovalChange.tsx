/**
 * What an approval request would change, shown in Review: a before and after
 * comparison for code and configuration, an inert dashboard or Energy preview
 * where the saved data allows one, the service call with its MESA outcome, or
 * the system action with what it removes.
 */

import React, { useEffect, useState } from "react";
import type { ApprovalRecord } from "../types";
import { BeforeAfter, RemovedPane } from "./DiffView";
import { collectPreviewViews, DashboardPreview, rememberPreviewMode, SegmentedToggle, singleCardPreviewConfig, storedPreviewMode, useHuiCardReady, wrapCardPreviewConfig } from "./DashboardPreview";
import { YamlView, toYaml } from "./YamlView";
import { diffSummary } from "../utils/approval_text";
import { ENERGY_SOURCE_LABEL_KEYS } from "../utils/approval_summary";
import { t } from "../i18n";

const CARD_OP_TOOLS = new Set(["add_dashboard_card", "edit_dashboard_card", "delete_dashboard_card"]);

/** True when a patch_dashboard record addresses a CARD, so the card preview applies.
 *
 * patch_dashboard can target anything in a layout (a view title, a badge), and
 * feeding a non-card to hui-card would show the approver a picture of something
 * other than what they are approving, which is worse than no picture. So the
 * preview is offered only when the path runs through a "cards" list; everything
 * else falls back to the text diff.
 */
function isCardPatchRecord(record: { tool_name: string; args?: Record<string, unknown> | null }): boolean {
  if (record.tool_name !== "patch_dashboard") return false;
  const path = record.args?.path;
  return Array.isArray(path) && path.includes("cards");
}

/** A patch whose op removes the value has only a Before side to show. */
function isRemovePatch(record: { tool_name: string; args?: Record<string, unknown> | null }): boolean {
  return record.tool_name === "patch_dashboard" && record.args?.op === "remove";
}

/** The proposed change itself: the exact code or configuration first, with an inert preview where one exists. */
export function ApprovalChange({ record }: {
  record: ApprovalRecord;
}) {
  const diff = record.diff || {};
  const kind = diff.kind || "system_action";
  const huiReady = useHuiCardReady();
  // Diff vs Preview is a remembered preference (storedPreviewMode), so an
  // operator who chose Preview once gets it on every later record too.
  const [mode, setModeState] = useState<"diff" | "preview">(() => storedPreviewMode());
  const setMode = (m: "diff" | "preview") => {
    rememberPreviewMode(m);
    setModeState(m);
  };
  // A card op previews Before (the card being removed/replaced) or After (the
  // card being added/that replaced it); a whole-layout write only ever has an
  // After to show (see below). Defaults to whichever side a delete has.
  const showsBeforeFirst = record.tool_name === "delete_dashboard_card" || isRemovePatch(record);
  const [side, setSide] = useState<"before" | "after">(showsBeforeFirst ? "before" : "after");
  useEffect(() => {
    setModeState(storedPreviewMode());
    setSide(showsBeforeFirst ? "before" : "after");
  }, [record.id, record.tool_name, showsBeforeFirst]);

  const isWholeLayout = record.tool_name === "set_dashboard_config";
  // Energy preferences have their own structured configuration preview.
  const energyPreview = (diff.preview as Record<string, unknown> | undefined)?.energy as
    { before?: unknown; after?: unknown } | undefined;
  const isCardOp = CARD_OP_TOOLS.has(record.tool_name) || isCardPatchRecord(record);
  // Whole-layout writes: only args.config (the full proposed layout) is
  // previewable. diff.before/after are truncated strings and the record
  // carries no untruncated current config, so only the After side exists.
  const wholeConfig = isWholeLayout ? (record.args?.config as unknown) : null;
  // Card ops: the After side comes from args.card, the full untruncated
  // object the approval carries for the executor (a single big card, e.g. a
  // multi-series chart, can exceed the diff string's truncation bound, and a
  // truncated diff.after does not parse). The Before side (the prior card on
  // edit/delete) only exists in diff.before, wrapped from its JSON;
  // unparseable disables just that side.
  const beforeCardConfig = isCardOp ? singleCardPreviewConfig(diff.before ?? null) : null;
  // patch_dashboard carries its card under args.value rather than args.card; both
  // are the full untruncated object the executor will apply.
  const afterCardConfig = isCardOp
    ? (wrapCardPreviewConfig(record.args?.card ?? record.args?.value)
       ?? singleCardPreviewConfig(diff.after ?? null))
    : null;
  const cardSideOk = {
    before: beforeCardConfig !== null && collectPreviewViews(beforeCardConfig) !== null,
    after: afterCardConfig !== null && collectPreviewViews(afterCardConfig) !== null,
  };
  const canPreview = !!energyPreview || (huiReady && (
    isWholeLayout ? collectPreviewViews(wholeConfig) !== null
    : isCardOp ? (cardSideOk.before || cardSideOk.after)
    : false
  ));
  // If the selected side is not actually previewable for this op (e.g. "after"
  // on a delete, which has none), fall back to whichever side is.
  const previewSide = cardSideOk[side] ? side : (cardSideOk.before ? "before" : "after");
  const previewConfig = isWholeLayout ? wholeConfig : (previewSide === "before" ? beforeCardConfig : afterCardConfig);

  if (kind === "yaml_diff" || kind === "config_diff") {
    const modeToggle = canPreview ? (
      <SegmentedToggle
        value={mode}
        onChange={setMode}
        ariaLabel={t("approvals.diffModeAria")}
        help="help.diffPreview"
        options={[{ value: "diff", label: t("common.diff") }, { value: "preview", label: t("common.preview") }]}
      />
    ) : undefined;
    if (!canPreview || mode !== "preview") {
      // Diff mode: the Diff|Preview switch rides in the diff's own toolbar,
      // right next to the side-by-side/stacked layout toggle.
      return <BeforeAfter before={diff.before ?? null} after={diff.after ?? null} toolbarExtra={modeToggle} />;
    }
    if (energyPreview) {
      return (
        <div className="approval-preview-pane">
          {modeToggle && <div className="approval-preview-toolbar">{modeToggle}</div>}
          <EnergyPreview energy={energyPreview as { before?: EnergyRows; after?: EnergyRows }} />
        </div>
      );
    }
    const showSideToggle = isCardOp && cardSideOk.before && cardSideOk.after;
    return (
      <div className="change-diff-wrap">
        <div className="change-diff-toolbar">
          {modeToggle}
        </div>
        {showSideToggle && (
          <div className="diff-mode-row">
            <SegmentedToggle
              value={previewSide}
              onChange={setSide}
              ariaLabel={t("approvals.diffSideAria")}
              help="help.previewSide"
              options={[{ value: "before", label: t("common.before") }, { value: "after", label: t("common.after") }]}
            />
          </div>
        )}
        <DashboardPreview key={previewSide} config={previewConfig} />
      </div>
    );
  }
  if (kind === "service_preview") {
    return <ServicePreview preview={diff.preview || {}} />;
  }
  return <SystemActionPreview summary={diffSummary(diff)} preview={diff.preview || {}} before={diff.before ?? null} />;
}

// Render a preview value for the review UI. Nested objects (e.g. service_data)
// are flattened to "key: value" pairs so they never show as "[object Object]".
function renderPreviewValue(v: unknown): string {
  if (v == null) return t("common.none");
  if (Array.isArray(v)) return v.length ? v.join(t("common.listSeparator")) : t("common.none");
  if (typeof v === "object") {
    const entries = Object.entries(v as Record<string, unknown>);
    if (entries.length === 0) return t("common.none");
    return entries
      .map(([k, val]) => `${k}: ${val !== null && typeof val === "object" ? JSON.stringify(val) : String(val)}`)
      .join(t("common.listSeparator"));
  }
  return String(v);
}

function ServicePreview({ preview }: { preview: Record<string, unknown> }) {
  const mesa = preview.mesa as Record<string, unknown> | undefined;
  return (
    <div>
      <div className="approval-detail-meta">
        {Object.entries(preview).filter(([k]) => k !== "mesa").map(([k, v]) => (
          <React.Fragment key={k}>
            <span className="stat-label">{k}</span>
            <span><code>{renderPreviewValue(v)}</code></span>
          </React.Fragment>
        ))}
      </div>
      {mesa && <MesaPreviewBlock mesa={mesa} />}
    </div>
  );
}

function MesaPreviewBlock({ mesa }: { mesa: Record<string, unknown> }) {
  const confirm = (mesa.confirm_entities as string[]) ?? [];
  const allowed = (mesa.allowed_entities as string[]) ?? [];
  const blocked = (mesa.blocked as Array<{ entity_id: string; rule: string }>) ?? [];
  const warnings = (mesa.warnings as string[]) ?? [];
  return (
    <div className="mesa-preview-block">
      <div className="approval-detail-meta">
        <span className="stat-label">{t("approvals.mesaConfirm")}</span>
        <span><code>{confirm.length ? confirm.join(t("common.listSeparator")) : t("common.none")}</code></span>
        <span className="stat-label">{t("approvals.mesaAllowed")}</span>
        <span><code>{allowed.length ? allowed.join(t("common.listSeparator")) : t("common.none")}</code></span>
        {blocked.length > 0 && (
          <>
            <span className="stat-label">{t("approvals.mesaBlocked")}</span>
            <span><code>{blocked.map((b) => `${b.entity_id} (${b.rule})`).join(t("common.listSeparator"))}</code></span>
          </>
        )}
      </div>
      {warnings.length > 0 && (
        <ul className="mesa-preview-warnings">
          {warnings.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      )}
    </div>
  );
}

// Show proposed Energy preferences directly, including the changed fields.
type EnergySourceRow = { type?: string; name?: string; meters?: Array<[string, string]> };
type EnergyDeviceRow = { name?: string; statistic?: string };
type EnergyRows = { sources?: EnergySourceRow[]; devices?: EnergyDeviceRow[] };

// A raw slug interpolated into the UI stays English in every locale, so the type
// resolves through the catalog like every other server-side enum in this panel.
type EnergyRow = { key: string; state: "added" | "removed" | "changed" | "same"; title: string; line: string };

/** Pair before/after rows into one ordered, diffed list.
 *
 * Keyed by STATISTIC for devices and by TYPE for sources, never by display name:
 * a rename would otherwise read as an unrelated removal plus addition. Removed
 * keys keep their original position so old and new values render adjacent.
 */
function diffEnergyRows<T>(
  before: T[], after: T[], key: (r: T) => string, title: (r: T) => string, line: (r: T) => string,
): EnergyRow[] {
  const keys: string[] = [];
  for (const r of [...before, ...after]) if (!keys.includes(key(r))) keys.push(key(r));
  const out: EnergyRow[] = [];
  for (const k of keys) {
    const b = before.find((r) => key(r) === k);
    const a = after.find((r) => key(r) === k);
    if (b && !a) out.push({ key: k, state: "removed", title: title(b), line: line(b) });
    else if (a && !b) out.push({ key: k, state: "added", title: title(a), line: line(a) });
    else if (a && b && (title(a) !== title(b) || line(a) !== line(b))) {
      // Shown twice, old then new, so the operator reads the substitution rather
      // than having to infer it from one highlighted row.
      out.push({ key: `${k}-b`, state: "changed", title: title(b), line: line(b) });
      out.push({ key: `${k}-a`, state: "added", title: title(a), line: line(a) });
    } else if (a) out.push({ key: k, state: "same", title: title(a), line: line(a) });
  }
  return out;
}

function EnergyPreviewSection({ label, rows }: { label: string; rows: EnergyRow[] }) {
  return (
    <div className="energy-preview-section">
      <div className="energy-preview-heading">{label}</div>
      {rows.length === 0 && <div className="energy-preview-empty">{t("common.none")}</div>}
      {rows.map((r) => (
        <div key={r.key} className={`energy-preview-row is-${r.state}`}>
          {/* The row's tint and mark show what changed; this says it. */}
          {r.state !== "same" && <span className="sr-only">{t(r.state === "added" ? "changes.added" : "changes.removed")}: </span>}
          <span className="energy-preview-name">{r.title}</span>
          <code className="energy-preview-stat">{r.line}</code>
        </div>
      ))}
    </div>
  );
}

export function EnergyPreview({ energy }: { energy: { before?: EnergyRows; after?: EnergyRows } }) {
  const before = energy.before ?? {};
  const after = energy.after ?? {};
  const sourceTitle = (r: EnergySourceRow) =>
    r.name || (r.type ? t(ENERGY_SOURCE_LABEL_KEYS[r.type] ?? "") || r.type : "");
  const sourceLine = (r: EnergySourceRow) =>
    (r.meters ?? []).map(([, stat]) => stat).join(t("common.listSeparator"));
  return (
    <div className="energy-preview">
      <EnergyPreviewSection
        label={t("approvals.energySources")}
        rows={diffEnergyRows(before.sources ?? [], after.sources ?? [],
          (r) => r.type ?? "", sourceTitle, sourceLine)}
      />
      <EnergyPreviewSection
        label={t("approvals.energyDevices")}
        rows={diffEnergyRows(before.devices ?? [], after.devices ?? [],
          (r) => r.statistic ?? "", (r) => r.name || r.statistic || "", (r) => r.statistic ?? "")}
      />
    </div>
  );
}

function SystemActionPreview({ summary, preview, before }: { summary?: string; preview: Record<string, unknown>; before?: string | null }) {
  return (
    <div>
      {summary && <p className="approval-summary-line"><strong>{summary}</strong></p>}
      {before && (
        <div className="yaml-diff-col">
          <div className="approval-diff-label">{t("approvals.diffRemoving")}</div>
          <RemovedPane value={before} />
        </div>
      )}
      {Object.keys(preview).length > 0 && (
        <YamlView value={toYaml(preview)} />
      )}
    </div>
  );
}
