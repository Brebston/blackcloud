import { FormEvent, useEffect, useState } from "react";
import { del, errorText, get, post } from "../api/client";
import type { PublicLink, Share } from "../api/types";
import { formatDate } from "../lib/format";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";
import UserPicker from "./UserPicker";

export default function ShareDialog({
  target,
  onClose,
}: {
  target: { type: "file" | "folder"; id: string; name: string; downloadable?: boolean };
  onClose: () => void;
}) {
  const toast = useToast();
  const [shares, setShares] = useState<Share[]>([]);
  const [links, setLinks] = useState<PublicLink[]>([]);
  const [newUrl, setNewUrl] = useState("");
  const [linkForm, setLinkForm] = useState({ expires_days: 7, password: "", max_downloads: "" });
  const [error, setError] = useState("");

  const load = async () => {
    const all = await get<Share[]>("/api/files/shares/");
    setShares(all.filter((s) => (target.type === "file" ? s.file === target.id : s.folder === target.id)));
    if (target.type === "file") setLinks(await get<PublicLink[]>(`/api/files/links/?file=${target.id}`));
  };

  useEffect(() => {
    load().catch((e) => setError(errorText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id]);

  const shareWith = async (username: string) => {
    setError("");
    try {
      await post("/api/files/shares/", { username, [target.type]: target.id });
      toast(`Доступ надано: ${username}`, "success");
      load();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const createLink = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (linkForm.password && linkForm.password.length < 6) {
      setError("Пароль посилання — щонайменше 6 символів.");
      return;
    }
    try {
      const r = await post<PublicLink>("/api/files/links/", {
        file: target.id,
        expires_days: Number(linkForm.expires_days),
        password: linkForm.password || undefined,
        max_downloads: linkForm.max_downloads ? Number(linkForm.max_downloads) : null,
      });
      setNewUrl(r.url || "");
      setLinkForm({ expires_days: 7, password: "", max_downloads: "" });
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={`Поділитися: ${target.name}`} onClose={onClose}>
      <section className="stack">
        <h4>Користувачам BlackCloud</h4>
        <p className="muted small">Отримувачі матимуть доступ лише на читання.</p>
        <UserPicker onPick={(u) => shareWith(u.username)} placeholder="Знайти користувача…" />
        {shares.length > 0 && (
          <ul className="plain-list">
            {shares.map((s) => (
              <li key={s.id} className="row-between">
                <span>
                  <Icon name="user" size={14} /> {s.recipient}
                </span>
                <button className="link-btn danger" onClick={() => del(`/api/files/shares/${s.id}/`).then(load)}>
                  Відкликати
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {target.type === "file" && (
        <section className="stack">
          <h4>Публічне посилання</h4>
          {target.downloadable === false ? (
            <p className="muted small">Посилання можна створити після антивірусної перевірки файлу.</p>
          ) : (
            <form className="grid-3" onSubmit={createLink}>
              <label>
                Діє днів
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={linkForm.expires_days}
                  onChange={(e) => setLinkForm({ ...linkForm, expires_days: Number(e.target.value) })}
                />
              </label>
              <label>
                Пароль (необов'язково)
                <input
                  type="password"
                  autoComplete="new-password"
                  value={linkForm.password}
                  onChange={(e) => setLinkForm({ ...linkForm, password: e.target.value })}
                />
              </label>
              <label>
                Макс. завантажень
                <input
                  type="number"
                  min={1}
                  placeholder="без ліміту"
                  value={linkForm.max_downloads}
                  onChange={(e) => setLinkForm({ ...linkForm, max_downloads: e.target.value })}
                />
              </label>
              <button className="btn btn-primary">
                <Icon name="link" size={16} /> Створити посилання
              </button>
            </form>
          )}
          {newUrl && (
            <div className="secret-box">
              <div className="small muted">Скопіюйте зараз — посилання більше не буде показано:</div>
              <div className="row-between">
                <code className="break">{newUrl}</code>
                <button
                  className="btn btn-sm"
                  onClick={() => navigator.clipboard.writeText(newUrl).then(() => toast("Скопійовано", "success"))}
                >
                  Копіювати
                </button>
              </div>
            </div>
          )}
          {links.length > 0 && (
            <ul className="plain-list">
              {links.map((l) => (
                <li key={l.id} className="row-between">
                  <span className="small">
                    до {formatDate(l.expires_at)} · {l.download_count}
                    {l.max_downloads ? `/${l.max_downloads}` : ""} завант. {l.has_password && "· з паролем"}
                    {!l.active && " · неактивне"}
                  </span>
                  <button className="link-btn danger" onClick={() => del(`/api/files/links/${l.id}/`).then(load)}>
                    Відкликати
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
      {error && <div className="form-error">{error}</div>}
    </Modal>
  );
}
