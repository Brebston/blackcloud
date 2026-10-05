import { DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { BrowseResult, FileItem, Folder } from "../api/types";
import FileViewer from "../components/FileViewer";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import ShareDialog from "../components/ShareDialog";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { useEvents } from "../hooks/useEvents";
import { fileIcon, formatBytes, formatDate } from "../lib/format";
import { uploadFile, UploadProgress } from "../lib/upload";
import { downloadUrl, thumbnailUrl, viewKind } from "../lib/viewer";

type ViewMode = "list" | "grid";

function loadViewMode(): ViewMode {
  try {
    return localStorage.getItem("bc_files_view") === "grid" ? "grid" : "list";
  } catch {
    return "list";
  }
}

function FileThumb({ f, size = "sm" }: { f: FileItem; size?: "sm" | "lg" }) {
  const [failed, setFailed] = useState(false);
  if (f.has_thumbnail && f.downloadable && !failed) {
    return <img className={`thumb thumb-${size}`} src={thumbnailUrl(f)} alt="" loading="lazy" onError={() => setFailed(true)} />;
  }
  return <Icon name={fileIcon(f.mime_type, f.name)} size={size === "lg" ? 48 : 18} />;
}

// Підпис у панелі завантажень відображає актуальний статус файлу зі списку
function doneLabel(status?: string): string {
  switch (status) {
    case "scanning":
      return "перевірка антивірусом…";
    case "clean":
      return "готово";
    case "infected":
      return "заблоковано: вірус";
    case "unscanned":
      return "не перевірено (завеликий)";
    case "failed":
      return "помилка перевірки";
    default:
      return "завантажено";
  }
}

type Dialog =
  | { kind: "newFolder" }
  | { kind: "rename"; type: "file" | "folder"; id: string; name: string }
  | { kind: "move"; type: "file" | "folder"; id: string; name: string }
  | { kind: "share"; type: "file" | "folder"; id: string; name: string; downloadable?: boolean }
  | { kind: "info"; file: FileItem }
  | null;

export default function FilesPage() {
  const [params, setParams] = useSearchParams();
  const folderId = params.get("folder") || "root";
  const toast = useToast();
  const { user, refresh: refreshUser } = useAuth();
  const navigate = useNavigate();
  const officeEnabled = !!user?.features?.office;
  const [viewMode, setViewMode] = useState<ViewMode>(loadViewMode);
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const [data, setData] = useState<BrowseResult | null>(null);
  const [error, setError] = useState("");
  const [dialog, setDialog] = useState<Dialog>(null);
  const [uploads, setUploads] = useState<Record<string, UploadProgress>>({});
  const [dragging, setDragging] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<{ folders: Folder[]; files: FileItem[] } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    try {
      setError("");
      setData(await get<BrowseResult>(`/api/files/browse/?folder=${encodeURIComponent(folderId)}`));
    } catch (e) {
      setError(errorText(e));
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  useEvents((event, payload) => {
    if (event === "file.updated" && data?.files.some((f) => f.id === payload.id)) load();
  });

  useEffect(() => {
    if (query.trim().length < 2) {
      setSearchResults(null);
      return;
    }
    const t = setTimeout(() => {
      get<{ folders: Folder[]; files: FileItem[] }>(`/api/files/search/?q=${encodeURIComponent(query.trim())}`)
        .then(setSearchResults)
        .catch(() => setSearchResults(null));
    }, 300);
    return () => clearTimeout(t);
  }, [query]);

  const openFolder = (id: string | null) => {
    setQuery("");
    setParams(id ? { folder: id } : {});
  };

  const startUploads = async (files: FileList | File[]) => {
    if (!data?.writable) return;
    const target = data.folder?.id || null;
    abortRef.current = new AbortController();
    for (const file of Array.from(files)) {
      try {
        await uploadFile(
          file,
          target,
          (p) => setUploads((u) => ({ ...u, [p.id]: p })),
          abortRef.current.signal,
        );
      } catch (e) {
        toast(`${file.name}: ${errorText(e)}`, "error");
      }
    }
    load();
    refreshUser();
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) startUploads(e.dataTransfer.files);
  };

  const remove = async (type: "file" | "folder", id: string, name: string) => {
    if (!window.confirm(`Перемістити «${name}» у кошик?`)) return;
    try {
      await del(type === "file" ? `/api/files/items/${id}/` : `/api/files/folders/${id}/`);
      toast("Переміщено в кошик", "success");
      load();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const activeUploads = Object.values(uploads).filter((u) => u.state === "uploading");
  const finishedUploads = Object.values(uploads).filter((u) => u.state !== "uploading");
  const listFolders = searchResults ? searchResults.folders : data?.folders || [];
  const listFiles = searchResults ? searchResults.files : data?.files || [];
  const writable = !!data?.writable && !searchResults;
  const viewable = listFiles.filter((f) => viewKind(f, officeEnabled) !== null);

  const openFile = (f: FileItem) => {
    const kind = viewKind(f, officeEnabled);
    if (kind === "office") navigate(`/edit/${f.id}`);
    else if (kind) setViewerIndex(viewable.findIndex((x) => x.id === f.id));
    else setDialog({ kind: "info", file: f });
  };

  const switchView = (mode: ViewMode) => {
    setViewMode(mode);
    try {
      localStorage.setItem("bc_files_view", mode);
    } catch {
      /* приватний режим — просто не запам'ятовуємо */
    }
  };

  const fileActions = (f: FileItem) => (
    <div className="row-actions">
      {f.downloadable && (
        <a className="icon-btn" title="Завантажити" href={downloadUrl(f)}>
          <Icon name="download" size={16} />
        </a>
      )}
      {writable && (
        <>
          <button className="icon-btn" title="Поділитися" onClick={() => setDialog({ kind: "share", type: "file", id: f.id, name: f.name, downloadable: f.downloadable })}>
            <Icon name="share" size={16} />
          </button>
          <button className="icon-btn" title="Перейменувати" onClick={() => setDialog({ kind: "rename", type: "file", id: f.id, name: f.name })}>
            <Icon name="edit" size={16} />
          </button>
          <button className="icon-btn" title="Перемістити" onClick={() => setDialog({ kind: "move", type: "file", id: f.id, name: f.name })}>
            <Icon name="move" size={16} />
          </button>
          <button className="icon-btn danger" title="У кошик" onClick={() => remove("file", f.id, f.name)}>
            <Icon name="trash" size={16} />
          </button>
        </>
      )}
    </div>
  );

  const statusPills = (f: FileItem) => (
    <>
      {f.status === "scanning" && <span className="pill">перевірка</span>}
      {f.status === "infected" && <span className="pill pill-danger">вірус</span>}
      {f.status === "unscanned" && <span className="pill pill-warning">не перевірено</span>}
      {f.status === "failed" && <span className="pill pill-danger">помилка</span>}
    </>
  );

  return (
    <div
      className={`page ${dragging ? "dragging" : ""}`}
      onDragOver={(e) => {
        if (!data?.writable) return;
        e.preventDefault();
        setDragging(true);
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={onDrop}
    >
      <div className="page-head">
        <div className="breadcrumbs">
          <button className="crumb" onClick={() => openFolder(null)}>
            {data && !data.writable ? `Спільне від ${data.owner}` : "Мої файли"}
          </button>
          {data?.breadcrumbs.map((b) => (
            <span key={b.id}>
              <Icon name="chevronRight" size={14} />
              <button className="crumb" onClick={() => openFolder(b.id)}>
                {b.name}
              </button>
            </span>
          ))}
          {data && !data.writable && <span className="pill">лише читання</span>}
        </div>
        <div className="toolbar">
          <div className="search">
            <Icon name="search" size={16} />
            <input placeholder="Пошук файлів…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div className="segmented" role="group" aria-label="Вигляд">
            <button className={viewMode === "list" ? "active" : ""} onClick={() => switchView("list")} aria-label="Список" title="Список">
              <Icon name="list" size={16} />
            </button>
            <button className={viewMode === "grid" ? "active" : ""} onClick={() => switchView("grid")} aria-label="Сітка" title="Сітка">
              <Icon name="grid" size={16} />
            </button>
          </div>
          {data?.writable && (
            <>
              <button className="btn" onClick={() => setDialog({ kind: "newFolder" })}>
                <Icon name="plus" size={16} /> Папка
              </button>
              <button className="btn btn-primary" onClick={() => inputRef.current?.click()}>
                <Icon name="upload" size={16} /> Завантажити
              </button>
              <input
                ref={inputRef}
                type="file"
                multiple
                hidden
                onChange={(e) => {
                  if (e.target.files) startUploads(e.target.files);
                  e.target.value = "";
                }}
              />
            </>
          )}
        </div>
      </div>

      {error && <div className="banner banner-danger">{error}</div>}

      {(activeUploads.length > 0 || finishedUploads.length > 0) && (
        <div className="uploads card">
          <div className="row-between">
            <strong>Завантаження</strong>
            <div>
              {activeUploads.length > 0 && (
                <button className="link-btn danger" onClick={() => abortRef.current?.abort()}>
                  Скасувати
                </button>
              )}
              {activeUploads.length === 0 && (
                <button className="link-btn" onClick={() => setUploads({})}>
                  Очистити
                </button>
              )}
            </div>
          </div>
          {Object.values(uploads).map((u) => (
            <div key={u.id} className="upload-row">
              <span className="truncate">{u.name}</span>
              <div className="progress">
                <div className={`progress-fill state-${u.state}`} style={{ width: `${u.total ? (u.loaded / u.total) * 100 : 100}%` }} />
              </div>
              <span className="small muted">
                {u.state === "uploading" && `${formatBytes(u.loaded)} / ${formatBytes(u.total)}`}
                {u.state === "done" && doneLabel(data?.files.find((f) => f.id === u.id)?.status)}
                {u.state === "error" && (u.error || "помилка")}
                {u.state === "cancelled" && "скасовано"}
              </span>
            </div>
          ))}
        </div>
      )}

      {viewMode === "grid" ? (
        <div className="file-grid">
          {data?.folder && !searchResults && (
            <button className="grid-card grid-back" onClick={() => openFolder(data.breadcrumbs.length > 1 ? data.breadcrumbs[data.breadcrumbs.length - 2].id : null)}>
              <div className="grid-preview">
                <Icon name="chevronLeft" size={40} />
              </div>
              <div className="grid-name muted">Назад</div>
            </button>
          )}
          {listFolders.map((f) => (
            <button key={f.id} className="grid-card" onClick={() => openFolder(f.id)}>
              <div className="grid-preview">
                <Icon name="folder" size={52} className="ico-folder" />
              </div>
              <div className="grid-name truncate">{f.name}</div>
            </button>
          ))}
          {listFiles.map((f) => (
            <div key={f.id} className="grid-card">
              <button className="grid-preview" onClick={() => openFile(f)} aria-label={f.name}>
                <FileThumb f={f} size="lg" />
              </button>
              <div className="grid-name">
                <button className="link-btn truncate" onClick={() => openFile(f)} title={f.name}>
                  {f.name}
                </button>
              </div>
              <div className="grid-meta">
                <span className="muted small">{formatBytes(f.size)}</span>
                {statusPills(f)}
              </div>
              <div className="grid-actions">{fileActions(f)}</div>
            </div>
          ))}
          {data && listFolders.length === 0 && listFiles.length === 0 && (
            <div className="empty grid-empty">
              <Icon name={searchResults ? "search" : "upload"} size={32} />
              <div>{searchResults ? "Нічого не знайдено" : data.writable ? "Перетягніть файли сюди або натисніть «Завантажити»" : "Папка порожня"}</div>
            </div>
          )}
        </div>
      ) : (
      <div className="card table-card">
        <table className="table files-table">
          <thead>
            <tr>
              <th>Назва</th>
              <th className="col-size">Розмір</th>
              <th className="col-date">Змінено</th>
              <th className="col-actions" aria-label="Дії" />
            </tr>
          </thead>
          <tbody>
            {data?.folder && !searchResults && (
              <tr className="clickable" onClick={() => openFolder(data.breadcrumbs.length > 1 ? data.breadcrumbs[data.breadcrumbs.length - 2].id : null)}>
                <td colSpan={4} className="muted">
                  <Icon name="chevronLeft" size={16} /> Назад
                </td>
              </tr>
            )}
            {listFolders.map((f) => (
              <tr key={f.id} className="clickable" onDoubleClick={() => openFolder(f.id)}>
                <td onClick={() => openFolder(f.id)}>
                  <span className="name-cell">
                    <Icon name="folder" className="ico-folder" />
                    <span className="truncate">{f.name}</span>
                  </span>
                </td>
                <td className="col-size muted">—</td>
                <td className="col-date muted">{formatDate(f.updated_at)}</td>
                <td className="col-actions">
                  {writable && (
                    <div className="row-actions">
                      <button className="icon-btn" title="Поділитися" onClick={() => setDialog({ kind: "share", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="share" size={16} />
                      </button>
                      <button className="icon-btn" title="Перейменувати" onClick={() => setDialog({ kind: "rename", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="edit" size={16} />
                      </button>
                      <button className="icon-btn" title="Перемістити" onClick={() => setDialog({ kind: "move", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="move" size={16} />
                      </button>
                      <button className="icon-btn danger" title="У кошик" onClick={() => remove("folder", f.id, f.name)}>
                        <Icon name="trash" size={16} />
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
            {listFiles.map((f) => (
              <tr key={f.id}>
                <td>
                  <span className="name-cell">
                    <button className="thumb-btn" onClick={() => openFile(f)} tabIndex={-1} aria-hidden="true">
                      <FileThumb f={f} />
                    </button>
                    <button className="link-btn truncate" onClick={() => openFile(f)}>
                      {f.name}
                    </button>
                    {statusPills(f)}
                    {f.shared && <Icon name="share" size={13} className="muted" />}
                  </span>
                </td>
                <td className="col-size muted">{formatBytes(f.size)}</td>
                <td className="col-date muted">{formatDate(f.updated_at)}</td>
                <td className="col-actions">{fileActions(f)}</td>
              </tr>
            ))}
            {data && listFolders.length === 0 && listFiles.length === 0 && (
              <tr>
                <td colSpan={4}>
                  <div className="empty">
                    <Icon name={searchResults ? "search" : "upload"} size={32} />
                    <div>{searchResults ? "Нічого не знайдено" : data.writable ? "Перетягніть файли сюди або натисніть «Завантажити»" : "Папка порожня"}</div>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}

      {dialog?.kind === "newFolder" && (
        <NameDialog
          title="Нова папка"
          initial=""
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            await post("/api/files/folders/", { name, parent: data?.folder?.id || null });
            load();
          }}
        />
      )}
      {dialog?.kind === "rename" && (
        <NameDialog
          title="Перейменувати"
          initial={dialog.name}
          onClose={() => setDialog(null)}
          onSubmit={async (name) => {
            await patch(dialog.type === "file" ? `/api/files/items/${dialog.id}/` : `/api/files/folders/${dialog.id}/`, { name });
            load();
          }}
        />
      )}
      {dialog?.kind === "move" && (
        <MoveDialog
          item={dialog}
          onClose={() => setDialog(null)}
          onMoved={() => {
            setDialog(null);
            load();
          }}
        />
      )}
      {dialog?.kind === "share" && <ShareDialog target={dialog} onClose={() => { setDialog(null); load(); }} />}
      {viewerIndex !== null && viewable[viewerIndex] && (
        <FileViewer
          files={viewable}
          index={viewerIndex}
          officeEnabled={officeEnabled}
          onIndex={setViewerIndex}
          onClose={() => setViewerIndex(null)}
        />
      )}
      {dialog?.kind === "info" && <InfoDialog file={dialog.file} onClose={() => setDialog(null)} />}
      {dragging && (
        <div className="drop-overlay">
          <Icon name="upload" size={40} />
          Відпустіть, щоб завантажити
        </div>
      )}
    </div>
  );
}

function NameDialog({
  title,
  initial,
  onClose,
  onSubmit,
}: {
  title: string;
  initial: string;
  onClose: () => void;
  onSubmit: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initial);
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const clean = name.trim();
    if (!clean) return setError("Введіть назву.");
    if (/[/\\]/.test(clean)) return setError("Назва не може містити / або \\.");
    if (clean.length > 255) return setError("Максимум 255 символів.");
    try {
      await onSubmit(clean);
      onClose();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Modal title={title} onClose={onClose}>
      <form onSubmit={submit} className="stack">
        <input autoFocus value={name} onChange={(e) => { setName(e.target.value); setError(""); }} />
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            Скасувати
          </button>
          <button className="btn btn-primary">Зберегти</button>
        </div>
      </form>
    </Modal>
  );
}

function MoveDialog({
  item,
  onClose,
  onMoved,
}: {
  item: { type: "file" | "folder"; id: string; name: string };
  onClose: () => void;
  onMoved: () => void;
}) {
  const [current, setCurrent] = useState<string>("root");
  const [data, setData] = useState<BrowseResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    get<BrowseResult>(`/api/files/browse/?folder=${current}`).then(setData).catch((e) => setError(errorText(e)));
  }, [current]);

  const move = async () => {
    try {
      await patch(item.type === "file" ? `/api/files/items/${item.id}/` : `/api/files/folders/${item.id}/`, {
        target: current,
      });
      onMoved();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal title={`Перемістити «${item.name}»`} onClose={onClose}>
      <div className="breadcrumbs small">
        <button className="crumb" onClick={() => setCurrent("root")}>
          Мої файли
        </button>
        {data?.breadcrumbs.map((b) => (
          <span key={b.id}>
            <Icon name="chevronRight" size={12} />
            <button className="crumb" onClick={() => setCurrent(b.id)}>
              {b.name}
            </button>
          </span>
        ))}
      </div>
      <div className="folder-picker">
        {data?.folders
          .filter((f) => f.id !== item.id)
          .map((f) => (
            <button key={f.id} onClick={() => setCurrent(f.id)}>
              <Icon name="folder" size={16} /> {f.name}
            </button>
          ))}
        {data && data.folders.length === 0 && <div className="muted small">Немає вкладених папок</div>}
      </div>
      {error && <div className="form-error">{error}</div>}
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          Скасувати
        </button>
        <button className="btn btn-primary" onClick={move}>
          Перемістити сюди
        </button>
      </div>
    </Modal>
  );
}

function InfoDialog({ file, onClose }: { file: FileItem; onClose: () => void }) {
  return (
    <Modal title={file.name} onClose={onClose}>
      <dl className="info-list">
        <dt>Розмір</dt>
        <dd>{formatBytes(file.size)}</dd>
        <dt>Тип</dt>
        <dd>{file.mime_type}</dd>
        <dt>Статус</dt>
        <dd>
          {file.status_display}
          {file.scan_detail && <div className="small muted">{file.scan_detail}</div>}
        </dd>
        <dt>SHA-256</dt>
        <dd>
          <code className="break small">{file.sha256 || "—"}</code>
        </dd>
        <dt>Власник</dt>
        <dd>{file.owner}</dd>
        <dt>Створено</dt>
        <dd>{formatDate(file.created_at)}</dd>
      </dl>
      <div className="modal-actions">
        {file.downloadable ? (
          <a className="btn btn-primary" href={`/api/files/items/${file.id}/download/`}>
            <Icon name="download" size={16} /> Завантажити
          </a>
        ) : (
          <span className="muted small">Файл недоступний для завантаження.</span>
        )}
      </div>
      <p className="small muted">
        Файли зберігаються зашифрованими (AES-256-GCM) і перевіряються антивірусом. <Link to="/trash">Кошик</Link>
      </p>
    </Modal>
  );
}
