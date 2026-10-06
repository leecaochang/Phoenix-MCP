import { useState, useEffect, useCallback, useRef } from "react";
import type { ResolveResult } from "../types";
import { api } from "../api";
import { MesaProfileLink } from "./MesaProfileLink";
import { t } from "../i18n";
import { PERMISSION_LABEL_KEYS } from "../utils";

type ResolveDepth = "entity" | "device" | "domain";

interface Props {
  accessKeyId: string;
  externalEntityId?: string;
  resolveDepth?: ResolveDepth;
  triggerVersion?: number;
  mesaProfileEntities?: Set<string>;
  onOpenMesa?: (entityId: string) => void;
}

function filterPath(
  path: Array<{ level: string; state: string }>,
  depth: ResolveDepth,
): Array<{ level: string; state: string }> {
  if (depth === "entity") return path;
  if (depth === "device") return path.filter((s) => !s.level.startsWith("entity:"));
  return path.filter((s) => !s.level.startsWith("device:") && !s.level.startsWith("entity:"));
}

function effectiveFromPath(steps: Array<{ level: string; state: string }>): string {
  if (steps.some((s) => s.state === "RED")) return "DENY";
  for (let i = steps.length - 1; i >= 0; i--) {
    if (steps[i].state === "GREEN") return "WRITE";
    if (steps[i].state === "YELLOW") return "READ";
  }
  return "NO_ACCESS";
}

const EFFECTIVE_CLASS: Record<string, string> = {
  WRITE: "state-GREEN",
  READ: "state-YELLOW",
  DENY: "state-RED",
  NO_ACCESS: "state-GREY",
  NOT_FOUND: "state-GREY",
};

export function PermissionSimulator({ accessKeyId, externalEntityId, resolveDepth = "entity", triggerVersion, mesaProfileEntities, onOpenMesa }: Props) {
  const [entityInput, setEntityInput] = useState(externalEntityId ?? "");
  const [result, setResult] = useState<ResolveResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const entityInputRef = useRef(entityInput);
  entityInputRef.current = entityInput;
  const externalUpdateRef = useRef(false);
  const genRef = useRef(0);

  const simulate = useCallback(async (eid: string) => {
    if (!eid.trim()) return;
    const gen = ++genRef.current;
    setLoading(true);
    setError(null);
    try {
      const data = await api.resolve(accessKeyId, eid.trim());
      if (gen !== genRef.current) return;
      setResult(data);
    } catch (e: unknown) {
      if (gen !== genRef.current) return;
      setError(e instanceof Error ? e.message : t("perms.simFailed"));
      setResult(null);
    } finally {
      if (gen !== genRef.current) return;
      setLoading(false);
    }
  }, [accessKeyId]);

  // External entity set: update input and suppress the typing debounce (the
  // simulate effect below handles it).
  useEffect(() => {
    if (externalEntityId) {
      externalUpdateRef.current = true;
      setEntityInput(externalEntityId);
    }
  }, [externalEntityId]);

  // Simulate when entity selection or permissions version changes. A new
  // selection is simulated directly, because entityInputRef may still hold the
  // previous entity; a permission refresh re-simulates what the box shows now,
  // which the operator may have cleared or retyped since selecting.
  const lastExternal = useRef<string | undefined>(undefined);
  useEffect(() => {
    const selected = lastExternal.current !== externalEntityId && externalEntityId?.trim();
    lastExternal.current = externalEntityId;
    const entity = selected || entityInputRef.current.trim();
    if (!entity) return;
    if (!triggerVersion && !externalEntityId) return;
    simulate(entity);
  }, [triggerVersion, externalEntityId, simulate]);

  // Manual typing: debounce 600ms (skip if input was set externally)
  useEffect(() => {
    if (!entityInput.trim()) {
      // An empty box shows nothing, including a reply still on its way for
      // the entity that was there before.
      genRef.current += 1;
      setResult(null);
      setError(null);
      setLoading(false);
      return;
    }
    if (externalUpdateRef.current) {
      externalUpdateRef.current = false;
      return;
    }
    const timer = setTimeout(() => simulate(entityInput), 600);
    return () => clearTimeout(timer);
  }, [entityInput, simulate]);

  return (
    <div>
      <input
        className="input"
        aria-label={t("perms.simEntityAria")}
        placeholder={t("perms.simEntityPlaceholder")}
        value={entityInput}
        onChange={(e) => setEntityInput(e.target.value)}
      />
      {loading && !result && <div className="sim-loading" role="status">{t("perms.simulating")}</div>}
      {error && <div className="banner banner-error mt-6">{error}</div>}
      {result && (() => {
        const visiblePath = filterPath(result.resolution_path, resolveDepth);
        // The server's verdict is authoritative for an entity: it also applies
        // what the path cannot show (a missing entity, a blocked domain, a
        // persona or household limit). A device or domain row previews only
        // its own ancestor rules, but never for an entity that does not exist.
        const effective = resolveDepth === "entity" || result.effective === "NOT_FOUND"
          ? result.effective : effectiveFromPath(visiblePath);
        return (
          <div className="sim-path">
            {visiblePath.map((step, i) => (
              <div key={i} className="sim-step">
                <span className="sim-resolution-level">{step.level}</span>
                {" -> "}
                <span className={`state-${step.state}`}>
                  {step.state}
                </span>
              </div>
            ))}
            <div className="sim-result">
              {t("perms.simResult")}
              <span className={EFFECTIVE_CLASS[effective] ?? "state-GREY"}>
                {PERMISSION_LABEL_KEYS[effective] ? t(PERMISSION_LABEL_KEYS[effective]) : effective}
              </span>
            </div>
            {onOpenMesa && entityInput.includes(".") && (
              <div className="sim-mesa-row">
                <MesaProfileLink
                  targetKey={entityInput.trim()}
                  exists={!!mesaProfileEntities?.has(entityInput.trim())}
                  onOpen={onOpenMesa}
                />
                <span className="sim-mesa-label">
                  {mesaProfileEntities?.has(entityInput.trim()) ? t("perms.viewMesaProfile") : t("perms.noMesaProfile")}
                </span>
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}
