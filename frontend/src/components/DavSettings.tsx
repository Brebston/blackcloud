// Налаштування WebDAV: адреса сервера, паролі застосунків (створення/відкликання), інструкції.
import { FormEvent, useEffect, useState } from "react";
import { del, errorText, get, post } from "../api/client";
import { useT } from "../i18n";
import { formatDate } from "../lib/format";
import { useToast } from "./Toast";

interface DavPassword {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  last_used_ip: string | null;
}

interface DavInfo {
  server_url: string;
  username: string;
  max: number;
  passwords: DavPassword[];
}

export default function DavSettings() {
  const t = useT();
  const toast = useToast();
  const [info, setInfo] = useState<DavInfo | null>(null);
  const [error, setError] = useState("");
  const [label, setLabel] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [generated, setGenerated] = useState<{ label: string; password: string } | null>(null);

  // Адреса — з поточного хоста (той самий домен, що й вебінтерфейс)
  const serverUrl = `https://${window.location.host}/dav/`;
  const host = window.location.host;

  const load = () =>
    get<DavInfo>("/api/dav/passwords/")
      .then((data) => {
        setInfo(data);
        setError("");
      })
      .catch((e) => setError(errorText(e)));

  useEffect(() => {
    load();
  }, []);

  const copy = (text: string) =>
    navigator.clipboard
      .writeText(text)
      .then(() => toast(t("dav.copied"), "success"))
      .catch(() => {});

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!password) return setError(t("dav.confirmPassword"));
    setBusy(true);
    try {
      const r = await post<DavPassword & { password: string }>("/api/dav/passwords/", { label: label.trim(), password });
      setGenerated({ label: r.label, password: r.password });
      setLabel("");
      setPassword("");
      load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (item: DavPassword) => {
    if (!window.confirm(t("dav.revokeConfirm", { label: item.label }))) return;
    try {
      await del(`/api/dav/passwords/${item.id}/`);
      toast(t("dav.revoked"), "info");
      load();
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  if (!info) return error ? <div className="card">{error}</div> : null;
  const full = info.passwords.length >= info.max;

  return (
    <div className="settings-grid">
      <div className="card stack">
        <h3>{t("dav.title")}</h3>
        <p className="small muted">{t("dav.intro")}</p>
        <dl className="info-list">
          <dt>{t("dav.serverUrl")}</dt>
          <dd>
            <div className="row gap-sm wrap">
              <code className="break">{serverUrl}</code>
              <button type="button" className="btn btn-sm" onClick={() => copy(serverUrl)}>
                {t("dav.copy")}
              </button>
            </div>
          </dd>
          <dt>{t("dav.username")}</dt>
          <dd>
            <code className="break">{info.username}</code>
          </dd>
        </dl>

        <div className="row-between">
          <h4>{t("dav.passwords")}</h4>
          <span className="small muted">{t("dav.passwordsCount", { n: info.passwords.length, max: info.max })}</span>
        </div>
        {info.passwords.length === 0 ? (
          <p className="small muted">{t("dav.noPasswords")}</p>
        ) : (
          <ul className="plain-list">
            {info.passwords.map((p) => (
              <li key={p.id} className="row-between">
                <div>
                  <div>{p.label}</div>
                  <div className="small muted">
                    {t("dav.created", { date: formatDate(p.created_at) })} ·{" "}
                    {p.last_used_at
                      ? t("dav.lastUsed", { date: formatDate(p.last_used_at), ip: p.last_used_ip || "—" })
                      : t("dav.neverUsed")}
                  </div>
                </div>
                <button type="button" className="link-btn danger" onClick={() => revoke(p)}>
                  {t("dav.revoke")}
                </button>
              </li>
            ))}
          </ul>
        )}

        {generated && (
          <div className="secret-box">
            <div className="small">{t("dav.generatedTitle", { label: generated.label })}</div>
            <div className="row gap-sm wrap">
              <code className="big-code break">{generated.password}</code>
              <button type="button" className="btn btn-sm" onClick={() => copy(generated.password)}>
                {t("dav.copy")}
              </button>
            </div>
            <div className="small muted">{t("dav.generatedHint", { username: info.username })}</div>
            <button type="button" className="link-btn" onClick={() => setGenerated(null)}>
              {t("dav.hide")}
            </button>
          </div>
        )}

        <form className="stack" onSubmit={create}>
          <h4>{t("dav.newTitle")}</h4>
          {full ? (
            <p className="small muted">{t("dav.limitReached")}</p>
          ) : (
            <>
              <label>
                {t("dav.label")}
                <input
                  value={label}
                  maxLength={60}
                  placeholder={t("dav.labelPlaceholder")}
                  onChange={(e) => setLabel(e.target.value)}
                />
              </label>
              <label>
                {t("dav.accountPassword")}
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
              </label>
              <div>
                <button className="btn btn-primary" disabled={busy}>
                  {t("dav.create")}
                </button>
              </div>
            </>
          )}
          {error && <div className="form-error">{error}</div>}
        </form>
      </div>

      <div className="card stack">
        <h3>{t("dav.howTo")}</h3>
        <div>
          <h4>{t("dav.mac")}</h4>
          <p className="small">{t("dav.macSteps", { url: serverUrl })}</p>
          <p className="small muted">{t("dav.macDsStore")}</p>
        </div>
        <div>
          <h4>{t("dav.windows")}</h4>
          <p className="small">{t("dav.windowsSteps", { url: serverUrl })}</p>
          <p className="small muted">{t("dav.windowsWebClient")}</p>
        </div>
        <div>
          <h4>{t("dav.linux")}</h4>
          <p className="small">{t("dav.linuxSteps", { url: serverUrl, host })}</p>
        </div>
        <div>
          <h4>{t("dav.mobile")}</h4>
          <p className="small">{t("dav.mobileSteps", { url: serverUrl })}</p>
        </div>
        <div>
          <h4>{t("dav.limitsTitle")}</h4>
          <ul className="small muted">
            <li>{t("dav.limitScan")}</li>
            <li>{t("dav.limitTrash")}</li>
            <li>{t("dav.limitShared")}</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
