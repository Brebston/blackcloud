import { FormEvent, useEffect, useState } from "react";
import { del, errorText, get, post } from "../api/client";
import type { PublicLink, Share } from "../api/types";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";
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
  const t = useT();
  const [shares, setShares] = useState<Share[]>([]);
  const [links, setLinks] = useState<PublicLink[]>([]);
  const [newUrl, setNewUrl] = useState("");
  const [linkForm, setLinkForm] = useState({ expires_days: 7, password: "", max_downloads: "" });
  const [folderForm, setFolderForm] = useState({ kind: "folder" as "folder" | "drop", expires_days: 7, password: "", note: "", max_mb: "" });
  const [error, setError] = useState("");

  const load = async () => {
    const all = await get<Share[]>("/api/files/shares/");
    setShares(all.filter((s) => (target.type === "file" ? s.file === target.id : s.folder === target.id)));
    setLinks(await get<PublicLink[]>(`/api/files/links/?${target.type}=${target.id}`));
  };

  useEffect(() => {
    load().catch((e) => setError(errorText(e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.id]);

  const shareWith = async (username: string) => {
    setError("");
    try {
      await post("/api/files/shares/", { username, [target.type]: target.id });
      toast(t("files.shareDlg.granted", { username }), "success");
      load();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const createLink = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (linkForm.password && linkForm.password.length < 8) {
      setError(t("files.shareDlg.passwordShort"));
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

  const createFolderLink = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (folderForm.password && folderForm.password.length < 8) {
      setError(t("files.shareDlg.passwordShort"));
      return;
    }
    try {
      const r = await post<PublicLink>("/api/files/links/", {
        kind: folderForm.kind,
        folder: target.id,
        expires_days: Number(folderForm.expires_days),
        password: folderForm.password || undefined,
        note: folderForm.note.trim(),
        max_upload_mb: folderForm.kind === "drop" && folderForm.max_mb ? Number(folderForm.max_mb) : null,
      });
      setNewUrl(r.url || "");
      setFolderForm({ ...folderForm, password: "", note: "", max_mb: "" });
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const linkList = links.length > 0 && (
    <ul className="plain-list">
      {links.map((l) => (
        <li key={l.id} className="row-between">
          <span className="small">
            {l.kind !== "file" && <span className="pill">{l.kind === "drop" ? t("pubf.labelDrop") : t("pubf.labelFolder")}</span>}{" "}
            {t("files.shareDlg.until", { date: formatDate(l.expires_at) })} ·{" "}
            {l.kind === "drop"
              ? t("pubf.uploads", { n: l.upload_count, size: formatBytes(l.upload_bytes) })
              : l.kind === "folder"
                ? t("pubf.downloads", { n: l.download_count })
                : `${l.download_count}${l.max_downloads ? `/${l.max_downloads}` : ""} ${t("files.shareDlg.downloads")}`}{" "}
            {l.has_password && t("files.shareDlg.withPassword")}
            {!l.active && t("files.shareDlg.inactive")}
          </span>
          <button className="link-btn danger" onClick={() => del(`/api/files/links/${l.id}/`).then(load)}>
            {t("files.shareDlg.revoke")}
          </button>
        </li>
      ))}
    </ul>
  );

  const newUrlBox = newUrl && (
    <div className="secret-box">
      <div className="small muted">{t("files.shareDlg.copyNow")}</div>
      <div className="row-between">
        <code className="break">{newUrl}</code>
        <button className="btn btn-sm" onClick={() => navigator.clipboard.writeText(newUrl).then(() => toast(t("files.shareDlg.copied"), "success"))}>
          {t("files.shareDlg.copy")}
        </button>
      </div>
    </div>
  );

  return (
    <Modal title={t("files.shareDlg.title", { name: target.name })} onClose={onClose}>
      <section className="stack">
        <h4>{t("files.shareDlg.users")}</h4>
        <p className="muted small">{t("files.shareDlg.readOnlyHint")}</p>
        <UserPicker onPick={(u) => shareWith(u.username)} placeholder={t("files.shareDlg.findUser")} />
        {shares.length > 0 && (
          <ul className="plain-list">
            {shares.map((s) => (
              <li key={s.id} className="row-between">
                <span>
                  <Icon name="user" size={14} /> {s.recipient}
                </span>
                <button className="link-btn danger" onClick={() => del(`/api/files/shares/${s.id}/`).then(load)}>
                  {t("files.shareDlg.revoke")}
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {target.type === "file" && (
        <section className="stack">
          <h4>{t("files.shareDlg.publicLink")}</h4>
          {target.downloadable === false ? (
            <p className="muted small">{t("files.shareDlg.afterScan")}</p>
          ) : (
            <form className="grid-3" onSubmit={createLink}>
              <label>
                {t("files.shareDlg.days")}
                <input
                  type="number"
                  min={1}
                  max={30}
                  value={linkForm.expires_days}
                  onChange={(e) => setLinkForm({ ...linkForm, expires_days: Number(e.target.value) })}
                />
              </label>
              <label>
                {t("files.shareDlg.password")}
                <input
                  type="password"
                  autoComplete="new-password"
                  value={linkForm.password}
                  onChange={(e) => setLinkForm({ ...linkForm, password: e.target.value })}
                />
              </label>
              <label>
                {t("files.shareDlg.maxDownloads")}
                <input
                  type="number"
                  min={1}
                  placeholder={t("files.shareDlg.unlimited")}
                  value={linkForm.max_downloads}
                  onChange={(e) => setLinkForm({ ...linkForm, max_downloads: e.target.value })}
                />
              </label>
              <button className="btn btn-primary">
                <Icon name="link" size={16} /> {t("files.shareDlg.createLink")}
              </button>
            </form>
          )}
          {newUrlBox}
          {linkList}
        </section>
      )}

      {target.type === "folder" && (
        <section className="stack">
          <h4>{t("pubf.folderLinks")}</h4>
          <form className="stack" onSubmit={createFolderLink}>
            <div className="link-kinds" role="radiogroup">
              {(["folder", "drop"] as const).map((k) => (
                <label key={k} className={`link-kind ${folderForm.kind === k ? "active" : ""}`}>
                  <input type="radio" name="link-kind" checked={folderForm.kind === k} onChange={() => setFolderForm({ ...folderForm, kind: k })} />
                  <span>
                    <strong>
                      <Icon name={k === "drop" ? "upload" : "folder"} size={15} /> {k === "drop" ? t("pubf.kindDrop") : t("pubf.kindFolder")}
                    </strong>
                    <span className="small muted">{k === "drop" ? t("pubf.kindDropHint") : t("pubf.kindFolderHint")}</span>
                  </span>
                </label>
              ))}
            </div>
            <div className="grid-3">
              <label>
                {t("files.shareDlg.days")}
                <input type="number" min={1} max={30} value={folderForm.expires_days} onChange={(e) => setFolderForm({ ...folderForm, expires_days: Number(e.target.value) })} />
              </label>
              <label>
                {t("files.shareDlg.password")}
                <input type="password" autoComplete="new-password" value={folderForm.password} onChange={(e) => setFolderForm({ ...folderForm, password: e.target.value })} />
              </label>
              {folderForm.kind === "drop" && (
                <label>
                  {t("pubf.maxMb")}
                  <input type="number" min={1} placeholder={t("files.shareDlg.unlimited")} value={folderForm.max_mb} onChange={(e) => setFolderForm({ ...folderForm, max_mb: e.target.value })} />
                </label>
              )}
            </div>
            <label>
              {t("pubf.note")}
              <input maxLength={300} placeholder={folderForm.kind === "drop" ? t("pubf.notePlaceholder") : ""} value={folderForm.note} onChange={(e) => setFolderForm({ ...folderForm, note: e.target.value })} />
            </label>
            <div>
              <button className="btn btn-primary">
                <Icon name="link" size={16} /> {t("pubf.create")}
              </button>
            </div>
          </form>
          {newUrlBox}
          {linkList}
        </section>
      )}
      {error && <div className="form-error">{error}</div>}
    </Modal>
  );
}
