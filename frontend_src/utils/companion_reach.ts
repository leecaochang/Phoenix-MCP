/**
 * What a Home Companion key can reach, whatever its permission tree says.
 *
 * Mirrors the persona check in the server's resolver (policy_engine.resolve):
 * only entities with a live state in its control or read domains, sensors only
 * of its device classes, and a Write in a read domain acts as Read. The domain
 * sets come from the server, so they cannot drift; the live states come from
 * Home Assistant, as the resolver reads them.
 */
import { useEffect, useState } from "react";
import { api, currentHass } from "../api";

export interface CompanionDomains {
  control_domains: string[];
  read_domains: string[];
  sensor_classes: string[];
}

/** control: Write applies; read: Write acts as Read; null: out of reach. */
export type CompanionCap = "control" | "read" | null;

type States = Record<string, { attributes?: Record<string, unknown> } | undefined>;

let loading: Promise<CompanionDomains | null> | null = null;

function loadDomains(): Promise<CompanionDomains | null> {
  loading ??= api.getInfo().then((info) => info.home_companion_reach ?? null).catch(() => {
    loading = null;
    return null;
  });
  return loading;
}

/** The domain sets while `enabled` (a Home Companion key is shown), else null. */
export function useCompanionDomains(enabled: boolean): CompanionDomains | null {
  const [domains, setDomains] = useState<CompanionDomains | null>(null);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    void loadDomains().then((d) => { if (live) setDomains(d); });
    return () => { live = false; };
  }, [enabled]);
  return enabled ? domains : null;
}

/** A whole domain's reach; the sensor domain reads as read, though only some of its classes qualify. */
export function companionDomainCap(domain: string, d: CompanionDomains): CompanionCap {
  if (d.control_domains.includes(domain)) return "control";
  return d.read_domains.includes(domain) ? "read" : null;
}

/** One entity's reach, from its domain and its live state. */
export function companionCap(entityId: string, d: CompanionDomains, states: States | undefined = (currentHass() as { states?: States } | null)?.states): CompanionCap {
  const domain = entityId.split(".")[0];
  const state = states?.[entityId];
  if (!state) return null;
  if (domain === "sensor" && !d.sensor_classes.includes(String(state.attributes?.device_class ?? ""))) return null;
  return companionDomainCap(domain, d);
}

/** An effective label (WRITE, READ, DENY, NO_ACCESS) as a Home Companion key gets it. */
export function companionLabel(label: string, cap: CompanionCap): string {
  if (cap === null) return "NO_ACCESS";
  return cap === "read" && label === "WRITE" ? "READ" : label;
}
