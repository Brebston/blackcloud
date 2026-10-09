import { FormEvent, useCallback, useEffect, useState } from "react";
import { errorText, get, patch, post, del } from "../api/client";
import type { Paginated } from "../api/types";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";
import { AntivirusTab, MailboxesTab, QuarantineTab } from "./AdminTabs";

interface AdminUser {
  id: string;
  username: string;
  email: string;
  display_name: string;
  is_active: boolean;
  is_staff: boolean;
  has_2fa: boolean;
  quota_bytes: number;
  used_bytes: number;
  date_joined: string;
  last_login: string | null;
}

interface Invite {
  id: string;
  email: string;
  quota_bytes: number | null;
  created_at: string;
  expires_at: string;
  used_at: string | null;
  valid: boolean;
  url?: string;
}

interface Stats {
  total: number;
  active: number;
  with_2fa: number;
  used: number | null;
  allocated: number | null;
  files: number;
  infected: number;
}

export default function AdminPage() {
  const { user } = useAuth();
  const toast = useToast();
  const t = useT();
  const [tab, setTab] = useState<"users" | "invites" | "audit" | "mailboxes" | "quarantine" | "antivirus">("users");
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [denied, setDenied] = useState("");

  const loadUsers = useCallback(async () => {
    try {
      const r = await get<Paginated<AdminUser>>(`/api/admin/users/?page_size=200&q=${encodeURIComponent(q)}`);
      setUsers(r.results);
      setStats(await get<Stats>("/api/admin/users/stats/"));
    } catch (e) {
      setDenied(errorText(e));
    }
  }, [q]);

  useEffect(() => {
    const t = setTimeout(loadUsers, 250);
    return () => clearTimeout(t);
  }, [loadUsers]);

  if (denied) {
    return (
      <div className="page">
        <div className="banner banner-warning">{denied}</div>
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <h2>{t("nav.admin")}</h2>
      </div>

      {stats && (
        <div className="stats">
          <div className="card stat">
            <div className="stat-label">Користувачі</div>
            <div className="stat-value">{stats.active}</div>
            <div className="small muted">усього {stats.total}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">З 2FA</div>
            <div className="stat-value">{stats.total ? Math.round((stats.with_2fa / stats.total) * 100) : 0}%</div>
            <div className="small muted">{stats.with_2fa} користувачів</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Використано</div>
            <div className="stat-value">{formatBytes(stats.used || 0)}</div>
            <div className="small muted">виділено {formatBytes(stats.allocated || 0)}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">Файли</div>
            <div className="stat-value">{stats.files}</div>
            <div className={`small ${stats.infected ? "text-danger" : "muted"}`}>заблоковано: {stats.infected}</div>
          </div>
        </div>
      )}

      <div className="tabs">
        <button className={`tab ${tab === "users" ? "active" : ""}`} onClick={() => setTab("users")}>
          <Icon name="users" size={16} /> {t("admin.tab.users")}
        </button>
        <button className={`tab ${tab === "invites" ? "active" : ""}`} onClick={() => setTab("invites")}>
          <Icon name="mail" size={16} /> {t("admin.tab.invites")}
        </button>
        <button className={`tab ${tab === "audit" ? "active" : ""}`} onClick={() => setTab("audit")}>
          <Icon name="eye" size={16} /> {t("admin.tab.audit")}
        </button>
        <button className={`tab ${tab === "mailboxes" ? "active" : ""}`} onClick={() => setTab("mailboxes")}>
          <Icon name="inbox" size={16} /> {t("admin.tab.mailboxes")}
        </button>
        <button className={`tab ${tab === "quarantine" ? "active" : ""}`} onClick={() => setTab("quarantine")}>
          <Icon name="bug" size={16} /> {t("admin.tab.quarantine")}
        </button>
        <button className={`tab ${tab === "antivirus" ? "active" : ""}`} onClick={() => setTab("antivirus")}>
          <Icon name="shield" size={16} /> {t("admin.tab.antivirus")}
        </button>
      </div>

      {tab === "users" && (
        <>
          <div className="toolbar">
            <div className="search">
              <Icon name="search" size={16} />
              <input placeholder="Пошук користувачів…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              <Icon name="plus" size={16} /> Користувач
            </button>
          </div>
          <div className="card table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>Користувач</th>
                  <th>Сховище</th>
                  <th>2FA</th>
                  <th>Останній вхід</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const pct = u.quota_bytes ? (u.used_bytes / u.quota_bytes) * 100 : 0;
                  return (
                    <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                      <td>
                        <div>
                          <strong>{u.username}</strong> {u.is_staff && <span className="pill">адмін</span>}
                          {!u.is_active && <span className="pill pill-danger">заблоковано</span>}
                        </div>
                        <div className="small muted">{u.email}</div>
                      </td>
                      <td className="quota-cell">
                        <div className="quota-track small-track">
                          <div className={`quota-fill ${pct > 90 ? "quota-danger" : pct > 75 ? "quota-warning" : "quota-ok"}`} style={{ width: `${Math.min(100, pct)}%` }} />
                        </div>
                        <div className="small muted">
                          {formatBytes(u.used_bytes)} / {formatBytes(u.quota_bytes)}
                        </div>
                      </td>
                      <td>{u.has_2fa ? <span className="pill pill-success">так</span> : <span className="pill pill-warning">ні</span>}</td>
                      <td className="small muted">{formatDate(u.last_login)}</td>
                      <td>
                        <button className="btn btn-sm" onClick={() => setEditing(u)}>
                          Керувати
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === "invites" && <InvitesTab />}
      {tab === "audit" && <AuditTab />}
      {tab === "mailboxes" && <MailboxesTab />}
      {tab === "quarantine" && <QuarantineTab />}
      {tab === "antivirus" && <AntivirusTab />}

      {editing && (
        <EditUserDialog
          user={editing}
          isSelf={editing.id === user?.id}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            loadUsers();
            toast("Збережено", "success");
          }}
        />
      )}
      {creating && (
        <CreateUserDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            loadUsers();
          }}
        />
      )}
    </div>
  );
}

function EditUserDialog({ user, isSelf, onClose, onSaved }: { user: AdminUser; isSelf: boolean; onClose: () => void; onSaved: () => void }) {
  const [quotaGb, setQuotaGb] = useState(String(Math.round((user.quota_bytes / 1024 ** 3) * 100) / 100));
  const [active, setActive] = useState(user.is_active);
  const [error, setError] = useState("");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const q = Number(quotaGb);
    if (!Number.isFinite(q) || q < 0 || q > 100000) return setError("Квота: число від 0 до 100000 ГБ.");
    try {
      await patch(`/api/admin/users/${user.id}/`, { quota_gb: q, is_active: active });
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const reset2fa = async () => {
    if (!window.confirm(`Скинути 2FA для ${user.username}? Користувач зможе увійти лише з паролем.`)) return;
    try {
      await post(`/api/admin/users/${user.id}/reset_2fa/`);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={user.username} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <p className="small muted">
          Використано {formatBytes(user.used_bytes)}. Зареєстровано {formatDate(user.date_joined, false)}.
        </p>
        <label>
          Квота, ГБ
          <input type="number" min={0} step="0.5" value={quotaGb} onChange={(e) => setQuotaGb(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={active} disabled={isSelf} onChange={(e) => setActive(e.target.checked)} />
          Акаунт активний (вимкнення завершує всі сесії)
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          {user.has_2fa && !isSelf && (
            <button type="button" className="btn btn-danger" onClick={reset2fa}>
              Скинути 2FA
            </button>
          )}
          <div className="spacer" />
          <button className="btn btn-primary">Зберегти</button>
        </div>
      </form>
    </Modal>
  );
}

function CreateUserDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [form, setForm] = useState({ username: "", email: "", password: "", quota_gb: "10" });
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^[a-z0-9][a-z0-9._-]{2,39}$/.test(form.username)) return setError("Невірне ім'я користувача.");
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email)) return setError("Невірний email.");
    if (form.password.length < 12) return setError("Пароль — мінімум 12 символів.");
    try {
      await post("/api/admin/users/", { ...form, quota_gb: Number(form.quota_gb) });
      onCreated();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Modal title="Новий користувач" onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <input placeholder="Ім'я користувача" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} />
        <input placeholder="Email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input placeholder="Тимчасовий пароль" type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <label>
          Квота, ГБ
          <input type="number" min={0} value={form.quota_gb} onChange={(e) => setForm({ ...form, quota_gb: e.target.value })} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-primary">Створити</button>
        </div>
      </form>
    </Modal>
  );
}

function InvitesTab() {
  const toast = useToast();
  const [invites, setInvites] = useState<Invite[]>([]);
  const [form, setForm] = useState({ email: "", quota_gb: "", days: "7" });
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState("");

  const load = () => get<Paginated<Invite>>("/api/admin/invites/").then((r) => setInvites(r.results));
  useEffect(() => {
    load();
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const days = Number(form.days);
    if (!Number.isInteger(days) || days < 1 || days > 30) return setError("Термін: 1–30 днів.");
    try {
      const r = await post<Invite>("/api/admin/invites/", {
        email: form.email,
        days,
        ...(form.quota_gb ? { quota_gb: Number(form.quota_gb) } : {}),
      });
      setCreated(r.url || null);
      setForm({ email: "", quota_gb: "", days: "7" });
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <div className="stack">
      <form className="card grid-4" onSubmit={create}>
        <label>
          Email (необов'язково)
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label>
          Квота, ГБ
          <input type="number" min={0} placeholder="за замовч." value={form.quota_gb} onChange={(e) => setForm({ ...form, quota_gb: e.target.value })} />
        </label>
        <label>
          Діє днів
          <input type="number" min={1} max={30} value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} />
        </label>
        <button className="btn btn-primary">Створити запрошення</button>
      </form>
      {error && <div className="form-error">{error}</div>}
      {created && (
        <div className="secret-box">
          <div className="small">Посилання-запрошення (показується один раз):</div>
          <div className="row-between">
            <code className="break">{created}</code>
            <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(created).then(() => toast("Скопійовано", "success"))}>
              Копіювати
            </button>
          </div>
        </div>
      )}
      <div className="card table-card">
        <table className="table">
          <tbody>
            {invites.map((i) => (
              <tr key={i.id}>
                <td>{i.email || <span className="muted">будь-хто</span>}</td>
                <td className="small muted">до {formatDate(i.expires_at)}</td>
                <td>
                  {i.used_at ? <span className="pill">використано</span> : i.valid ? <span className="pill pill-success">активне</span> : <span className="pill">прострочене</span>}
                </td>
                <td>
                  {!i.used_at && (
                    <button className="link-btn danger" onClick={() => del(`/api/admin/invites/${i.id}/`).then(load)}>
                      Видалити
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AuditTab() {
  const [rows, setRows] = useState<{ id: number; created_at: string; action: string; user: string | null; ip_address: string | null; target: string }[]>([]);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const t = setTimeout(() => {
      get<Paginated<(typeof rows)[number]>>(`/api/admin/audit/?page_size=200&action=${encodeURIComponent(filter)}`).then((r) => setRows(r.results));
    }, 250);
    return () => clearTimeout(t);
  }, [filter]);
  return (
    <div className="stack">
      <div className="search">
        <Icon name="search" size={16} />
        <input placeholder="Фільтр дії (напр. login.failed)" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </div>
      <div className="card table-card">
        <table className="table">
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td className="small muted nowrap">{formatDate(r.created_at)}</td>
                <td className={r.action.includes("failed") || r.action.includes("infected") ? "text-danger" : ""}>{r.action}</td>
                <td>{r.user || "—"}</td>
                <td className="small">{r.ip_address}</td>
                <td className="small muted truncate">{r.target}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
