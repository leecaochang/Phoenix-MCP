/**
 * The access key list behind a Settings dropdown. It reads the list again
 * whenever a key is created or revoked anywhere in the panel, so a dropdown
 * never offers a revoked key under the name of the key that replaced it.
 */
import { useEffect, useState } from "react";
import { api } from "../api";
import type { AccessKeyRecord } from "../types";

export function useAccessKeyList(): AccessKeyRecord[] {
  const [accessKeys, setAccessKeys] = useState<AccessKeyRecord[]>([]);
  useEffect(() => {
    // Only the newest read may land, so a slow earlier reply cannot bring a revoked key back.
    let live = true;
    let latest = 0;
    const load = () => {
      const seq = ++latest;
      api.listAccessKeys()
        .then((keys) => { if (live && seq === latest) setAccessKeys(keys); })
        .catch(() => { if (live && seq === latest) setAccessKeys([]); });
    };
    load();
    window.addEventListener("phx-access-keys-changed", load);
    return () => {
      live = false;
      window.removeEventListener("phx-access-keys-changed", load);
    };
  }, []);
  return accessKeys;
}
