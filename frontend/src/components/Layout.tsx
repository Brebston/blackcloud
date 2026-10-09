import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import type { Notification } from "../api/types";
import { useAuth } from "../hooks/useAuth";
import { closeEvents, useEvents } from "../hooks/useEvents";
import { LanguageSwitcher, useT } from "../i18n";
import type { TKey } from "../i18n";
import { applyAppearance, resolvedScheme } from "../lib/appearance";
import { formatDate } from "../lib/format";
import Icon from "./Icon";
import QuotaBar from "./QuotaBar";
import { useToast } from "./Toast";

const NAV: { to: string; icon: string; label: TKey }[] = [
  { to: "/files", icon: "folder", label: "nav.files" },
  { to: "/shared", icon: "share", label: "nav.shared" },
  { to: "/calendar", icon: "calendar", label: "nav.calendar" },
  { to: "/mail", icon: "mail", label: "nav.mail" },
  { to: "/chat", icon: "chat", label: "nav.chat" },
  { to: "/trash", icon: "trash", label: "nav.trash" },
  { to: "/settings", icon: "settings", label: "nav.settings" },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout, refresh, updatePreferences } = useAuth();
  const t = useT();
  const navigate = useNavigate();
  const toast = useToast();
  const [notifications, setNotifications] = useState<Notification[]>([]);
  const [showNotif, setShowNotif] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const userMenuRef = useRef<HTMLDivElement>(null);
  const notifRef = useRef<HTMLDivElement>(null);

  // Закривати меню кліком поза ними
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) setShowUserMenu(false);
      if (notifRef.current && !notifRef.current.contains(e.target as Node)) setShowNotif(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const loadNotifications = useCallback(async () => {
    try {
      const data = await get<{ results: Notification[] }>("/api/notifications/?page_size=20");
      setNotifications(data.results);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    loadNotifications();
  }, [loadNotifications]);

  useEvents((event, payload) => {
    if (event === "notification") {
      setNotifications((xs) => [{ ...payload, read_at: null }, ...xs].slice(0, 20));
      toast(payload.title, payload.kind === "security" ? "error" : "info");
    }
    if (event === "file.updated") refresh();
  });

  const unread = notifications.filter((n) => !n.read_at).length;

  const markAll = async () => {
    await post("/api/notifications/mark_all_read/");
    setNotifications((xs) => xs.map((n) => ({ ...n, read_at: n.read_at || new Date().toISOString() })));
  };

  const doLogout = async () => {
    closeEvents();
    await logout();
    navigate("/login");
  };

  // Швидке перемикання світла/темна (вибір «як у системі» — у Налаштуваннях)
  const toggleTheme = () => {
    if (!user) return;
    const next = resolvedScheme(user.preferences.theme) === "dark" ? "light" : "dark";
    applyAppearance(next);
    updatePreferences({ theme: next }).catch(() => toast(t("common.saveFailed"), "error"));
  };

  if (!user) return null;
  const isDark = resolvedScheme(user.preferences.theme) === "dark";

  return (
    <div className={`app ${navOpen ? "nav-open" : ""}`}>
      <aside className="sidebar">
        <Link to="/files" className="brand" onClick={() => setNavOpen(false)}>
          <Icon name="cloud" size={22} />
          <span>BlackCloud</span>
        </Link>
        <nav>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} className="nav-item" onClick={() => setNavOpen(false)}>
              <Icon name={item.icon} />
              <span>{t(item.label)}</span>
            </NavLink>
          ))}
          {user.is_staff && (
            <NavLink to="/admin" className="nav-item" onClick={() => setNavOpen(false)}>
              <Icon name="admin" />
              <span>{t("nav.admin")}</span>
            </NavLink>
          )}
        </nav>
        <div className="sidebar-foot">
          <QuotaBar used={user.used_bytes} quota={user.quota_bytes} />
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setNavOpen(!navOpen)} aria-label={t("nav.menu")}>
            <Icon name="menu" />
          </button>
          <div className="topbar-spacer" />
          {user.has_2fa ? (
            <span className="pill pill-success" title={t("top.2faOn")}>
              <Icon name="shield" size={14} /> 2FA
            </span>
          ) : (
            <Link to="/settings?tab=security" className="pill pill-warning">
              <Icon name="alert" size={14} /> {t("top.enable2fa")}
            </Link>
          )}
          <LanguageSwitcher className="desktop-only" />
          <button
            className="icon-btn"
            onClick={toggleTheme}
            aria-label={isDark ? t("top.lightTheme") : t("top.darkTheme")}
            title={isDark ? t("top.lightTheme") : t("top.darkTheme")}
          >
            <Icon name={isDark ? "sun" : "moon"} />
          </button>
          <div className="dropdown" ref={notifRef}>
            <button className="icon-btn" onClick={() => setShowNotif(!showNotif)} aria-label={t("top.notifications")}>
              <Icon name="bell" />
              {unread > 0 && <span className="badge">{unread}</span>}
            </button>
            {showNotif && (
              <div className="dropdown-menu notif-menu">
                <div className="dropdown-head">
                  <strong>{t("top.notifications")}</strong>
                  {unread > 0 && (
                    <button className="link-btn" onClick={markAll}>
                      {t("top.markRead")}
                    </button>
                  )}
                </div>
                {notifications.length === 0 && <div className="empty-small">{t("top.noNotifications")}</div>}
                {notifications.map((n) => (
                  <button
                    key={n.id}
                    className={`notif ${n.read_at ? "" : "unread"}`}
                    onClick={() => {
                      setShowNotif(false);
                      if (n.link) navigate(n.link);
                    }}
                  >
                    <div className="notif-title">{n.title}</div>
                    {n.body && <div className="notif-body">{n.body}</div>}
                    <div className="notif-time">{formatDate(n.created_at)}</div>
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="dropdown" ref={userMenuRef}>
            <button
              className="user-chip"
              onClick={() => setShowUserMenu(!showUserMenu)}
              aria-haspopup="menu"
              aria-expanded={showUserMenu}
            >
              <div className="avatar">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</div>
              <span className="desktop-only">{user.display_name || user.username}</span>
              <Icon name="chevronDown" size={14} />
            </button>
            {showUserMenu && (
              <div className="dropdown-menu user-menu" role="menu">
                <div className="user-menu-head">
                  <div className="avatar">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</div>
                  <div className="truncate">
                    <strong className="truncate">{user.display_name || user.username}</strong>
                    <div className="small muted truncate">{user.mailbox || user.email}</div>
                  </div>
                </div>
                {(
                  [
                    { to: "/settings?tab=profile", icon: "user", label: "menu.profile" },
                    { to: "/settings?tab=security", icon: "shield", label: "menu.security" },
                    { to: "/settings?tab=sessions", icon: "lock", label: "menu.sessions" },
                    { to: "/settings?tab=mail", icon: "mail", label: "menu.mailClients" },
                    { to: "/settings?tab=activity", icon: "eye", label: "menu.activity" },
                    ...(user.is_staff ? [{ to: "/admin", icon: "admin", label: "nav.admin" }] : []),
                  ] as { to: string; icon: string; label: TKey }[]
                ).map((item) => (
                  <button
                    key={item.to}
                    role="menuitem"
                    className="menu-item"
                    onClick={() => {
                      setShowUserMenu(false);
                      navigate(item.to);
                    }}
                  >
                    <Icon name={item.icon} size={16} /> {t(item.label)}
                  </button>
                ))}
                <div className="menu-sep" />
                <div className="menu-row mobile-only">
                  <LanguageSwitcher />
                </div>
                <button role="menuitem" className="menu-item danger" onClick={doLogout}>
                  <Icon name="logout" size={16} /> {t("menu.logout")}
                </button>
              </div>
            )}
          </div>
        </header>
        {user.must_enroll_2fa && (
          <div className="banner banner-warning">
            {t("banner.admin2fa")} <Link to="/settings?tab=security">{t("banner.admin2faLink")}</Link>.
          </div>
        )}
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
