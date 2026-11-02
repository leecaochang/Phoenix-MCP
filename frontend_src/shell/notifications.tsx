/**
 * The Notifications window: this session's notices, newest first, each with
 * its time and a Clear button. Every workspace shows the same history.
 */

import { localeClock, localeDateTimeShort, t } from "../i18n";
import { Icon } from "../ui/Icon";
import { useNotifications } from "../ui/notify";

/** A notice's time; with its date once it is from another day, as a panel left open overnight shows. */
function noticeTime(time: number): string {
  return new Date(time).toDateString() === new Date().toDateString() ? localeClock(time) : localeDateTimeShort(time);
}

export function NotificationsWindow() {
  const center = useNotifications();
  const items = center?.history ?? [];
  if (!items.length) return <p className="phx-notifications-empty">{t("notifications.empty")}</p>;
  return (
    <ul className="phx-notifications" data-guide="window.notifications#history">
      {items.map((n) => (
        <li key={n.id} className="phx-notification">
          <div className="phx-notification-text">
            <p>{n.text}</p>
            <time dateTime={new Date(n.time).toISOString()}>{noticeTime(n.time)}</time>
          </div>
          <button type="button" className="phx-window-control" data-help="help.clearNotification" aria-label={t("notifications.clear")} title={t("notifications.clear")}
            onClick={() => center?.clear(n.id)}>
            <Icon name="close" size={14} />
          </button>
        </li>
      ))}
    </ul>
  );
}
