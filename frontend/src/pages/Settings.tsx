import { FormEvent, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { Preferences } from "../api/types";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { LANGS, useI18n, useT } from "../i18n";
import type { TKey } from "../i18n";
import { ACCENTS, THEMES, applyAppearance } from "../lib/appearance";
import type { Accent, Theme } from "../lib/appearance";
import { formatBytes, formatDate } from "../lib/format";

const TABS: { id: string; label: TKey; icon: string }[] = [
  { id: "profile", label: "settings.tab.profile", icon: "user" },
  { id: "security", label: "settings.tab.security", icon: "shield" },
  { id: "sessions", label: "settings.tab.sessions", icon: "lock" },
  { id: "mail", label: "settings.tab.mail", icon: "mail" },
  { id: "activity", label: "settings.tab.activity", icon: "eye" },
];

export default function SettingsPage() {
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") || "profile";
  const t = useT();
  return (
    <div className="page">
      <div className="page-head">
        <h2>{t("nav.settings")}</h2>
      </div>
      <div className="tabs">
        {TABS.map((tb) => (
          <button key={tb.id} className={`tab ${tab === tb.id ? "active" : ""}`} onClick={() => setParams({ tab: tb.id })}>
            <Icon name={tb.icon} size={16} /> {t(tb.label)}
          </button>
        ))}
      </div>
      {tab === "profile" && (
        <>
          <AppearanceCard />
          <ProfileTab />
        </>
      )}
      {tab === "security" && <SecurityTab />}
      {tab === "sessions" && <SessionsTab />}
      {tab === "mail" && <MailClientTab />}
      {tab === "activity" && <ActivityTab />}
    </div>
  );
}

function AppearanceCard() {
  const { user, updatePreferences } = useAuth();
  const { lang, setLang, t } = useI18n();
  const toast = useToast();
  if (!user) return null;
  const prefs = user.preferences;

  // Застосовується одразу; сервер зберігає вибір для інших пристроїв
  const save = (changes: { theme?: Theme; accent?: Accent }) => {
    applyAppearance(changes.theme, changes.accent);
    updatePreferences(changes).catch((err) => toast(errorText(err), "error"));
  };
  const THEME_LABELS: Record<Theme, TKey> = { system: "appearance.system", light: "appearance.light", dark: "appearance.dark" };

  return (
    <div className="card stack appearance-card">
      <h3>{t("appearance.title")}</h3>
      <div className="appearance-row">
        <span className="appearance-label">{t("appearance.theme")}</span>
        <div className="segmented" role="radiogroup" aria-label={t("appearance.theme")}>
          {THEMES.map((th) => (
            <button
              key={th}
              type="button"
              role="radio"
              aria-checked={prefs.theme === th}
              className={prefs.theme === th ? "active" : ""}
              onClick={() => save({ theme: th })}
            >
              <Icon name={th === "light" ? "sun" : th === "dark" ? "moon" : "settings"} size={14} /> {t(THEME_LABELS[th])}
            </button>
          ))}
        </div>
      </div>
      <div className="appearance-row">
        <span className="appearance-label">{t("appearance.accent")}</span>
        <div className="swatches" role="radiogroup" aria-label={t("appearance.accent")}>
          {ACCENTS.map((a) => (
            <button
              key={a.id}
              type="button"
              role="radio"
              aria-checked={prefs.accent === a.id}
              aria-label={t(`accent.${a.id}` as TKey)}
              title={t(`accent.${a.id}` as TKey)}
              className={`swatch swatch-${a.id} ${prefs.accent === a.id ? "active" : ""}`}
              onClick={() => save({ accent: a.id })}
            >
              {prefs.accent === a.id && <Icon name="check" size={14} />}
            </button>
          ))}
        </div>
      </div>
      <div className="appearance-row">
        <span className="appearance-label">{t("common.language")}</span>
        <div className="segmented" role="radiogroup" aria-label={t("common.language")}>
          {LANGS.map((l) => (
            <button
              key={l.id}
              type="button"
              role="radio"
              aria-checked={lang === l.id}
              className={lang === l.id ? "active" : ""}
              onClick={() => setLang(l.id).catch((err) => toast(errorText(err), "error"))}
            >
              {l.label}
            </button>
          ))}
        </div>
      </div>
      <p className="small muted">{t("appearance.hint")}</p>
    </div>
  );
}

function ProfileTab() {
  const { user, refresh } = useAuth();
  const t = useT();
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
      if (prefs) {
        // Тема, акцент і мова зберігаються окремо (одразу при виборі) — тут їх не перезаписуємо
        const { theme: _theme, accent: _accent, language: _language, ...rest } = prefs;
        await patch("/api/account/preferences/", rest);
      }
      await refresh();
      toast(t("common.saved"), "success");
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  if (!user || !prefs) return null;
  const set = <K extends keyof Preferences>(k: K, v: Preferences[K]) => setPrefs({ ...prefs, [k]: v });
  const TYPE_LABELS: Record<string, string> = {
    image: t("profile.type.image"),
    video: t("profile.type.video"),
    audio: t("profile.type.audio"),
    application: t("profile.type.application"),
    text: t("profile.type.text"),
  };

  return (
    <div className="settings-grid">
      <form className="card stack" onSubmit={save}>
        <h3>{t("profile.title")}</h3>
        <label>
          {t("profile.username")}
          <input value={user.username} disabled />
        </label>
        <label>
          {t("auth.email")}
          <input value={user.email} disabled />
        </label>
        <label>
          {t("profile.displayName")}
          <input value={displayName} onChange={(e) => setDisplayName(e.target.value)} maxLength={100} />
        </label>
        <h3>{t("profile.preferences")}</h3>
        <div className="grid-2">
          <label>
            {t("profile.timezone")}
            <input value={prefs.timezone} onChange={(e) => set("timezone", e.target.value)} />
          </label>
          <label>
            {t("profile.weekStart")}
            <select value={prefs.week_starts_monday ? "1" : "0"} onChange={(e) => set("week_starts_monday", e.target.value === "1")}>
              <option value="1">{t("profile.monday")}</option>
              <option value="0">{t("profile.sunday")}</option>
            </select>
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={prefs.discoverable} onChange={(e) => set("discoverable", e.target.checked)} />
          {t("profile.discoverable")}
        </label>
        <label className="check">
          <input type="checkbox" checked={prefs.mail_load_remote_images} onChange={(e) => set("mail_load_remote_images", e.target.checked)} />
          {t("profile.remoteImages")}
        </label>
        <div>
          <button className="btn btn-primary">{t("common.save")}</button>
        </div>
      </form>

      {usage && (
        <div className="card stack">
          <h3>{t("profile.storage")}</h3>
          <div className="big-number">
            {formatBytes(usage.used_bytes)} <span className="muted">{t("profile.of", { total: formatBytes(usage.quota_bytes) })}</span>
          </div>
          <ul className="plain-list">
            {Object.entries(usage.by_type).map(([k, v]) => (
              <li key={k} className="row-between">
                <span>{TYPE_LABELS[k] || k}</span>
                <span className="muted">{formatBytes(v)}</span>
              </li>
            ))}
            <li className="row-between">
              <span>{t("nav.trash")}</span>
              <span className="muted">{formatBytes(usage.trash_bytes)}</span>
            </li>
          </ul>
          <p className="small muted">{t("profile.quotaHint")}</p>
        </div>
      )}
    </div>
  );
}

function SecurityTab() {
  const { user, refresh } = useAuth();
  const t = useT();
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
    if (pw.next.length < 12) return setPwError(t("security.pwTooShort"));
    if (pw.next !== pw.next2) return setPwError(t("security.pwMismatch"));
    try {
      await post("/api/account/password/", { current_password: pw.current, new_password: pw.next });
      setPw({ current: "", next: "", next2: "" });
      toast(t("security.pwChanged"), "success");
      load();
    } catch (err) {
      setPwError(errorText(err));
    }
  };

  const startSetup = async () => {
    setError("");
    if (!confirmPw) return setError(t("security.confirmPassword"));
    try {
      setSetup(await post("/api/account/security/totp/setup/", { password: confirmPw }));
      setConfirmPw("");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const confirmSetup = async () => {
    setError("");
    if (!/^\d{6}$/.test(code.trim())) return setError(t("security.enterCode6"));
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
    if (!confirmPw || !code) return setError(t("security.needPwAndCode"));
    try {
      await post("/api/account/security/2fa/disable/", { password: confirmPw, code: code.trim() });
      setConfirmPw("");
      setCode("");
      await refresh();
      load();
      toast(t("security.2faDisabled"), "info");
    } catch (err) {
      setError(errorText(err));
    }
  };

  const regenerate = async () => {
    setError("");
    if (!confirmPw) return setError(t("security.confirmPassword"));
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
          <Icon name="shield" /> {t("security.2faTitle")}
        </h3>
        {info.has_2fa ? (
          <p>
            <span className="pill pill-success">{t("security.enabled")}</span> {t("security.backupLeft")} <strong>{info.backup_codes_remaining}</strong>
          </p>
        ) : (
          <p className="muted">
            {t("security.2faHint")}
          </p>
        )}

        {codes && (
          <div className="secret-box">
            <strong>{t("security.backupSaveNow")}</strong>
            <div className="codes">
              {codes.map((c) => (
                <code key={c}>{c}</code>
              ))}
            </div>
            <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(codes.join("\n")).then(() => toast(t("security.copied"), "success"))}>
              {t("security.copy")}
            </button>
          </div>
        )}

        {setup ? (
          <div className="stack">
            <p>{t("security.scanQr")}</p>
            <img className="qr" src={setup.qr_svg} alt={t("security.qrAlt")} />
            <details>
              <summary className="small">{t("security.cantScan")}</summary>
              <code className="break">{setup.secret}</code>
            </details>
            <p>{t("security.enterAppCode")}</p>
            <input inputMode="numeric" maxLength={6} className="code-input" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
            <div className="row gap">
              <button className="btn btn-primary" onClick={confirmSetup}>
                {t("security.enable2fa")}
              </button>
              <button className="btn" onClick={() => setSetup(null)}>
                {t("common.cancel")}
              </button>
            </div>
          </div>
        ) : (
          <div className="stack">
            <label>
              {t("security.currentPassword")}
              <input type="password" autoComplete="current-password" value={confirmPw} onChange={(e) => setConfirmPw(e.target.value)} />
            </label>
            {info.has_2fa && (
              <label>
                {t("security.codeToDisable")}
                <input inputMode="numeric" value={code} onChange={(e) => setCode(e.target.value)} />
              </label>
            )}
            <div className="row gap wrap">
              {!info.has_2fa && (
                <button className="btn btn-primary" onClick={startSetup}>
                  {t("security.setup2fa")}
                </button>
              )}
              {info.has_2fa && (
                <>
                  <button className="btn" onClick={regenerate}>
                    {t("security.newBackupCodes")}
                  </button>
                  <button className="btn btn-danger" onClick={disable}>
                    {t("security.disable2fa")}
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
          <Icon name="lock" /> {t("security.password")}
        </h3>
        <p className="small muted">{t("security.changedAt", { date: formatDate(info.password_changed_at) })}</p>
        <label>
          {t("security.currentPassword")}
          <input type="password" autoComplete="current-password" value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
        </label>
        <label>
          {t("security.newPassword")}
          <input type="password" autoComplete="new-password" value={pw.next} onChange={(e) => setPw({ ...pw, next: e.target.value })} />
        </label>
        <label>
          {t("security.repeatPassword")}
          <input type="password" autoComplete="new-password" value={pw.next2} onChange={(e) => setPw({ ...pw, next2: e.target.value })} />
        </label>
        {pwError && <div className="form-error">{pwError}</div>}
        <div>
          <button className="btn btn-primary">{t("security.changePassword")}</button>
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
  const t = useT();
  const toast = useToast();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const load = () => get<SessionInfo[]>("/api/account/sessions/").then(setSessions);
  useEffect(() => {
    load();
  }, []);

  return (
    <div className="card stack">
      <div className="row-between">
        <h3>{t("security.sessionsTitle")}</h3>
        <button
          className="btn btn-sm"
          onClick={async () => {
            const r = await post<{ revoked: number }>("/api/account/sessions/revoke_others/");
            toast(t("security.sessionsRevoked", { n: r.revoked }), "success");
            load();
          }}
        >
          {t("security.signOutOthers")}
        </button>
      </div>
      <table className="table">
        <thead>
          <tr>
            <th>{t("security.device")}</th>
            <th>{t("security.ip")}</th>
            <th>{t("security.activity")}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td className="small">
                {s.user_agent.slice(0, 90) || t("security.unknown")} {s.current && <span className="pill pill-success">{t("security.thisSession")}</span>}
              </td>
              <td className="small">{s.ip_address}</td>
              <td className="small muted">{formatDate(s.last_seen)}</td>
              <td>
                {!s.current && (
                  <button className="link-btn danger" onClick={() => del(`/api/account/sessions/${s.id}/`).then(load)}>
                    {t("security.endSession")}
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
  const t = useT();
  const toast = useToast();
  const [boxes, setBoxes] = useState<{ id: string; address: string }[]>([]);
  const [boxId, setBoxId] = useState("");
  const [mb, setMb] = useState<{
    id: string;
    address: string;
    quota_mb: number;
    client_password_set_at: string | null;
    imap: { host: string; port: number; security: string };
    smtp: { host: string; port: number; security: string };
  } | null>(null);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [generated, setGenerated] = useState<string | null>(null);

  const load = (id = boxId) =>
    get<NonNullable<typeof mb>>(`/api/mail/mailbox/${id ? `?mailbox=${encodeURIComponent(id)}` : ""}`)
      .then(setMb)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    load();
    get<{ id: string; address: string }[]>("/api/mail/mailboxes/").then(setBoxes).catch(() => {});
  }, []);

  const generate = async (revoke = false) => {
    setError("");
    if (!password) return setError(t("security.confirmAccountPassword"));
    try {
      const r = await post<{ password?: string }>("/api/mail/mailbox/client-password/", { password, revoke, mailbox: mb?.id });
      setGenerated(r.password || null);
      setPassword("");
      if (revoke) toast(t("security.clientAccessOff"), "info");
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
        <h3>{t("security.mailClientTitle")}</h3>
        {boxes.length > 1 && (
          <label>
            {t("security.mailbox")}
            <select
              value={mb.id}
              onChange={(e) => {
                setBoxId(e.target.value);
                setGenerated(null);
                load(e.target.value);
              }}
            >
              {boxes.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.address}
                </option>
              ))}
            </select>
          </label>
        )}
        <dl className="info-list">
          <dt>{t("security.address")}</dt>
          <dd>{mb.address}</dd>
          <dt>IMAP</dt>
          <dd>
            {mb.imap.host}:{mb.imap.port} ({mb.imap.security})
          </dd>
          <dt>SMTP</dt>
          <dd>
            {t("security.smtpOr", { host: mb.smtp.host, port: mb.smtp.port, security: mb.smtp.security })}
          </dd>
          <dt>{t("security.login")}</dt>
          <dd>{mb.address}</dd>
          <dt>{t("security.quota")}</dt>
          <dd>{t("security.quotaMb", { n: mb.quota_mb })}</dd>
        </dl>
      </div>
      <div className="card stack">
        <h3>{t("security.appPassword")}</h3>
        <p className="small muted">
          {t("security.appPasswordHint", {
            status: mb.client_password_set_at
              ? t("security.appPwCreated", { date: formatDate(mb.client_password_set_at) })
              : t("security.appPwNotCreated"),
          })}
        </p>
        {generated && (
          <div className="secret-box">
            <div className="small">{t("security.copyNow")}</div>
            <code className="big-code">{generated}</code>
          </div>
        )}
        <label>
          {t("security.accountPwConfirm")}
          <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        <div className="row gap wrap">
          <button className="btn btn-primary" onClick={() => generate(false)}>
            {mb.client_password_set_at ? t("security.generateNew") : t("security.generate")}
          </button>
          {mb.client_password_set_at && (
            <button className="btn btn-danger" onClick={() => generate(true)}>
              {t("security.revoke")}
            </button>
          )}
        </div>
        {error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}

const ACTION_LABELS: Record<string, TKey> = {
  "login.success": "security.action.loginSuccess",
  "login.failed": "security.action.loginFailed",
  "login.password_ok": "security.action.loginPasswordOk",
  "login.2fa_failed": "security.action.login2faFailed",
  "login.locked": "security.action.loginLocked",
  logout: "security.action.logout",
  "password.changed": "security.action.passwordChanged",
  "2fa.enabled": "security.action.2faEnabled",
  "2fa.disabled": "security.action.2faDisabled",
  "2fa.setup_started": "security.action.2faSetupStarted",
  "2fa.backup_codes_regenerated": "security.action.backupCodesRegenerated",
  "session.revoked": "security.action.sessionRevoked",
  "session.revoked_others": "security.action.sessionRevokedOthers",
  "file.uploaded": "security.action.fileUploaded",
  "file.trashed": "security.action.fileTrashed",
  "share.created": "security.action.shareCreated",
  "public_link.created": "security.action.publicLinkCreated",
  "public_link.download": "security.action.publicLinkDownload",
  "mail.sent": "security.action.mailSent",
  "mail.client_password_set": "security.action.mailClientPasswordSet",
};

function ActivityTab() {
  const t = useT();
  const [events, setEvents] = useState<{ action: string; created_at: string; ip_address: string | null; user_agent: string }[]>([]);
  useEffect(() => {
    get<typeof events>("/api/account/audit/").then(setEvents);
  }, []);
  return (
    <div className="card">
      <h3>{t("security.activityTitle")}</h3>
      <table className="table">
        <tbody>
          {events.map((e, i) => (
            <tr key={i}>
              <td className={e.action.includes("failed") || e.action.includes("locked") ? "text-danger" : ""}>
                {ACTION_LABELS[e.action] ? t(ACTION_LABELS[e.action]) : e.action}
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
