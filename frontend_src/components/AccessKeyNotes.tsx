import { useEffect, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import type { AccessKeyRecord } from "../types";
import { BusyButton } from "./BusyButton";

// Mirrors ACCESS_KEY_NOTES_MAX_LENGTH in const.py. The server counts code
// points, so the counter does too (an emoji is one character, not two).
export const ACCESS_KEY_NOTES_MAX_LENGTH = 2000;

export function notesLength(text: string): number {
  return [...text].length;
}

// Administrator notes: advisory prose added to the instructions of every agent
// using this access key. Saved explicitly, never on each keystroke, because a
// save reaches connected clients.
export function AccessKeyNotes({ accessKey, onUpdate }: { accessKey: AccessKeyRecord; onUpdate: (k: AccessKeyRecord) => void }) {
  const saved = accessKey.notes ?? "";
  const [draft, setDraft] = useState(saved);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Follow a change made elsewhere unless the draft holds unsaved edits.
  const [base, setBase] = useState(saved);
  useEffect(() => {
    if (saved !== base) {
      if (draft === base) setDraft(saved);
      setBase(saved);
    }
  }, [saved, base, draft]);

  const length = notesLength(draft);
  const tooLong = length > ACCESS_KEY_NOTES_MAX_LENGTH;
  const dirty = draft !== saved;

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const updated = await api.patchAccessKey(accessKey.id, { notes: draft.trim() ? draft : null });
      setDraft(updated.notes ?? "");
      setBase(updated.notes ?? "");
      onUpdate(updated);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("accessKeys.updateFailed"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="access-key-notes">
      {error && <div className="banner banner-error mb-8">{error}</div>}
      <p className="access-key-notes-help">{t("accessKeys.notesHelp")}</p>
      <p className="access-key-notes-help">{t("accessKeys.notesCaution")}</p>
      <textarea
        className={`input access-key-notes-input${tooLong ? " error" : ""}`}
        aria-label={t("accessKeys.notesLabel")}
        aria-invalid={tooLong || undefined}
        placeholder={t("accessKeys.notesPlaceholder")}
        rows={6}
        value={draft}
        readOnly={saving}
        onChange={(e) => setDraft(e.target.value)}
      />
      <div className="access-key-notes-footer">
        <span className={`access-key-notes-count${tooLong ? " field-error" : ""}`} aria-live="polite">
          {tooLong
            ? t("accessKeys.notesTooLong", { max: ACCESS_KEY_NOTES_MAX_LENGTH })
            : t("accessKeys.notesCount", { count: length, max: ACCESS_KEY_NOTES_MAX_LENGTH })}
        </span>
        {dirty && (
          <span className="access-key-notes-actions">
            <BusyButton type="button" className="btn btn-text btn-sm" busy={saving} onClick={() => setDraft(saved)}>
              {t("accessKeys.notesDiscard")}
            </BusyButton>
            <BusyButton type="button" className="btn btn-primary btn-sm" busy={saving} disabled={tooLong} onClick={() => void save()}>
              {saving ? t("common.saving") : t("common.save")}
            </BusyButton>
          </span>
        )}
      </div>
    </div>
  );
}
