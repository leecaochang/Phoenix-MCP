import { useState } from "react";
import { BusyButton } from "./BusyButton";
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
    <Modal titleId="wipe-title" onClose={wiping ? undefined : onClose}>
      <h3 className="modal-title modal-title-error" id="wipe-title">
        {t("settings.wipeTitle")}
      </h3>
      <div className="banner banner-error mb-16">
        {t("settings.wipeWarningSelected")}
      </div>
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
        <BusyButton
          className="btn btn-danger"
          onClick={doWipe}
          disabled={typed !== "WIPE" || nothingSelected}
          busy={wiping}
        >
          {wiping ? t("settings.wipeBusy") : t("settings.wipeSubmit")}
        </BusyButton>
        <BusyButton className="btn btn-text" onClick={onClose} busy={wiping}>{t("settings.cancel")}</BusyButton>
      </div>
    </Modal>
  );
}
