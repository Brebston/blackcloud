import { FormEvent, useCallback, useEffect, useState } from "react";
import { errorText, get, patch, post, del } from "../api/client";
import type { Paginated } from "../api/types";
import Avatar from "../components/Avatar";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";
import { AntivirusTab, BackupsTab, MailboxesTab, QuarantineTab } from "./AdminTabs";

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
  avatar_url?: string | null;
  blocked_reason?: string;
  blocked_at?: string | null;
  blocked_by?: string | null;
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
  const [tab, setTab] = useState<"users" | "invites" | "audit" | "mailboxes" | "quarantine" | "antivirus" | "backups">("users");
  const [stats, setStats] = useState<Stats | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<AdminUser | null>(null);
  const [creating, setCreating] = useState(false);
  const [blocking, setBlocking] = useState<AdminUser | null>(null);
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
    const timer = setTimeout(loadUsers, 250);
    return () => clearTimeout(timer);
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
            <div className="stat-label">{t("admin.stats.users")}</div>
            <div className="stat-value">{stats.active}</div>
            <div className="small muted">{t("admin.stats.total", { n: stats.total })}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">{t("admin.stats.with2fa")}</div>
            <div className="stat-value">{stats.total ? Math.round((stats.with_2fa / stats.total) * 100) : 0}%</div>
            <div className="small muted">{t("admin.stats.users2fa", { n: stats.with_2fa })}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">{t("admin.stats.used")}</div>
            <div className="stat-value">{formatBytes(stats.used || 0)}</div>
            <div className="small muted">{t("admin.stats.allocated", { size: formatBytes(stats.allocated || 0) })}</div>
          </div>
          <div className="card stat">
            <div className="stat-label">{t("admin.stats.files")}</div>
            <div className="stat-value">{stats.files}</div>
            <div className={`small ${stats.infected ? "text-danger" : "muted"}`}>{t("admin.stats.blocked", { n: stats.infected })}</div>
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
        <button className={`tab ${tab === "backups" ? "active" : ""}`} onClick={() => setTab("backups")}>
          <Icon name="archive" size={16} /> {t("backup.tab")}
        </button>
      </div>

      {tab === "users" && (
        <>
          <div className="toolbar">
            <div className="search">
              <Icon name="search" size={16} />
              <input placeholder={t("admin.users.search")} value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              <Icon name="plus" size={16} /> {t("admin.users.add")}
            </button>
          </div>
          <div className="card table-card">
            <table className="table admin-users-table">
              <thead>
                <tr>
                  <th>{t("admin.users.colUser")}</th>
                  <th>{t("admin.users.colStorage")}</th>
                  <th>{t("admin.users.col2fa")}</th>
                  <th>{t("admin.users.colLastLogin")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {users.map((u) => {
                  const pct = u.quota_bytes ? (u.used_bytes / u.quota_bytes) * 100 : 0;
                  return (
                    <tr key={u.id} className={u.is_active ? "" : "inactive"}>
                      <td>
                        <div className="admin-user-cell">
                          <Avatar name={u.display_name || u.username} email={u.email} url={u.avatar_url} size={32} />
                          <div className="admin-user-text">
                            <div>
                              <strong>{u.username}</strong> {u.is_staff && <span className="pill">{t("admin.users.pillAdmin")}</span>}
                              {!u.is_active && <BlockedPill user={u} />}
                            </div>
                            <div className="small muted truncate">{u.email}</div>
                          </div>
                        </div>
                      </td>
                      <td className="quota-cell">
                        <div className="quota-track small-track">
                          <div className={`quota-fill ${pct > 90 ? "quota-danger" : pct > 75 ? "quota-warning" : "quota-ok"}`} style={{ width: `${Math.min(100, pct)}%` }} />
                        </div>
                        <div className="small muted">
                          {formatBytes(u.used_bytes)} / {formatBytes(u.quota_bytes)}
                        </div>
                      </td>
                      <td className="col-2fa">{u.has_2fa ? <span className="pill pill-success">{t("common.yes")}</span> : <span className="pill pill-warning">{t("common.no")}</span>}</td>
                      <td className="small muted col-last-login">{formatDate(u.last_login)}</td>
                      <td className="col-user-actions">
                        <div className="row gap-sm admin-user-actions">
                          <button className="btn btn-sm" onClick={() => setEditing(u)}>
                            {t("admin.users.manage")}
                          </button>
                          {u.id !== user?.id && (u.is_active ? (
                            <button className="btn btn-sm btn-danger-outline" onClick={() => setBlocking(u)}>
                              <Icon name="lock" size={14} /> {t("admin2.block.block")}
                            </button>
                          ) : (
                            <button
                              className="btn btn-sm"
                              onClick={async () => {
                                if (!window.confirm(t("admin2.block.unblockConfirm", { name: u.username }))) return;
                                try {
                                  await post(`/api/admin/users/${u.id}/unblock/`);
                                  toast(t("admin2.block.unblocked", { name: u.username }), "success");
                                  loadUsers();
                                } catch (err) {
                                  toast(errorText(err), "error");
                                }
                              }}
                            >
                              {t("admin2.block.unblock")}
                            </button>
                          ))}
                        </div>
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
      {tab === "backups" && <BackupsTab />}

      {editing && (
        <EditUserDialog
          user={editing}
          isSelf={editing.id === user?.id}
          onClose={() => setEditing(null)}
          onBlock={() => {
            setBlocking(editing);
            setEditing(null);
          }}
          onSaved={() => {
            setEditing(null);
            loadUsers();
            toast(t("common.saved"), "success");
          }}
        />
      )}
      {blocking && (
        <BlockUserDialog
          user={blocking}
          onClose={() => setBlocking(null)}
          onDone={() => {
            toast(t("admin2.block.blocked", { name: blocking.username }), "success");
            setBlocking(null);
            loadUsers();
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

function BlockedPill({ user }: { user: AdminUser }) {
  const t = useT();
  const tip = [
    user.blocked_reason ? t("admin2.block.reasonTip", { reason: user.blocked_reason }) : t("admin2.block.noReason"),
    user.blocked_at ? t("admin2.block.whenTip", { date: formatDate(user.blocked_at), by: user.blocked_by || "—" }) : "",
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <span className="pill pill-danger" title={tip} aria-label={`${t("admin.users.pillBlocked")}. ${tip}`} tabIndex={0}>
      {t("admin.users.pillBlocked")}
    </span>
  );
}

function BlockUserDialog({ user, onClose, onDone }: { user: AdminUser; onClose: () => void; onDone: () => void }) {
  const t = useT();
  const [reason, setReason] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!reason.trim()) return setError(t("admin2.block.reasonRequired"));
    if (!password) return setError(t("admin.passwordRequired"));
    setBusy(true);
    try {
      await post(`/api/admin/users/${user.id}/block/`, { reason: reason.trim(), password });
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("admin2.block.title", { name: user.username })} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p className="small">{t("admin2.block.explain")}</p>
        <label>
          {t("admin2.block.reason")}
          <textarea rows={3} maxLength={500} value={reason} autoFocus onChange={(e) => setReason(e.target.value)} />
          <span className="small muted">{t("admin2.block.reasonCount", { n: reason.length })}</span>
        </label>
        <label>
          {t("admin.yourPassword")}
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-danger" disabled={busy}>
            <Icon name="lock" size={16} /> {t("admin2.block.block")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditUserDialog({
  user,
  isSelf,
  onClose,
  onSaved,
  onBlock,
}: {
  user: AdminUser;
  isSelf: boolean;
  onClose: () => void;
  onSaved: () => void;
  onBlock: () => void;
}) {
  const [quotaGb, setQuotaGb] = useState(String(Math.round((user.quota_bytes / 1024 ** 3) * 100) / 100));
  const t = useT();
  const [error, setError] = useState("");

  const save = async (e: FormEvent) => {
    e.preventDefault();
    const q = Number(quotaGb);
    if (!Number.isFinite(q) || q < 0 || q > 100000) return setError(t("admin.edit.quotaInvalid"));
    try {
      await patch(`/api/admin/users/${user.id}/`, { quota_gb: q });
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const reset2fa = async () => {
    if (!window.confirm(t("admin.edit.reset2faConfirm", { name: user.username }))) return;
    try {
      await post(`/api/admin/users/${user.id}/reset_2fa/`);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const unblock = async () => {
    if (!window.confirm(t("admin2.block.unblockConfirm", { name: user.username }))) return;
    try {
      await post(`/api/admin/users/${user.id}/unblock/`);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={user.username} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <div className="admin-user-cell">
          <Avatar name={user.display_name || user.username} email={user.email} url={user.avatar_url} size={48} />
          <div className="admin-user-text">
            <strong className="truncate">{user.display_name || user.username}</strong>
            <span className="small muted truncate">{user.email}</span>
          </div>
        </div>
        <p className="small muted">
          {t("admin.edit.summary", { used: formatBytes(user.used_bytes), date: formatDate(user.date_joined, false) })}
        </p>
        <label>
          {t("admin.edit.quotaGb")}
          <input type="number" min={0} step="0.5" value={quotaGb} onChange={(e) => setQuotaGb(e.target.value)} />
        </label>
        {!isSelf && (
          <div className={`admin-block-box ${user.is_active ? "" : "is-blocked"}`}>
            {user.is_active ? (
              <>
                <div className="small muted">{t("admin2.block.activeHint")}</div>
                <button type="button" className="btn btn-danger-outline" onClick={onBlock}>
                  <Icon name="lock" size={16} /> {t("admin2.block.block")}
                </button>
              </>
            ) : (
              <>
                <div className="small">
                  <strong>{t("admin.users.pillBlocked")}</strong>
                  {user.blocked_at && <> · {t("admin2.block.whenTip", { date: formatDate(user.blocked_at), by: user.blocked_by || "—" })}</>}
                  <div className="break-anywhere">{user.blocked_reason ? t("admin2.block.reasonTip", { reason: user.blocked_reason }) : t("admin2.block.noReason")}</div>
                </div>
                <button type="button" className="btn" onClick={unblock}>
                  {t("admin2.block.unblock")}
                </button>
              </>
            )}
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          {user.has_2fa && !isSelf && (
            <button type="button" className="btn btn-danger" onClick={reset2fa}>
              {t("admin.edit.reset2fa")}
            </button>
          )}
          <div className="spacer" />
          <button className="btn btn-primary">{t("common.save")}</button>
        </div>
      </form>
    </Modal>
  );
}

function CreateUserDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const t = useT();
  const [form, setForm] = useState({ username: "", email: "", password: "", quota_gb: "10" });
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^[a-z0-9][a-z0-9._-]{2,39}$/.test(form.username)) return setError(t("admin.create.badUsername"));
    if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(form.email)) return setError(t("admin.create.badEmail"));
    if (form.password.length < 12) return setError(t("admin.create.shortPassword"));
    try {
      await post("/api/admin/users/", { ...form, quota_gb: Number(form.quota_gb) });
      onCreated();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Modal title={t("admin.create.title")} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <input placeholder={t("admin.create.username")} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value.toLowerCase() })} />
        <input placeholder={t("admin.create.email")} type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <input placeholder={t("admin.create.tempPassword")} type="password" autoComplete="new-password" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
        <label>
          {t("admin.edit.quotaGb")}
          <input type="number" min={0} value={form.quota_gb} onChange={(e) => setForm({ ...form, quota_gb: e.target.value })} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-primary">{t("common.create")}</button>
        </div>
      </form>
    </Modal>
  );
}

function InvitesTab() {
  const toast = useToast();
  const t = useT();
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
    if (!Number.isInteger(days) || days < 1 || days > 30) return setError(t("admin.invites.badDays"));
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
          {t("admin.invites.emailOptional")}
          <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        </label>
        <label>
          {t("admin.edit.quotaGb")}
          <input type="number" min={0} placeholder={t("admin.invites.quotaDefault")} value={form.quota_gb} onChange={(e) => setForm({ ...form, quota_gb: e.target.value })} />
        </label>
        <label>
          {t("admin.invites.days")}
          <input type="number" min={1} max={30} value={form.days} onChange={(e) => setForm({ ...form, days: e.target.value })} />
        </label>
        <button className="btn btn-primary">{t("admin.invites.create")}</button>
      </form>
      {error && <div className="form-error">{error}</div>}
      {created && (
        <div className="secret-box">
          <div className="small">{t("admin.invites.linkOnce")}</div>
          <div className="row-between">
            <code className="break">{created}</code>
            <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(created).then(() => toast(t("admin.invites.copied"), "success"))}>
              {t("admin.invites.copy")}
            </button>
          </div>
        </div>
      )}
      <div className="card table-card">
        <table className="table">
          <tbody>
            {invites.map((i) => (
              <tr key={i.id}>
                <td>{i.email || <span className="muted">{t("admin.invites.anyone")}</span>}</td>
                <td className="small muted">{t("admin.invites.until", { date: formatDate(i.expires_at) })}</td>
                <td>
                  {i.used_at ? <span className="pill">{t("admin.invites.used")}</span> : i.valid ? <span className="pill pill-success">{t("admin.invites.active")}</span> : <span className="pill">{t("admin.invites.expired")}</span>}
                </td>
                <td>
                  {!i.used_at && (
                    <button className="link-btn danger" onClick={() => del(`/api/admin/invites/${i.id}/`).then(load)}>
                      {t("common.delete")}
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
  const t = useT();
  const [rows, setRows] = useState<{ id: number; created_at: string; action: string; user: string | null; ip_address: string | null; target: string }[]>([]);
  const [filter, setFilter] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => {
      get<Paginated<(typeof rows)[number]>>(`/api/admin/audit/?page_size=200&action=${encodeURIComponent(filter)}`).then((r) => setRows(r.results));
    }, 250);
    return () => clearTimeout(timer);
  }, [filter]);
  return (
    <div className="stack">
      <div className="search">
        <Icon name="search" size={16} />
        <input placeholder={t("admin.audit.filter")} value={filter} onChange={(e) => setFilter(e.target.value)} />
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
