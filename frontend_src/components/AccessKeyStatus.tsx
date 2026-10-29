/** An access key's status shown as an icon and words, in the key list and the archived keys. */
import { accessKeyStatusLabel } from "../utils";
import { Icon, type IconName } from "../ui/Icon";

const STATUS_ICONS: Record<string, IconName> = { Active: "success", Expired: "statusExpired", Revoked: "statusRejected" };

/** Only Expired and Revoked are coloured; Active is the usual state. */
export function AccessKeyStatus({ status }: { status: string }) {
  return (
    <span className={`phx-outcome access-key-status-${status.toLowerCase()}`}>
      <Icon name={STATUS_ICONS[status] ?? "info"} size={14} />
      {accessKeyStatusLabel(status)}
    </span>
  );
}
