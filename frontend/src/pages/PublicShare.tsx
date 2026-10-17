import { FormEvent, useCallback, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import Icon from "../components/Icon";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";
import { DropBoxView, PasswordGate, PublicFolderView, publicPost, type PublicInfo } from "./PublicFolder";

// Публічна сторінка посилання: файл, тека (перегляд і завантаження) або скринька для файлів.
// Без сесії й CSRF (ендпоінти не використовують cookie). Токен живе у фрагменті URL (/s#токен):
// браузер не надсилає фрагмент на сервер, тож токен не потрапляє в журнали, Referer та історію.
export default function PublicSharePage() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const t = useT();
  const token = params.token || decodeURIComponent(location.hash.replace(/^#/, ""));
  const [info, setInfo] = useState<PublicInfo | null>(null);
  const [grant, setGrant] = useState("");
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  // Старі посилання /s/<токен> переписуються на /s#<токен>
  useEffect(() => {
    if (params.token) navigate(`/s#${encodeURIComponent(params.token)}`, { replace: true });
  }, [params.token, navigate]);

  const authorize = useCallback(
    async (pw: string) => {
      setError("");
      setBusy(true);
      try {
        const d = await publicPost<{ kind: string; grant?: string; download_url?: string }>("authorize", { token, password: pw });
        if (d.grant) setGrant(d.grant);
        else if (d.download_url) window.location.assign(d.download_url);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [token],
  );

  useEffect(() => {
    if (!token) {
      setError(t("publicShare.invalidLink"));
      return;
    }
    publicPost<PublicInfo>("info", { token })
      .then((d) => {
        setInfo(d);
        // Тека чи скринька без пароля — одразу відкриваємо
        if (d.kind !== "file" && !d.requires_password) authorize("");
      })
      .catch((e) => setError(e.message || t("publicShare.invalidLink")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  const download = async (e?: FormEvent) => {
    e?.preventDefault();
    if (info?.requires_password && !password) {
      setError(t("publicShare.enterPassword"));
      return;
    }
    authorize(password);
  };

  const wide = info?.kind === "folder" && !!grant;

  return (
    <div className={`auth-page ${wide ? "public-page-wide" : ""}`}>
      <div className={wide ? "public-card-wide" : "auth-card"}>
        <div className="auth-brand">
          <Icon name="cloud" size={32} />
          <h1>BlackCloud</h1>
        </div>
        {!info && !error && <p className="muted">{t("publicShare.loading")}</p>}
        {info?.kind === "file" && (
          <form onSubmit={download} className="stack">
            <div className="public-file">
              <Icon name="file" size={28} />
              <div>
                <div className="public-name">{info.name}</div>
                <div className="muted small">
                  {formatBytes(info.size || 0)} · {t("publicShare.availableUntil", { date: formatDate(info.expires_at) })}
                </div>
              </div>
            </div>
            {info.requires_password && (
              <label>
                {t("publicShare.password")}
                <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            )}
            <button className="btn btn-primary btn-block" disabled={busy}>
              <Icon name="download" size={16} /> {t("publicShare.download")}
            </button>
          </form>
        )}
        {info && info.kind !== "file" && !grant && info.requires_password && (
          <div className="stack">
            <div className="public-file">
              <Icon name={info.kind === "drop" ? "upload" : "folder"} size={28} />
              <div>
                <div className="public-name">{info.name}</div>
                <div className="muted small">
                  {info.kind === "drop" ? t("pubf.dropBy", { name: info.owner_name || "" }) : t("pubf.sharedBy", { name: info.owner_name || "" })}
                </div>
              </div>
            </div>
            <PasswordGate onSubmit={authorize} busy={busy} />
          </div>
        )}
        {info?.kind === "folder" && grant && <PublicFolderView grant={grant} info={info} />}
        {info?.kind === "drop" && grant && <DropBoxView grant={grant} info={info} />}
        {error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}
