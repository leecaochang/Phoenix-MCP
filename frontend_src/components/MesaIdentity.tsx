/** Consistent operator-facing identity for MESA rows and notices. */
export function MesaIdentity({ id, name }: { id: string; name?: string | null }) {
  const friendly = name?.trim();
  return (
    <span className="mesa-identity">
      {friendly && friendly !== id && <strong className="mesa-identity-name">{friendly}</strong>}
      {friendly && friendly !== id && " "}
      <code className={`mesa-identity-id${friendly && friendly !== id ? " mesa-identity-secondary" : ""}`}>{id}</code>
    </span>
  );
}
