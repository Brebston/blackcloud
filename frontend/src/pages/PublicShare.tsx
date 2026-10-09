import { FormEvent, useEffect, useState } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import Icon from "../components/Icon";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";

interface Info {
  name: string;
  size: number;
  mime_type: string;
  requires_password: boolean;
  expires_at: string;
}

function postPublic(path: string, body: Record<string, string>) {
  return fetch(`/api/public/${path}/`, {
    method: "POST",
    credentials: "omit",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// Публічна сторінка завантаження: без сесії, без CSRF (ендпоінти не використовують cookie).
// Токен живе у фрагменті URL (/s#токен): браузер не надсилає фрагмент на сервер,
// тож токен не потрапляє в журнали, Referer та історію запитів проксі.
export default function PublicSharePage() {
  const params = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const t = useT();
  const token = params.token || decodeURIComponent(location.hash.replace(/^#/, ""));
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  // Старі посилання /s/<токен> переписуються на /s#<токен>
  useEffect(() => {
    if (params.token) navigate(`/s#${encodeURIComponent(params.token)}`, { replace: true });
  }, [params.token, navigate]);

  useEffect(() => {
    if (!token) {
      setError(t("publicShare.invalidLink"));
      return;
    }
    postPublic("info", { token })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || t("publicShare.invalidLink"));
        setInfo(d);
      })
      .catch((e) => setError(e.message));
  }, [token]);

  const download = async (e?: FormEvent) => {
    e?.preventDefault();
    setError("");
    if (info?.requires_password && !password) {
      setError(t("publicShare.enterPassword"));
      return;
    }
    setBusy(true);
    try {
      const r = await postPublic("authorize", { token, password });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || t("publicShare.error"));
      window.location.assign(d.download_url);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <Icon name="cloud" size={32} />
          <h1>BlackCloud</h1>
        </div>
        {!info && !error && <p className="muted">{t("publicShare.loading")}</p>}
        {info && (
          <form onSubmit={download} className="stack">
            <div className="public-file">
              <Icon name="file" size={28} />
              <div>
                <div className="public-name">{info.name}</div>
                <div className="muted small">
                  {formatBytes(info.size)} · {t("publicShare.availableUntil", { date: formatDate(info.expires_at) })}
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
        {error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}
