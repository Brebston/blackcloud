// Мобільна навігація (≤ 820px): нижня панель вкладок + шторка «Ще» з рештою розділів і налаштувань.
// На широкому екрані прихована стилями — там лишається бічне меню.
import { useState } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import type { Notification } from "../api/types";
import { LanguageSwitcher, useT } from "../i18n";
import type { TKey } from "../i18n";
import { useBadges } from "../lib/badges";
import { formatDate } from "../lib/format";
import { isIOS, isStandalone } from "../lib/pwa";
import Icon from "./Icon";
import Modal from "./Modal";

const TABS: { to: string; icon: string; label: TKey }[] = [
  { to: "/files", icon: "folder", label: "nav.files" },
  { to: "/mail", icon: "mail", label: "nav.mail" },
  { to: "/calendar", icon: "calendar", label: "nav.calendar" },
  { to: "/chat", icon: "chat", label: "nav.chat" },
];

const MORE: { to: string; icon: string; label: TKey; staff?: boolean }[] = [
  { to: "/shared", icon: "share", label: "nav.shared" },
  { to: "/trash", icon: "trash", label: "nav.trash" },
  { to: "/settings", icon: "settings", label: "nav.settings" },
  { to: "/admin", icon: "admin", label: "nav.admin", staff: true },
];

const badgeText = (n: number) => (n > 99 ? "99+" : String(n));

export default function MobileNav({
  isStaff,
  isDark,
  notifications,
  unread,
  onToggleTheme,
  onMarkAll,
  onLogout,
}: {
  isStaff: boolean;
  isDark: boolean;
  notifications: Notification[];
  unread: number;
  onToggleTheme: () => void;
  onMarkAll: () => void;
  onLogout: () => void;
}) {
  const t = useT();
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const badges = useBadges();
  const [open, setOpen] = useState(false);
  const moreActive = MORE.some((m) => pathname.startsWith(m.to));
  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  return (
    <>
      <nav className="tabbar" aria-label={t("mobile.tabbar")}>
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} className="tab-item">
            <span className="tab-icon">
              <Icon name={tab.icon} size={22} />
              {tab.to === "/mail" && badges.mail > 0 && <span className="badge">{badgeText(badges.mail)}</span>}
            </span>
            <span className="tab-label">{t(tab.label)}</span>
          </NavLink>
        ))}
        <button
          type="button"
          className={`tab-item ${moreActive || open ? "active" : ""}`}
          aria-haspopup="dialog"
          aria-expanded={open}
          onClick={() => setOpen(true)}
        >
          <span className="tab-icon">
            <Icon name="more" size={22} />
            {unread > 0 && <span className="badge">{badgeText(unread)}</span>}
          </span>
          <span className="tab-label">{t("mobile.more")}</span>
        </button>
      </nav>

      {open && (
        <Modal title={t("mobile.more")} onClose={() => setOpen(false)} className="more-sheet">
          <div className="more-grid">
            {MORE.filter((m) => !m.staff || isStaff).map((m) => (
              <button key={m.to} type="button" className={`more-item ${pathname.startsWith(m.to) ? "active" : ""}`} onClick={() => go(m.to)}>
                <Icon name={m.icon} size={22} />
                <span>{t(m.label)}</span>
              </button>
            ))}
          </div>

          <div className="more-list">
            <button type="button" className="menu-item" onClick={onToggleTheme}>
              <Icon name={isDark ? "sun" : "moon"} size={18} /> {isDark ? t("top.lightTheme") : t("top.darkTheme")}
            </button>
            <div className="menu-item more-lang">
              <Icon name="globe" size={18} />
              <span className="grow">{t("common.language")}</span>
              <LanguageSwitcher />
            </div>
          </div>

          <div className="more-section">
            <div className="row-between">
              <strong className="row gap-sm">
                <Icon name="bell" size={16} /> {t("top.notifications")}
                {unread > 0 && <span className="count">{unread}</span>}
              </strong>
              {unread > 0 && (
                <button type="button" className="link-btn" onClick={onMarkAll}>
                  {t("top.markRead")}
                </button>
              )}
            </div>
            {notifications.length === 0 && <div className="empty-small">{t("top.noNotifications")}</div>}
            <div className="more-notifs">
              {notifications.slice(0, 8).map((n) => (
                <button
                  key={n.id}
                  type="button"
                  className={`notif ${n.read_at ? "" : "unread"}`}
                  onClick={() => (n.link ? go(n.link) : undefined)}
                >
                  <div className="notif-title">{n.title}</div>
                  {n.body && <div className="notif-body">{n.body}</div>}
                  <div className="notif-time">{formatDate(n.created_at)}</div>
                </button>
              ))}
            </div>
          </div>

          <details className="more-section more-share">
            <summary>
              <Icon name="upload" size={16} /> {t("mobile.shareTitle")}
            </summary>
            <p className="small muted">
              {isIOS() ? t("mobile.shareIos") : isStandalone() ? t("mobile.shareAndroid") : t("mobile.shareInstall")}
            </p>
            {isIOS() && (
              <button type="button" className="btn btn-sm" onClick={() => go("/settings?tab=dav")}>
                <Icon name="folder" size={16} /> {t("settings.tab.dav")}
              </button>
            )}
          </details>

          <button type="button" className="menu-item danger" onClick={onLogout}>
            <Icon name="logout" size={18} /> {t("menu.logout")}
          </button>
        </Modal>
      )}
    </>
  );
}
