import { useState } from "react";
import { ActionButton } from "../ui/ActionButton";
import { api } from "../api";
import { Modal } from "./Modal";
import { t } from "../i18n";

/** What a wipe deletes, chosen in Data Management before the typed confirmation. */
export interface WipeScopes {
  core: boolean;
  providers: boolean;
  mesa: boolean;
}

/** The two credential-bearing scopes start selected; MESA (authored safety policy, not
 *  credentials) does not, so a reset never silently discards hand-authored profiles. */
export const DEFAULT_WIPE_SCOPES: WipeScopes = { core: true, providers: true, mesa: false };

export const WIPE_SCOPES: { key: keyof WipeScopes; labelKey: string; helpKey: string }[] = [
  { key: "core", labelKey: "settings.wipeCoreLabel", helpKey: "settings.wipeCoreHelp" },
  { key: "providers", labelKey: "settings.wipeProvidersLabel", helpKey: "settings.wipeProvidersHelp" },
  { key: "mesa", labelKey: "settings.wipeMesaLabel", helpKey: "settings.wipeMesaHelp" },
];

interface Props {
  scopes: WipeScopes;
  onWiped: () => void;
  onClose: () => void;
}

/** The typed WIPE confirmation, kept as a dialog because the deletion cannot be undone. */
export function WipeConfirmModal({ scopes, onWiped, onClose }: Props) {
  const [typed, setTyped] = useState("");
  const [wiping, setWiping] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const nothingSelected = !scopes.core && !scopes.providers && !scopes.mesa;

  async function doWipe() {
    setWiping(true);
    setError(null);
    try {
      await api.wipe({ wipe_core: scopes.core, wipe_providers: scopes.providers, wipe_mesa: scopes.mesa });
      onWiped();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : t("settings.wipeFailed"));
      setWiping(false);
    }
  }

  return (
    // Over the whole workspace, plain UI2 dialog: no coloured title or banner; only Wipe is red (user, 2026-10-28).
    <Modal titleId="wipe-title" onClose={wiping ? undefined : onClose} overWorkspace>
      <h3 className="modal-title" id="wipe-title">
        {t("settings.wipeTitle")}
      </h3>
      <p className="modal-body-text"><strong>{t("settings.wipeWarningSelected")}</strong></p>
      <ul className="phx-wipe-scopes">
        {WIPE_SCOPES.filter((scope) => scopes[scope.key]).map((scope) => <li key={scope.key}>{t(scope.labelKey)}</li>)}
      </ul>

      <div className="field settings-toggle-mt">
        <label htmlFor="wipe-confirm-input">{t("settings.wipeTypeConfirm")}</label>
        <input
          id="wipe-confirm-input"
          className="input"
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="WIPE"
          autoFocus
        />
      </div>
      {error && <div className="banner banner-error" role="alert">{error}</div>}
      <div className="modal-actions">
        <ActionButton icon="undo" label={t("settings.cancel")} className="btn btn-outline btn-sm" onClick={onClose} busy={wiping} />
        <ActionButton icon="delete" label={wiping ? t("settings.wipeBusy") : t("settings.wipeSubmit")} className="btn btn-danger btn-sm"
          onClick={() => void doWipe()} disabled={typed !== "WIPE" || nothingSelected} busy={wiping} />
      </div>
    </Modal>
  );
}
