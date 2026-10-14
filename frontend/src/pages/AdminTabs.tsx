// Вкладки адмінки: карантин, антивірус, поштові скриньки.
// Усі права перевіряються на сервері (IsStaffWith2FA, обмеження для скриньок/файлів адміністраторів);
// інтерфейс лише ховає недоступне й просить підтвердження для незворотних дій.

import { FormEvent, useCallback, useEffect, useState } from "react";
import { ApiError, del, errorText, get, patch, post } from "../api/client";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { useT } from "../i18n";
import type { TKey } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";

interface QuarantinedFile {
  id: string;
  name: string;
  owner: string;
  size: number;
  mime_type: string;
  status: "infected" | "failed" | "unscanned" | "scanning";
  status_display: string;
  scan_detail: string;
  sha256: string;
  in_trash: boolean;
  created_at: string;
}

interface AntivirusStatus {
  enabled: boolean;
  reachable: boolean;
  version: string;
  error: string;
  unscanned_policy: string;
  max_scan_bytes: number;
  counts: Record<string, number>;
}

interface AdminMailbox {
  id: string;
  address: string;
  local_part: string;
  domain: string;
  display_name: string;
  user: string;
  quota_mb: number;
  active: boolean;
  client_password_set: boolean;
  created_at: string;
}

const STATUS_LABELS: Record<string, TKey> = {
  infected: "q.status.infected",
  failed: "q.status.failed",
  unscanned: "q.status.unscanned",
  scanning: "q.status.scanning",
  clean: "q.status.clean",
  uploading: "q.status.uploading",
};

/** Підтвердження незворотної дії паролем адміністратора. */
function PasswordConfirm({
  title,
  text,
  action,
  danger,
  onConfirm,
  onClose,
}: {
  title: string;
  text: string;
  action: string;
  danger?: boolean;
  onConfirm: (password: string) => Promise<void>;
  onClose: () => void;
}) {
  const t = useT();
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password) return setError(t("admin.passwordRequired"));
    setBusy(true);
    setError("");
    try {
      await onConfirm(password);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={title} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <p>{text}</p>
        <label>
          {t("admin.yourPassword")}
          <input type="password" autoComplete="current-password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={busy}>
            {action}
          </button>
        </div>
      </form>
    </Modal>
  );
}

export function QuarantineTab() {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<QuarantinedFile[] | null>(null);
  const [status, setStatus] = useState("");
  const [error, setError] = useState("");
  const [purging, setPurging] = useState<QuarantinedFile | null>(null);
  const [details, setDetails] = useState<QuarantinedFile | null>(null);

  const load = useCallback(() => {
    setError("");
    get<QuarantinedFile[]>(`/api/admin/quarantine/${status ? `?status=${status}` : ""}`)
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const rescan = async (f: QuarantinedFile) => {
    try {
      await post(`/api/admin/quarantine/${f.id}/rescan/`);
      toast(t("q.rescanQueued"), "success");
      load();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  return (
    <div className="stack">
      <div className="banner banner-info small">{t("q.explain")}</div>
      <div className="toolbar">
        <select className="select-sm" value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("q.filter")}>
          <option value="">{t("q.all")}</option>
          <option value="infected">{t("q.status.infected")}</option>
          <option value="failed">{t("q.status.failed")}</option>
          <option value="unscanned">{t("q.status.unscanned")}</option>
        </select>
        <button className="icon-btn" onClick={load} aria-label={t("common.refresh")} title={t("common.refresh")}>
          <Icon name="refresh" />
        </button>
      </div>
      {error && <div className="banner banner-warning">{error}</div>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>{t("q.file")}</th>
              <th>{t("q.owner")}</th>
              <th>{t("q.status")}</th>
              <th>{t("q.uploaded")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items?.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-small">
                  {t("q.empty")}
                </td>
              </tr>
            )}
            {items?.map((f) => (
              <tr key={f.id}>
                <td>
                  <div className="truncate">
                    <strong>{f.name}</strong>
                  </div>
                  <div className="small muted">
                    {formatBytes(f.size)} · {f.mime_type || "?"}
                    {f.in_trash && ` · ${t("q.inTrash")}`}
                  </div>
                </td>
                <td>{f.owner}</td>
                <td>
                  <span className={`pill ${f.status === "infected" ? "pill-danger" : "pill-warning"}`}>
                    {STATUS_LABELS[f.status] ? t(STATUS_LABELS[f.status]) : f.status_display}
                  </span>
                  {f.scan_detail && <div className="small muted truncate" title={f.scan_detail}>{f.scan_detail}</div>}
                </td>
                <td className="small muted">{formatDate(f.created_at)}</td>
                <td>
                  <div className="row gap-sm">
                    <button className="btn btn-sm" onClick={() => setDetails(f)}>
                      {t("q.details")}
                    </button>
                    <button className="btn btn-sm" onClick={() => rescan(f)}>
                      {t("q.rescan")}
                    </button>
                    <button className="btn btn-sm btn-danger" onClick={() => setPurging(f)}>
                      {t("q.purge")}
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">{t("q.noRelease")}</p>

      {details && (
        <Modal title={details.name} onClose={() => setDetails(null)}>
          <dl className="info-list">
            <dt>{t("q.owner")}</dt>
            <dd>{details.owner}</dd>
            <dt>{t("q.status")}</dt>
            <dd>{STATUS_LABELS[details.status] ? t(STATUS_LABELS[details.status]) : details.status_display}</dd>
            <dt>{t("q.signature")}</dt>
            <dd>{details.scan_detail || "—"}</dd>
            <dt>{t("q.size")}</dt>
            <dd>{formatBytes(details.size)}</dd>
            <dt>MIME</dt>
            <dd>{details.mime_type || "—"}</dd>
            <dt>SHA-256</dt>
            <dd className="mono small break">{details.sha256 || "—"}</dd>
            <dt>{t("q.uploaded")}</dt>
            <dd>{formatDate(details.created_at)}</dd>
          </dl>
        </Modal>
      )}
      {purging && (
        <PasswordConfirm
          title={t("q.purgeTitle")}
          text={t("q.purgeText", { name: purging.name, owner: purging.owner })}
          action={t("q.purge")}
          danger
          onClose={() => setPurging(null)}
          onConfirm={async (password) => {
            await post(`/api/admin/quarantine/${purging.id}/purge/`, { password });
            setPurging(null);
            toast(t("q.purged"), "success");
            load();
          }}
        />
      )}
    </div>
  );
}

export function AntivirusTab() {
  const t = useT();
  const toast = useToast();
  const [av, setAv] = useState<AntivirusStatus | null>(null);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);

  const load = useCallback(() => {
    setError("");
    get<AntivirusStatus>("/api/admin/antivirus/")
      .then(setAv)
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <div className="banner banner-warning">{error}</div>;
  if (!av) return <div className="empty-small">{t("common.loading")}</div>;

  const state = !av.enabled ? "off" : av.reachable ? "ok" : "down";
  return (
    <div className="settings-grid">
      <div className="card stack">
        <div className="row-between">
          <h3>ClamAV</h3>
          <button className="icon-btn" onClick={load} aria-label={t("common.refresh")} title={t("common.refresh")}>
            <Icon name="refresh" />
          </button>
        </div>
        <div>
          {state === "ok" && <span className="pill pill-success">{t("av.ok")}</span>}
          {state === "down" && <span className="pill pill-danger">{t("av.down")}</span>}
          {state === "off" && <span className="pill pill-warning">{t("av.off")}</span>}
        </div>
        <dl className="info-list">
          <dt>{t("av.version")}</dt>
          <dd className="small">{av.version || "—"}</dd>
          <dt>{t("av.policy")}</dt>
          <dd>{av.unscanned_policy === "block" ? t("av.policyBlock") : t("av.policyAllow", { policy: av.unscanned_policy })}</dd>
          <dt>{t("av.maxSize")}</dt>
          <dd>{formatBytes(av.max_scan_bytes)}</dd>
          {av.error && (
            <>
              <dt>{t("av.error")}</dt>
              <dd className="text-danger small">{av.error}</dd>
            </>
          )}
        </dl>
        <p className="small muted">{t("av.configOnly")}</p>
      </div>
      <div className="card stack">
        <h3>{t("av.files")}</h3>
        <ul className="plain-list">
          {Object.entries(av.counts).map(([k, v]) => (
            <li key={k} className="row-between">
              <span>{STATUS_LABELS[k] ? t(STATUS_LABELS[k]) : k}</span>
              <span className={k === "infected" && v ? "text-danger" : "muted"}>{v}</span>
            </li>
          ))}
        </ul>
        <div>
          <button className="btn" disabled={!av.enabled || !av.counts.failed} onClick={() => setConfirming(true)}>
            <Icon name="refresh" size={16} /> {t("av.rescanFailed", { n: av.counts.failed || 0 })}
          </button>
        </div>
        <p className="small muted">{t("av.rescanHint")}</p>
      </div>
      {confirming && (
        <PasswordConfirm
          title={t("av.rescanTitle")}
          text={t("av.rescanText", { n: av.counts.failed || 0 })}
          action={t("q.rescan")}
          onClose={() => setConfirming(false)}
          onConfirm={async (password) => {
            const r = await post<{ queued: number }>("/api/admin/antivirus/rescan-failed/", { password });
            setConfirming(false);
            toast(t("av.queued", { n: r.queued }), "success");
            load();
          }}
        />
      )}
    </div>
  );
}

export function MailboxesTab() {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<AdminMailbox[] | null>(null);
  const [filter, setFilter] = useState("");
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<AdminMailbox | null>(null);
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    get<AdminMailbox[]>("/api/admin/mailboxes/")
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const q = filter.trim().toLowerCase();
  const shown = (items || []).filter((m) => !q || m.address.includes(q) || m.user.toLowerCase().includes(q) || m.display_name.toLowerCase().includes(q));

  return (
    <div className="stack">
      <div className="toolbar">
        <div className="search">
          <Icon name="search" size={16} />
          <input placeholder={t("mb.search")} value={filter} onChange={(e) => setFilter(e.target.value)} />
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>
          <Icon name="plus" size={16} /> {t("mb.new")}
        </button>
      </div>
      {error && <div className="banner banner-warning">{error}</div>}
      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>{t("mb.address")}</th>
              <th>{t("mb.user")}</th>
              <th>{t("mb.quota")}</th>
              <th>{t("mb.clients")}</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {items && shown.length === 0 && (
              <tr>
                <td colSpan={5} className="empty-small">
                  {t("mb.empty")}
                </td>
              </tr>
            )}
            {shown.map((m) => (
              <tr key={m.id} className={m.active ? "" : "inactive"}>
                <td>
                  <strong>{m.address}</strong> {!m.active && <span className="pill pill-danger">{t("mb.disabled")}</span>}
                  {m.display_name && <div className="small muted">{m.display_name}</div>}
                </td>
                <td>{m.user}</td>
                <td className="small">{m.quota_mb} MB</td>
                <td>{m.client_password_set ? <span className="pill pill-success">{t("common.yes")}</span> : <span className="pill">{t("common.no")}</span>}</td>
                <td>
                  <button className="btn btn-sm" onClick={() => setEditing(m)}>
                    {t("mb.manage")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">{t("mb.hint")}</p>
      {creating && (
        <CreateMailboxDialog
          onClose={() => setCreating(false)}
          onCreated={() => {
            setCreating(false);
            toast(t("mb.created"), "success");
            load();
          }}
        />
      )}
      {editing && (
        <EditMailboxDialog
          mailbox={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            toast(t("common.saved"), "success");
            load();
          }}
          onDeleted={() => {
            toast(t("admin2.mbDelete.done", { address: editing.address }), "success");
            setEditing(null);
            load();
          }}
        />
      )}
    </div>
  );
}

// Те саме правило, що й LOCAL_PART_VALIDATOR на сервері (після переведення в нижній регістр)
const LOCAL_PART_RE = /^[a-z0-9][a-z0-9._+-]{0,63}$/;

function CreateMailboxDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const t = useT();
  const [form, setForm] = useState({ username: "", local_part: "", display_name: "", quota_mb: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.username.trim()) return setError(t("mb.userRequired"));
    if (!LOCAL_PART_RE.test(form.local_part.trim().toLowerCase())) return setError(t("mb.badLocalPart"));
    setBusy(true);
    try {
      await post("/api/admin/mailboxes/", {
        username: form.username.trim(),
        local_part: form.local_part.trim().toLowerCase(),
        display_name: form.display_name.trim(),
        ...(form.quota_mb ? { quota_mb: Number(form.quota_mb) } : {}),
      });
      onCreated();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  return (
    <Modal title={t("mb.new")} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label>
          {t("mb.owner")}
          <input value={form.username} onChange={set("username")} autoFocus placeholder="username" />
        </label>
        <label>
          {t("mb.localPart")}
          <input value={form.local_part} onChange={set("local_part")} placeholder="sales" maxLength={64} />
        </label>
        <label>
          {t("mb.displayName")}
          <input value={form.display_name} onChange={set("display_name")} maxLength={100} />
        </label>
        <label>
          {t("mb.quotaMb")}
          <input type="number" min={10} value={form.quota_mb} onChange={set("quota_mb")} placeholder="1024" />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {t("common.create")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function EditMailboxDialog({
  mailbox,
  onClose,
  onSaved,
  onDeleted,
}: {
  mailbox: AdminMailbox;
  onClose: () => void;
  onSaved: () => void;
  onDeleted: () => void;
}) {
  const t = useT();
  const [deleting, setDeleting] = useState(false);
  const [localPart, setLocalPart] = useState(mailbox.local_part);
  const [displayName, setDisplayName] = useState(mailbox.display_name);
  const [quota, setQuota] = useState(String(mailbox.quota_mb));
  const [active, setActive] = useState(mailbox.active);
  const [keepAlias, setKeepAlias] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const renamed = localPart.trim().toLowerCase() !== mailbox.local_part;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!LOCAL_PART_RE.test(localPart.trim().toLowerCase())) return setError(t("mb.badLocalPart"));
    if (renamed && !window.confirm(t("mb.confirmRename", { from: mailbox.address, to: `${localPart.trim().toLowerCase()}@${mailbox.domain}` }))) return;
    if (!active && mailbox.active && !window.confirm(t("mb.confirmDisable", { address: mailbox.address }))) return;
    setBusy(true);
    try {
      await patch(`/api/admin/mailboxes/${mailbox.id}/`, {
        ...(renamed ? { local_part: localPart.trim().toLowerCase(), keep_old_alias: keepAlias } : {}),
        display_name: displayName.trim(),
        quota_mb: Number(quota),
        active,
      });
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  // Підтвердження видалення замінює вікно редагування (а не відкривається поверх нього)
  if (deleting) return <DeleteMailboxDialog mailbox={mailbox} onClose={() => setDeleting(false)} onDeleted={onDeleted} />;

  return (
    <Modal title={mailbox.address} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <label>
          {t("mb.localPart")}
          <div className="row gap-sm">
            <input className="grow" value={localPart} onChange={(e) => setLocalPart(e.target.value)} maxLength={64} />
            <span className="muted">@{mailbox.domain}</span>
          </div>
        </label>
        {renamed && (
          <label className="check">
            <input type="checkbox" checked={keepAlias} onChange={(e) => setKeepAlias(e.target.checked)} />
            {t("mb.keepAlias", { address: mailbox.address })}
          </label>
        )}
        <label>
          {t("mb.displayName")}
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={100} />
        </label>
        <label>
          {t("mb.quotaMb")}
          <input type="number" min={10} value={quota} onChange={(e) => setQuota(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />
          {t("mb.active")}
        </label>
        <p className="small muted">{t("mb.renameNote")}</p>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn btn-danger-outline" onClick={() => setDeleting(true)}>
            <Icon name="trash" size={16} /> {t("admin2.mbDelete.button")}
          </button>
          <div className="spacer" />
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-primary" disabled={busy}>
            {t("common.save")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** Видалення скриньки: пароль адміністратора + введення адреси. Листи стираються через IMAP;
 * якщо поштовий сервер недоступний, сервер відмовляє (503), і лише суперкористувач може
 * видалити запис примусово. */
function DeleteMailboxDialog({ mailbox, onClose, onDeleted }: { mailbox: AdminMailbox; onClose: () => void; onDeleted: () => void }) {
  const t = useT();
  const [confirm, setConfirm] = useState("");
  const [password, setPassword] = useState("");
  const [force, setForce] = useState(false);
  const [imapDown, setImapDown] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const matches = confirm.trim().toLowerCase() === mailbox.address.toLowerCase();

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!matches) return setError(t("admin2.mbDelete.typeAddress"));
    if (!password) return setError(t("admin.passwordRequired"));
    setBusy(true);
    try {
      await del(`/api/admin/mailboxes/${mailbox.id}/`, { password, confirm: confirm.trim(), force });
      onDeleted();
    } catch (err) {
      if (err instanceof ApiError && err.status === 503) setImapDown(true);
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("admin2.mbDelete.title")} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <div className="banner banner-danger">{t("admin2.mbDelete.warning", { address: mailbox.address, user: mailbox.user })}</div>
        <ul className="small muted mb-delete-list">
          <li>{t("admin2.mbDelete.what1")}</li>
          <li>{t("admin2.mbDelete.what2")}</li>
          <li>{t("admin2.mbDelete.what3")}</li>
        </ul>
        <label>
          {t("admin2.mbDelete.typeLabel", { address: mailbox.address })}
          <input value={confirm} autoFocus autoComplete="off" spellCheck={false} onChange={(e) => setConfirm(e.target.value)} />
        </label>
        <label>
          {t("admin.yourPassword")}
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {imapDown && (
          <label className="check">
            <input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} />
            {t("admin2.mbDelete.force")}
          </label>
        )}
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-danger" disabled={busy || !matches || !password}>
            <Icon name="trash" size={16} /> {busy ? t("admin2.mbDelete.deleting") : t("admin2.mbDelete.button")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

// ═══════════════════════════ Резервні копії ═══════════════════════════
// Копії робить сервер (systemd + restic, scripts/backup.sh). Адмінка лише показує стан
// зі status.json і може поставити запит «зробити копію зараз» — відновлення й видалення
// копій свідомо доступні тільки з терміналу сервера.

interface BackupRun {
  status: "ok" | "failed" | "running";
  started_at?: string;
  finished_at?: string;
  duration_s?: number;
  trigger?: "schedule" | "manual";
  requested_by?: string;
  message?: string;
}

interface BackupSnapshot {
  id: string;
  time: string;
  data_added: number | null;
  files_new: number | null;
  files_changed: number | null;
}

interface BackupStatusData {
  last_backup?: BackupRun;
  last_check?: BackupRun;
  last_success_at?: string;
  snapshots?: BackupSnapshot[];
  snapshot_count?: number;
  repo_size_bytes?: number | null;
  schedule?: { next_backup: string | null; next_check: string | null };
  retention?: { keep_daily: number; keep_weekly: number; keep_monthly: number };
  updated_at?: string;
}

interface BackupResponse {
  configured: boolean;
  status: BackupStatusData | null;
  stale: boolean | null;
  running: boolean;
  request_pending: boolean;
  can_request: boolean;
}

export function BackupsTab() {
  const t = useT();
  const toast = useToast();
  const [data, setData] = useState<BackupResponse | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    get<BackupResponse>("/api/admin/backups/")
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((e) => setError(errorText(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Поки копія виконується або чекає запуску — оновлюємо стан кожні 5 с
  const active = !!data && (data.running || data.request_pending);
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(load, 5000);
    return () => window.clearInterval(timer);
  }, [active, load]);

  const runNow = async () => {
    setBusy(true);
    try {
      await post("/api/admin/backups/run/");
      toast(t("backup.requested"), "success");
      load();
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setBusy(false);
    }
  };

  if (error && !data) return <div className="banner banner-warning">{error}</div>;
  if (!data) return <div className="empty-small">{t("common.loading")}</div>;
  if (!data.configured || !data.status) return <div className="banner banner-info">{t("backup.notConfigured")}</div>;

  const s = data.status;
  const last = s.last_backup;
  const check = s.last_check;
  const state = data.running ? "running" : last?.status === "failed" ? "failed" : data.stale ? "stale" : "ok";
  const pill = { ok: "pill-success", running: "pill-warning", stale: "pill-warning", failed: "pill-danger" }[state];
  const trigger =
    last?.trigger === "manual" ? t("backup.trigger.manual", { user: last.requested_by || "—" }) : t("backup.trigger.schedule");
  const retention = s.retention;

  return (
    <div className="stack">
      {error && <div className="banner banner-warning">{error}</div>}
      <div className="settings-grid">
        <div className="card stack">
          <div className="row-between">
            <h3>{t("backup.title")}</h3>
            <button className="icon-btn" onClick={load} aria-label={t("common.refresh")} title={t("common.refresh")}>
              <Icon name="refresh" />
            </button>
          </div>
          <div>
            <span className={`pill ${pill}`}>{t(`backup.state.${state}` as TKey)}</span>
          </div>
          <dl className="info-list">
            <dt>{t("backup.lastSuccess")}</dt>
            <dd>{s.last_success_at ? formatDate(s.last_success_at) : t("backup.never")}</dd>
            {last?.started_at && (
              <>
                <dt>{t("backup.lastRun")}</dt>
                <dd>
                  {formatDate(last.started_at)} · {trigger}
                  {typeof last.duration_s === "number" && ` · ${t("backup.seconds", { n: last.duration_s })}`}
                </dd>
              </>
            )}
            {last?.status === "failed" && last.message && (
              <>
                <dt>{t("backup.error")}</dt>
                <dd className="text-danger small backup-error">{last.message}</dd>
              </>
            )}
            <dt>{t("backup.next")}</dt>
            <dd>{s.schedule?.next_backup ? formatDate(s.schedule.next_backup) : "—"}</dd>
            <dt>{t("backup.size")}</dt>
            <dd>{typeof s.repo_size_bytes === "number" ? formatBytes(s.repo_size_bytes) : "—"}</dd>
            <dt>{t("backup.count")}</dt>
            <dd>{s.snapshot_count ?? "—"}</dd>
            {retention && (
              <>
                <dt>{t("backup.retention")}</dt>
                <dd>{t("backup.retentionValue", { d: retention.keep_daily, w: retention.keep_weekly, m: retention.keep_monthly })}</dd>
              </>
            )}
          </dl>
          <div>
            <button className="btn btn-primary" onClick={runNow} disabled={busy || active || !data.can_request}>
              <Icon name="upload" size={16} /> {data.running ? t("backup.state.running") : data.request_pending ? t("backup.pending") : t("backup.runNow")}
            </button>
          </div>
          <p className="small muted">{t("backup.runHint")}</p>
        </div>

        <div className="card stack">
          <h3>{t("backup.check.title")}</h3>
          <div>
            {!check && <span className="pill">{t("backup.check.never")}</span>}
            {check?.status === "ok" && <span className="pill pill-success">{t("backup.check.ok")}</span>}
            {check?.status === "failed" && <span className="pill pill-danger">{t("backup.check.failed")}</span>}
            {check?.status === "running" && <span className="pill pill-warning">{t("backup.check.running")}</span>}
          </div>
          <dl className="info-list">
            {check?.started_at && (
              <>
                <dt>{t("backup.check.last")}</dt>
                <dd>{formatDate(check.started_at)}</dd>
              </>
            )}
            {check?.message && (
              <>
                <dt>{check.status === "failed" ? t("backup.error") : t("backup.check.result")}</dt>
                <dd className={check.status === "failed" ? "text-danger small backup-error" : "small"}>{check.message}</dd>
              </>
            )}
            <dt>{t("backup.check.next")}</dt>
            <dd>{s.schedule?.next_check ? formatDate(s.schedule.next_check) : "—"}</dd>
          </dl>
          <p className="small muted">{t("backup.check.hint")}</p>
        </div>
      </div>

      <div className="card table-card">
        <table className="table">
          <thead>
            <tr>
              <th>{t("backup.col.date")}</th>
              <th>ID</th>
              <th>{t("backup.col.added")}</th>
              <th>{t("backup.col.files")}</th>
            </tr>
          </thead>
          <tbody>
            {(s.snapshots ?? []).length === 0 && (
              <tr>
                <td colSpan={4} className="empty-small">
                  {t("backup.historyEmpty")}
                </td>
              </tr>
            )}
            {(s.snapshots ?? []).map((snap) => (
              <tr key={snap.id + snap.time}>
                <td>{formatDate(snap.time)}</td>
                <td className="mono small">{snap.id}</td>
                <td>{typeof snap.data_added === "number" ? formatBytes(snap.data_added) : "—"}</td>
                <td className="muted">
                  {snap.files_new ?? "—"} / {snap.files_changed ?? "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="small muted">
        {t("backup.restoreNote")}
        {s.updated_at && ` ${t("backup.updated", { when: formatDate(s.updated_at) })}`}
      </p>
    </div>
  );
}
