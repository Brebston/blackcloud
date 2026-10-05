import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useNavigate } from "react-router-dom";
import { get, post } from "../api/client";
import type { Notification } from "../api/types";
import { useAuth } from "../hooks/useAuth";
import { closeEvents, useEvents } from "../hooks/useEvents";
import { formatDate } from "../lib/format";
import Icon from "./Icon";
import QuotaBar from "./QuotaBar";
import { useToast } from "./Toast";

const NAV = [
  { to: "/files", icon: "folder", label: "Файли" },
  { to: "/shared", icon: "share", label: "Спільні" },
  { to: "/calendar", icon: "calendar", label: "Календар" },
  { to: "/mail", icon: "mail", label: "Пошта" },
  { to: "/chat", icon: "chat", label: "Чат" },
  { to: "/trash", icon: "trash", label: "Кошик" },
  { to: "/settings", icon: "settings", label: "Налаштування" },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout, refresh } = useAuth();
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

  if (!user) return null;

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
              <span>{item.label}</span>
            </NavLink>
          ))}
          {user.is_staff && (
            <NavLink to="/admin" className="nav-item" onClick={() => setNavOpen(false)}>
              <Icon name="admin" />
              <span>Адміністрування</span>
            </NavLink>
          )}
        </nav>
        <div className="sidebar-foot">
          <QuotaBar used={user.used_bytes} quota={user.quota_bytes} />
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setNavOpen(!navOpen)} aria-label="Меню">
            <Icon name="menu" />
          </button>
          <div className="topbar-spacer" />
          {user.has_2fa ? (
            <span className="pill pill-success" title="Двофакторна автентифікація увімкнена">
              <Icon name="shield" size={14} /> 2FA
            </span>
          ) : (
            <Link to="/settings?tab=security" className="pill pill-warning">
              <Icon name="alert" size={14} /> Увімкніть 2FA
            </Link>
          )}
          <div className="dropdown" ref={notifRef}>
            <button className="icon-btn" onClick={() => setShowNotif(!showNotif)} aria-label="Сповіщення">
              <Icon name="bell" />
              {unread > 0 && <span className="badge">{unread}</span>}
            </button>
            {showNotif && (
              <div className="dropdown-menu notif-menu">
                <div className="dropdown-head">
                  <strong>Сповіщення</strong>
                  {unread > 0 && (
                    <button className="link-btn" onClick={markAll}>
                      Позначити прочитаними
                    </button>
                  )}
                </div>
                {notifications.length === 0 && <div className="empty-small">Немає сповіщень</div>}
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
                {[
                  { to: "/settings?tab=profile", icon: "user", label: "Мій профіль" },
                  { to: "/settings?tab=security", icon: "shield", label: "Безпека та 2FA" },
                  { to: "/settings?tab=sessions", icon: "lock", label: "Мої пристрої" },
                  { to: "/settings?tab=mail", icon: "mail", label: "Поштові клієнти" },
                  { to: "/settings?tab=activity", icon: "eye", label: "Журнал входів" },
                  ...(user.is_staff ? [{ to: "/admin", icon: "admin", label: "Адміністрування" }] : []),
                ].map((item) => (
                  <button
                    key={item.to}
                    role="menuitem"
                    className="menu-item"
                    onClick={() => {
                      setShowUserMenu(false);
                      navigate(item.to);
                    }}
                  >
                    <Icon name={item.icon} size={16} /> {item.label}
                  </button>
                ))}
                <div className="menu-sep" />
                <button role="menuitem" className="menu-item danger" onClick={doLogout}>
                  <Icon name="logout" size={16} /> Вийти
                </button>
              </div>
            )}
          </div>
        </header>
        {user.must_enroll_2fa && (
          <div className="banner banner-warning">
            Адміністратори зобов'язані використовувати 2FA. Доступ до адмін-функцій заблоковано, доки ви не{" "}
            <Link to="/settings?tab=security">увімкнете двофакторну автентифікацію</Link>.
          </div>
        )}
        <main className="content">{children}</main>
      </div>
    </div>
  );
}
