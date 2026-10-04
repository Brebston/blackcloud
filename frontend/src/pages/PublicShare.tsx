import { FormEvent, useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import Icon from "../components/Icon";
import { formatBytes, formatDate } from "../lib/format";

interface Info {
  name: string;
  size: number;
  mime_type: string;
  requires_password: boolean;
  expires_at: string;
}

// Публічна сторінка завантаження: без сесії, без CSRF (ендпоінти не використовують cookie)
export default function PublicSharePage() {
  const { token = "" } = useParams();
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch(`/api/public/${encodeURIComponent(token)}/`, { credentials: "omit" })
      .then(async (r) => {
        const d = await r.json();
        if (!r.ok) throw new Error(d.detail || "Посилання недійсне");
        setInfo(d);
      })
      .catch((e) => setError(e.message));
  }, [token]);

  const download = async (e?: FormEvent) => {
    e?.preventDefault();
    setError("");
    if (info?.requires_password && !password) {
      setError("Введіть пароль.");
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(`/api/public/${encodeURIComponent(token)}/authorize/`, {
        method: "POST",
        credentials: "omit",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      const d = await r.json();
      if (!r.ok) throw new Error(d.detail || "Помилка");
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
        {!info && !error && <p className="muted">Завантаження…</p>}
        {info && (
          <form onSubmit={download} className="stack">
            <div className="public-file">
              <Icon name="file" size={28} />
              <div>
                <div className="public-name">{info.name}</div>
                <div className="muted small">
                  {formatBytes(info.size)} · доступно до {formatDate(info.expires_at)}
                </div>
              </div>
            </div>
            {info.requires_password && (
              <label>
                Пароль
                <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            )}
            <button className="btn btn-primary btn-block" disabled={busy}>
              <Icon name="download" size={16} /> Завантажити
            </button>
          </form>
        )}
        {error && <div className="form-error">{error}</div>}
      </div>
    </div>
  );
}
