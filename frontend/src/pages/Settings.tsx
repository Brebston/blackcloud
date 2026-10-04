import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { Preferences } from "../api/types";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { formatBytes, formatDate } from "../lib/format";

const TABS = [
  { id: "profile", label: "Профіль", icon: "user" },
  { id: "security", label: "Безпека", icon: "shield" },
  { id: "sessions", label: "Пристрої", icon: "lock" },
  { id: "mail", label: "Поштові клієнти", icon: "mail" },
  { id: "activity", label: "Журнал", icon: "eye" },
];

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "profile";
  return (
    <div className="page">
      <div className="page-head">
        <h2>Налаштування</h2>
      </div>
      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.id} className={`tab ${tab === t.id ? "active" : ""}`} onClick={() => setParams({ tab: t.id })}>
            <Icon name={t.icon} size={16} /> {t.label}
          </button>
        ))}
      </div>
      {tab === "profile" && <ProfileTab />}
      {tab === "security" && <SecurityTab />}
      {tab === "sessions" && <SessionsTab />}
      {tab === "mail" && <MailClientTab />}
      {tab === "activity" && <ActivityTab />}
    </div>
  );
}

function ProfileTab() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [displayName, setDisplayName] = useState(user?.display_name || "");
  const [prefs, setPrefs] = useState<Preferences | null>(user?.preferences || null);
  const [usage, setUsage] = useState<{ quota_bytes: number; used_bytes: number; trash_bytes: number; by_type: Record<string, number> } | null>(null);

  useEffect(() => {
    get<typeof usage>("/api/files/usage/").then(setUsage).catch(() => {});
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await patch("/api/account/profile/", { display_name: displayName.trim().slice(0, 100) });
      if (prefs) await patch("/api/account/preferences/", prefs);
      await refresh();
      toast("Збережено", "success");
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  if (!user || !prefs) return null;
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });
  const TYPE_LABELS: Record<string, string> = { image: "Зображення", video: "Відео", audio: "Аудіо", application: "Документи й архіви", text: "Текст" };

  return (
    <div className="settings-grid">
      <form className="card stack" onSubmit={save}>
        <h3>Профіль</h3>
        <label>
          Ім'я користувача
          <input value={user.username} disabled />
        </label>
        <label>
          Email
          <input value={user.email} disabled />
        </label>
        <label>
          Відображуване ім'я
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={100} />
        </label>
        <h3>Вподобання</h3>
        <div className="grid-2">
          <label>
            Тема
            <select value={prefs.theme} onChange={(e) => set("theme", e.target.value as Preferences["theme"])}>
              <option value="system">Системна</option>
              <option value="dark">Темна</option>
              <option value="light">Світла</option>
            </select>
          </label>
          <label>
            Мова
            <select value={prefs.language} onChange={(e) => set("language", e.target.value as Preferences["language"])}>
              <option value="uk">Українська</option>
              <option value="en">English</option>
            </select>
          </label>
          <label>
            Часовий пояс
            <input value={prefs.timezone} onChange={(e) => set("timezone", e.target.value)} />
          </label>
          <label>
            Перший день тижня
            <select value={prefs.week_starts_monday ? "1" : "0"} onChange={(e) => set("week_starts_monday", e.target.value === "1")}>
              <option value="1">Понеділок</option>
              <option value="0">Неділя</option>
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={prefs.discoverable} onChange={(e) => set("discoverable", e.target.checked)} />
          Інші користувачі можуть знайти мене в пошуку
        </label>
        <label className="check">
          <input type="checkbox" checked={prefs.mail_load_remote_images} onChange={(e) => set("mail_load_remote_images", e.target.checked)} />
          Завжди завантажувати віддалені зображення в листах (менш приватно)
        </label>
        <div>
          <button className="btn btn-primary">Зберегти</button>
        </div>
      </form>

      {usage && (
        <div className="card stack">
          <h3>Сховище</h3>
          <div className="big-number">
            {formatBytes(usage.used_bytes)} <span className="muted">з {formatBytes(usage.quota_bytes)}</span>
          </div>
          <ul className="plain-list">
            {Object.entries(usage.by_type).map(([k, v]) => (
              <li key={k} className="row-between">
                <span>{TYPE_LABELS[k] || k}</span>
                <span className="muted">{formatBytes(v)}</span>
              </li>
            ))}
            <li className="row-between">
              <span>Кошик</span>
              <span className="muted">{formatBytes(usage.trash_bytes)}</span>
            </li>
          </ul>
          <p className="small muted">Квоту встановлює адміністратор.</p>
        </div>
      )}
    </div>
  );
}

function SecurityTab() {
  const { user, refresh } = useAuth();
  const toast = useToast();
  const [info, setInfo] = useState<{ has_2fa: boolean; backup_codes_remaining: number; password_changed_at: string | null } | null>(null);
  const [pw, setPw] = useState({ current: "", next: "", next2: "" });
  const [pwError, setPwError] = useState("");
  const [setup, setSetup] = useState<{ secret: string; qr_svg: string } | null>(null);
  const [confirmPw, setConfirmPw] = useState("");
  const [code, setCode] = useState("");
  const [codes, setCodes] = useState<string[] | null>(null);
  const [error, setError] = useState("");

  const load = () => get<NonNullable<typeof info>>("/api/account/security/").then(setInfo);
  useEffect(() => {
    load();
  }, []);

  const changePassword = async (e: FormEvent) => {
    e.preventDefault();
    setPwError("");
    if (pw.next.length < 12) return setPwError("Новий пароль — мінімум 12 символів.");
    if (pw.next !== pw.next2) return setPwError("Паролі не збігаються.");
    try {
      await post("/api/account/password/", { current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", next2: "" });
      toast("Пароль змінено. Інші сесії завершено.", "success");
      load();
    } catch (err) {
      setPwError(errorText(err));
    }
  };

  const startSetup = async () => {
    setError("");
    if (!confirmPw) return setError("Підтвердіть пароль.");
    try {
      setSetup(await post("/api/account/security/totp/setup/", { password: confirmPw }));
      setConfirmPw("");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const confirmSetup = async () => {
    setError("");
    if (!/^\d{6}$/.test(code.trim())) return setError("Введіть 6-значний код.");
    try {
      const r = await post<{ backup_codes: string[] }>("/api/account/security/totp/confirm/", { code: code.trim() });
      setCodes(r.backup_codes);
      setSetup(null);
      setCode("");
      await refresh();
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const disable = async () => {
    setError("");
    if (!confirmPw || !code) return setError("Потрібні пароль і код 2FA.");
    try {
      await post("/api/account/security/2fa/disable/", { password: confirmPw, code: code.trim() });
      setConfirmPw("");
      setCode("");
      await refresh();
      load();
      toast("2FA вимкнено", "info");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const regenerate = async () => {
    setError("");
    if (!confirmPw) return setError("Підтвердіть пароль.");
    try {
      const r = await post<{ backup_codes: string[] }>("/api/account/security/backup-codes/", { password: confirmPw });
      setCodes(r.backup_codes);
      setConfirmPw("");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  if (!info || !user) return null;

  return (
    <div className="settings-grid">
      <div className="card stack">
        <h3>
          <Icon name="shield" /> Двофакторна автентифікація
        </h3>
        {info.has_2fa ? (
          <p>
            <span className="pill pill-success">Увімкнено</span> Залишилось резервних кодів: <strong>{info.backup_codes_remaining}</strong>
          </p>
        ) : (
          <p className="muted">
            Додайте другий фактор: застосунок-автентифікатор (Aegis, Google Authenticator, 1Password, Bitwarden тощо).
          </p>
        )}

        {codes && (
          <div className="secret-box">
            <strong>Резервні коди — збережіть їх зараз, вони більше не показуватимуться:</strong>
            <div className="codes">
              {codes.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </div>
            <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(codes.join("\n")).then(() => toast("Скопійовано", "success"))}>
              Копіювати
            </button>
          </div>
        )}

        {setup ? (
          <div className="stack">
            <p>1. Відскануйте QR-код у застосунку-автентифікаторі:</p>
            <img className="qr" src={setup.qr_svg} alt="QR-код для 2FA" />
            <details>
              <summary className="small">Не вдається відсканувати? Введіть ключ вручну</summary>
              <code className="break">{setup.secret}</code>
            </details>
            <p>2. Введіть код із застосунку:</p>
            <input inputMode="numeric" maxLength={6} className="code-input" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            <div className="row gap">
              <button className="btn btn-primary" onClick={confirmSetup}>
                Увімкнути 2FA
              </button>
              <button className="btn" onClick={() => setSetup(null)}>
                Скасувати
              </button>
            </div>
          </div>
        ) : (
          <div className="stack">
            <label>
              Поточний пароль
              <input type="password" autoComplete="current-password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
            </label>
            {info.has_2fa && (
              <label>
                Код 2FA (для вимкнення)
                <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
              </label>
            )}
            <div className="row gap wrap">
              {!info.has_2fa && (
                <button className="btn btn-primary" onClick={startSetup}>
                  Налаштувати 2FA
                </button>
              )}
              {info.has_2fa && (
                <>
                  <button className="btn" onClick={regenerate}>
                    Нові резервні коди
                  </button>
                  <button className="btn btn-danger" onClick={disable}>
                    Вимкнути 2FA
                  </button>
                </>
              )}
            </div>
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
      </div>

      <form className="card stack" onSubmit={changePassword}>
        <h3>
          <Icon name="lock" /> Пароль
        </h3>
        <p className="small muted">Змінено: {formatDate(info.password_changed_at)}</p>
        <label>
          Поточний пароль
          <input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
        </label>
        <label>
          Новий пароль (мінімум 12 символів)
          <input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
        </label>
        <label>
          Повторіть новий пароль
          <input type="password" autoComplete="new-password" value={pw.next2} onChange={(e) => setPw({ ...pw, next2: e.target.value })} />
        </label>
        {pwError && <div className="form-error">{pwError}</div>}
        <div>
          <button className="btn btn-primary">Змінити пароль</button>
        </div>
      </form>
    </div>
  );
}

interface SessionInfo {
  id: string;
  ip_address: string | null;
  user_agent: string;
  created_at: string;
  last_seen: string;
  current: boolean;
}

function SessionsTab() {
  const toast = useToast();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const load = () => get<SessionInfo[]>("/api/account/sessions/").then(setSessions);
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="card stack">
      <div className="row-between">
        <h3>Активні сесії</h3>
        <button
          className="btn btn-sm"
          onClick={async () => {
            const r = await post<{ revoked: number }>("/api/account/sessions/revoke_others/");
            toast(`Завершено сесій: ${r.revoked}`, "success");
            load();
          }}
        >
          Вийти на всіх інших пристроях
        </button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>Пристрій</th>
            <th>IP</th>
            <th>Активність</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td className="small">
                {s.user_agent.slice(0, 90) || "Невідомо"} {s.current && <span className="pill pill-success">ця сесія</span>}
              </td>
              <td className="small">{s.ip_address}</td>
              <td className="small muted">{formatDate(s.last_seen)}</td>
              <td>
                {!s.current && (
                  <button className="link-btn danger" onClick={() => del(`/api/account/sessions/${s.id}/`).then(load)}>
                    Завершити
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MailClientTab() {
  const toast = useToast();
  const [mb, setMb] = useState<{
    address: string;
    quota_mb: number;
    client_password_set_at: string | null;
    imap: { host: string; port: number; security: string };
    smtp: { host: string; port: number; security: string };
  } | null>(null);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [generated, setGenerated] = useState<string | null>(null);

  const load = () => get<NonNullable<typeof mb>>("/api/mail/mailbox/").then(setMb).catch((e) => setError(errorText(e)));
  useEffect(() => {
    load();
  }, []);

  const generate = async (revoke = false) => {
    setError("");
    if (!password) return setError("Підтвердіть пароль акаунта.");
    try {
      const r = await post<{ password?: string }>("/api/mail/mailbox/client-password/", { password, revoke });
      setGenerated(r.password || null);
      setPassword("");
      if (revoke) toast("Доступ поштових клієнтів вимкнено", "info");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  if (error && !mb) return <div className="card">{error}</div>;
  if (!mb) return null;

  return (
    <div className="settings-grid">
      <div className="card stack">
        <h3>Підключення поштового клієнта</h3>
        <dl className="info-list">
          <dt>Адреса</dt>
          <dd>{mb.address}</dd>
          <dt>IMAP</dt>
          <dd>
            {mb.imap.host}:{mb.imap.port} ({mb.imap.security})
          </dd>
          <dt>SMTP</dt>
          <dd>
            {mb.smtp.host}:{mb.smtp.port} ({mb.smtp.security}) або 587 (STARTTLS)
          </dd>
          <dt>Логін</dt>
          <dd>{mb.address}</dd>
          <dt>Квота</dt>
          <dd>{mb.quota_mb} МБ</dd>
        </dl>
      </div>
      <div className="card stack">
        <h3>Пароль застосунку</h3>
        <p className="small muted">
          Поштові клієнти (Thunderbird, Apple Mail, телефон) не підтримують 2FA, тому для них використовується окремий
          згенерований пароль. Пароль акаунта для пошти не підходить. Статус:{" "}
          {mb.client_password_set_at ? `створено ${formatDate(mb.client_password_set_at)}` : "не створено"}.
        </p>
        {generated && (
          <div className="secret-box">
            <div className="small">Скопіюйте зараз — пароль більше не буде показано:</div>
            <code className="big-code">{generated}</code>
          </div>
        )}
        <label>
          Пароль акаунта для підтвердження
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="row gap wrap">
          <button className="btn btn-primary" onClick={() => generate(false)}>
            {mb.client_password_set_at ? "Згенерувати новий" : "Згенерувати"}
          </button>
          {mb.client_password_set_at && (
            <button className="btn btn-danger" onClick={() => generate(true)}>
              Відкликати
            </button>
          )}
        </div>
        {error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}

const ACTION_LABELS: Record<string, string> = {
  "login.success": "Вхід",
  "login.failed": "Невдала спроба входу",
  "login.password_ok": "Пароль підтверджено (очікується 2FA)",
  "login.2fa_failed": "Невірний код 2FA",
  "login.locked": "Вхід заблоковано",
  logout: "Вихід",
  "password.changed": "Пароль змінено",
  "2fa.enabled": "2FA увімкнено",
  "2fa.disabled": "2FA вимкнено",
  "2fa.setup_started": "Початок налаштування 2FA",
  "2fa.backup_codes_regenerated": "Нові резервні коди",
  "session.revoked": "Сесію завершено",
  "session.revoked_others": "Завершено інші сесії",
  "file.uploaded": "Файл завантажено",
  "file.trashed": "Файл у кошику",
  "share.created": "Спільний доступ",
  "public_link.created": "Публічне посилання",
  "public_link.download": "Завантаження за посиланням",
  "mail.sent": "Лист надіслано",
  "mail.client_password_set": "Пароль поштового клієнта",
};

function ActivityTab() {
  const [events, setEvents] = useState<{ action: string; created_at: string; ip_address: string | null; user_agent: string }[]>([]);
  useEffect(() => {
    get<typeof events>("/api/account/audit/").then(setEvents);
  }, []);
  return (
    <div className="card">
      <h3>Останні події безпеки</h3>
      <table className="table">
        <tbody>
          {events.map((e, i) => (
            <tr key={i}>
              <td className={e.action.includes("failed") || e.action.includes("locked") ? "text-danger" : ""}>
                {ACTION_LABELS[e.action] || e.action}
              </td>
              <td className="small">{e.ip_address}</td>
              <td className="small muted">{formatDate(e.created_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
