import { ChangeEvent, DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import Icon from "../components/Icon";
import { useT } from "../i18n";
import { fileIcon, formatBytes, formatDate } from "../lib/format";

// Публічна тека (перегляд і завантаження) і «скринька для файлів» — без входу в акаунт.
// Пропуск (grant) передається в тілі POST або заголовком X-Link-Grant і ніколи — в URL.

export interface PublicInfo {
  kind: "file" | "folder" | "drop";
  name: string;
  note?: string;
  owner_name?: string;
  size?: number;
  mime_type?: string;
  requires_password: boolean;
  expires_at: string;
}

interface PubFile {
  id: string;
  name: string;
  size: number;
  mime_type: string;
  has_thumbnail: boolean;
  has_preview: boolean;
  content_version: number;
  updated_at: string;
  inline: boolean;
}

interface Listing {
  folder: { id: string; name: string };
  breadcrumbs: { id: string; name: string }[];
  folders: { id: string; name: string }[];
  files: PubFile[];
}

class PublicError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export async function publicPost<T>(path: string, body: Record<string, unknown>, headers: Record<string, string> = {}): Promise<T> {
  const r = await fetch(`/api/public/${path}/`, {
    method: "POST",
    credentials: "omit",
    headers: { "Content-Type": "application/json", ...headers },
    body: JSON.stringify(body),
  });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new PublicError(d.detail || `HTTP ${r.status}`, r.status);
  return d as T;
}

async function publicBlob(path: string, body: Record<string, unknown>): Promise<string> {
  const r = await fetch(`/api/public/${path}/`, {
    method: "POST",
    credentials: "omit",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new PublicError(`HTTP ${r.status}`, r.status);
  return URL.createObjectURL(await r.blob());
}

const BROWSER_IMAGES = ["image/jpeg", "image/png", "image/gif", "image/webp", "image/avif"];
const BROWSER_VIDEOS = ["video/mp4", "video/webm", "video/quicktime"];

function go(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

function Thumb({ grant, file }: { grant: string; file: PubFile }) {
  const [src, setSrc] = useState("");
  useEffect(() => {
    if (!file.has_thumbnail) return;
    let url = "";
    let cancelled = false;
    publicBlob("folder/image", { grant, file: file.id, size: "thumb" })
      .then((u) => {
        url = u;
        if (!cancelled) setSrc(u);
        else URL.revokeObjectURL(u);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
      if (url) URL.revokeObjectURL(url);
    };
  }, [grant, file.id, file.has_thumbnail]);
  if (src) return <img src={src} alt="" />;
  return <Icon name={fileIcon(file.mime_type, file.name)} size={34} />;
}

function Lightbox({ grant, files, index, onIndex, onClose, onDownload }: {
  grant: string;
  files: PubFile[];
  index: number;
  onIndex: (i: number) => void;
  onClose: () => void;
  onDownload: (f: PubFile) => void;
}) {
  const t = useT();
  const f = files[index];
  const [src, setSrc] = useState<{ id: string; url: string; kind: "img" | "video" | "none" } | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index < files.length - 1) onIndex(index + 1);
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [index, files.length, onClose, onIndex]);

  useEffect(() => {
    let cancelled = false;
    let blobUrl = "";
    setSrc(null);
    const done = (url: string, kind: "img" | "video" | "none") => !cancelled && setSrc({ id: f.id, url, kind });
    if (f.has_preview) {
      publicBlob("folder/image", { grant, file: f.id, size: "preview" })
        .then((u) => ((blobUrl = u), done(u, "img")))
        .catch(() => done("", "none"));
    } else if (f.inline && (BROWSER_IMAGES.includes(f.mime_type) || BROWSER_VIDEOS.includes(f.mime_type))) {
      publicPost<{ url: string }>("folder/file", { grant, file: f.id, mode: "view" })
        .then((r) => done(r.url, BROWSER_VIDEOS.includes(f.mime_type) ? "video" : "img"))
        .catch(() => done("", "none"));
    } else {
      done("", "none");
    }
    return () => {
      cancelled = true;
      if (blobUrl) URL.revokeObjectURL(blobUrl);
    };
  }, [grant, f.id, f.has_preview, f.inline, f.mime_type]);

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={f.name}>
      <header className="viewer-bar">
        <button className="icon-btn" onClick={onClose} aria-label={t("pubf.close")}>
          <Icon name="x" />
        </button>
        <div className="viewer-title">
          <span className="truncate">{f.name}</span>
          <span className="muted small">
            {formatBytes(f.size)} · {index + 1} / {files.length}
          </span>
        </div>
        <div className="viewer-actions">
          <button className="btn btn-sm" onClick={() => onDownload(f)}>
            <Icon name="download" size={14} /> {t("publicShare.download")}
          </button>
        </div>
      </header>
      <div className="viewer-body">
        {index > 0 && (
          <button className="viewer-nav prev" onClick={() => onIndex(index - 1)} aria-label={t("pubf.prev")}>
            <Icon name="chevronLeft" size={28} />
          </button>
        )}
        {!src && <div className="viewer-empty muted">{t("publicShare.loading")}</div>}
        {src?.kind === "img" && <img key={src.id} className="viewer-media" src={src.url} alt={f.name} />}
        {src?.kind === "video" && <video key={src.id} className="viewer-media" src={src.url} controls autoPlay playsInline />}
        {src?.kind === "none" && (
          <div className="viewer-empty">
            <Icon name={fileIcon(f.mime_type, f.name)} size={48} />
            <div>{t("pubf.noPreview")}</div>
          </div>
        )}
        {index < files.length - 1 && (
          <button className="viewer-nav next" onClick={() => onIndex(index + 1)} aria-label={t("pubf.next")}>
            <Icon name="chevronRight" size={28} />
          </button>
        )}
      </div>
    </div>
  );
}

export function PublicFolderView({ grant, info }: { grant: string; info: PublicInfo }) {
  const t = useT();
  const [folderId, setFolderId] = useState<string | null>(null);
  const [data, setData] = useState<Listing | null>(null);
  const [error, setError] = useState("");
  const [viewer, setViewer] = useState<number | null>(null);

  useEffect(() => {
    setData(null);
    publicPost<Listing>("folder/list", { grant, ...(folderId ? { folder: folderId } : {}) })
      .then(setData)
      .catch((e) => setError(e.status === 403 ? t("pubf.sessionExpired") : e.message));
  }, [grant, folderId, t]);

  const download = async (f: PubFile) => {
    try {
      go((await publicPost<{ download_url: string }>("folder/file", { grant, file: f.id })).download_url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const downloadAll = async () => {
    try {
      go((await publicPost<{ download_url: string }>("folder/zip", { grant, ...(folderId ? { folder: folderId } : {}) })).download_url);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  return (
    <div className="public-wide stack">
      <div className="public-head">
        <div>
          <div className="small muted">{t("pubf.sharedBy", { name: info.owner_name || "" })}</div>
          <nav className="breadcrumbs public-crumbs">
            {(data?.breadcrumbs || [{ id: "", name: info.name }]).map((b, i, arr) => (
              <span key={b.id || i}>
                {i > 0 && <Icon name="chevronRight" size={14} />}
                <button className="crumb" disabled={i === arr.length - 1} onClick={() => setFolderId(i === 0 ? null : b.id)}>
                  {b.name}
                </button>
              </span>
            ))}
          </nav>
          {info.note && <p className="public-note">{info.note}</p>}
          <div className="small muted">{t("publicShare.availableUntil", { date: formatDate(info.expires_at) })}</div>
        </div>
        <button className="btn btn-primary" onClick={downloadAll} disabled={!data || (data.files.length === 0 && data.folders.length === 0)}>
          <Icon name="archive" size={16} /> {t("pubf.downloadAll")}
        </button>
      </div>
      {error && <div className="form-error">{error}</div>}
      {!data && !error && <p className="muted">{t("publicShare.loading")}</p>}
      {data && data.folders.length === 0 && data.files.length === 0 && <div className="empty-small">{t("pubf.empty")}</div>}
      {data && (
        <div className="public-grid">
          {data.folders.map((d) => (
            <button key={d.id} className="public-tile" onClick={() => setFolderId(d.id)}>
              <span className="public-tile-img">
                <Icon name="folder" size={44} className="ico-folder" />
              </span>
              <span className="truncate">{d.name}</span>
            </button>
          ))}
          {data.files.map((f, i) => (
            <div key={f.id} className="public-tile">
              <button className="public-tile-img" onClick={() => setViewer(i)} aria-label={f.name}>
                <Thumb grant={grant} file={f} />
              </button>
              <span className="row-between public-tile-meta">
                <span className="truncate" title={f.name}>
                  {f.name}
                </span>
                <button className="icon-btn" onClick={() => download(f)} title={t("publicShare.download")} aria-label={t("publicShare.download")}>
                  <Icon name="download" size={15} />
                </button>
              </span>
              <span className="small muted">{formatBytes(f.size)}</span>
            </div>
          ))}
        </div>
      )}
      {data && viewer !== null && data.files[viewer] && (
        <Lightbox grant={grant} files={data.files} index={viewer} onIndex={setViewer} onClose={() => setViewer(null)} onDownload={download} />
      )}
    </div>
  );
}

// ─────────────────────────── Скринька ───────────────────────────

interface Sent {
  key: string;
  name: string;
  size: number;
  loaded: number;
  state: "queued" | "sending" | "done" | "error";
  error?: string;
}

async function sendFile(grant: string, sender: string, file: File, onProgress: (loaded: number) => void) {
  const start = await publicPost<{ upload: string; chunk_size: number; chunks_total: number }>("drop/start", {
    grant,
    name: file.name,
    size: file.size,
    sender,
  });
  const headers = { "X-Link-Grant": grant, "X-Upload-Token": start.upload };
  for (let i = 0; i < start.chunks_total; i++) {
    const blob = file.slice(i * start.chunk_size, (i + 1) * start.chunk_size);
    let attempt = 0;
    for (;;) {
      const r = await fetch(`/api/public/drop/chunk/${i}/`, {
        method: "PUT",
        credentials: "omit",
        headers: { "Content-Type": "application/octet-stream", ...headers },
        body: blob,
      });
      if (r.ok) break;
      const d = await r.json().catch(() => ({}));
      // Мережевий збій чи перевантаження — ще раз; помилка даних — одразу
      if (++attempt >= 3 || (r.status >= 400 && r.status < 500 && r.status !== 429)) throw new PublicError(d.detail || `HTTP ${r.status}`, r.status);
      await new Promise((res) => setTimeout(res, 1000 * attempt));
    }
    onProgress(Math.min(file.size, (i + 1) * start.chunk_size));
  }
  await publicPost("drop/complete", { grant, upload: start.upload }, headers);
}

export function DropBoxView({ grant, info }: { grant: string; info: PublicInfo }) {
  const t = useT();
  const [sender, setSender] = useState("");
  const [items, setItems] = useState<Sent[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const queue = useRef<{ key: string; file: File }[]>([]);

  const update = (key: string, patch: Partial<Sent>) => setItems((cur) => cur.map((x) => (x.key === key ? { ...x, ...patch } : x)));

  const run = useCallback(async () => {
    setBusy(true);
    while (queue.current.length) {
      const { key, file } = queue.current.shift()!;
      update(key, { state: "sending" });
      try {
        await sendFile(grant, sender.trim(), file, (loaded) => update(key, { loaded }));
        update(key, { state: "done", loaded: file.size });
      } catch (e) {
        const err = e as PublicError;
        update(key, { state: "error", error: err.status === 403 && /сесі|session/i.test(err.message) ? t("pubf.sessionExpired") : err.message });
      }
    }
    setBusy(false);
  }, [grant, sender, t]);

  const add = (list: FileList | null) => {
    setError("");
    if (!list || list.length === 0) return;
    if (sender.trim().length < 2) {
      setError(t("pubf.nameRequired"));
      return;
    }
    const added = Array.from(list).map((file, i) => ({ key: `${Date.now()}-${i}-${file.name}`, file }));
    setItems((cur) => [...cur, ...added.map(({ key, file }) => ({ key, name: file.name, size: file.size, loaded: 0, state: "queued" as const }))]);
    queue.current.push(...added);
    if (!busy) run();
  };

  const done = items.filter((x) => x.state === "done").length;

  return (
    <div className="public-drop stack">
      <div className="public-file">
        <Icon name="upload" size={28} />
        <div>
          <div className="public-name">{info.name}</div>
          <div className="muted small">{t("pubf.dropBy", { name: info.owner_name || "" })}</div>
        </div>
      </div>
      {info.note && <p className="public-note">{info.note}</p>}
      <label>
        {t("pubf.yourName")}
        <input value={sender} maxLength={60} autoFocus onChange={(e: ChangeEvent<HTMLInputElement>) => setSender(e.target.value)} disabled={busy} />
        <span className="small muted">{t("pubf.yourNameHint")}</span>
      </label>
      <button
        type="button"
        className={`drop-zone ${dragging ? "dragging" : ""}`}
        onClick={() => (sender.trim().length < 2 ? setError(t("pubf.nameRequired")) : input.current?.click())}
        onDragOver={(e: DragEvent) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e: DragEvent) => {
          e.preventDefault();
          setDragging(false);
          add(e.dataTransfer.files);
        }}
      >
        <Icon name="upload" size={30} />
        <span>{t("pubf.dropHere")}</span>
      </button>
      <input ref={input} type="file" multiple hidden onChange={(e) => (add(e.target.files), (e.target.value = ""))} />
      {error && <div className="form-error">{error}</div>}
      {items.length > 0 && (
        <ul className="drop-list">
          {items.map((x) => (
            <li key={x.key}>
              <span className="truncate">{x.name}</span>
              <span className="small muted nowrap">
                {x.state === "done" ? t("pubf.sent") : x.state === "error" ? x.error || t("pubf.failed") : `${Math.round((x.loaded / Math.max(1, x.size)) * 100)}%`}
              </span>
              <span className="progress">
                <span className={`progress-fill state-${x.state === "done" ? "done" : x.state === "error" ? "error" : "uploading"}`} style={{ width: `${x.size ? (x.loaded / x.size) * 100 : 100}%` }} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {busy && <div className="small muted">{t("pubf.sending", { done, total: items.length })}</div>}
      {!busy && done > 0 && <div className="banner banner-info small">{t("pubf.allSent", { n: done })}</div>}
      <p className="small muted">
        {t("pubf.cannotSee")} {t("publicShare.availableUntil", { date: formatDate(info.expires_at) })}
      </p>
    </div>
  );
}

export function PasswordGate({ onSubmit, busy }: { onSubmit: (password: string) => void; busy: boolean }) {
  const t = useT();
  const [password, setPassword] = useState("");
  return (
    <form
      className="stack"
      onSubmit={(e: FormEvent) => {
        e.preventDefault();
        onSubmit(password);
      }}
    >
      <label>
        {t("publicShare.password")}
        <input type="password" autoFocus value={password} onChange={(e) => setPassword(e.target.value)} />
      </label>
      <button className="btn btn-primary btn-block" disabled={busy}>
        <Icon name="lock" size={16} /> {t("pubf.open")}
      </button>
    </form>
  );
}
