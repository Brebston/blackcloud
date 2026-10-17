import { DragEvent, FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { BrowseResult, FileItem, FileTag, Folder } from "../api/types";
import FileViewer from "../components/FileViewer";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import PullToRefresh from "../components/PullToRefresh";
import ShareDialog from "../components/ShareDialog";
import ShareImportDialog from "../components/ShareImportDialog";
import TagsDialog, { TagDot } from "../components/TagsDialog";
import VersionsDialog from "../components/VersionsDialog";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { useEvents } from "../hooks/useEvents";
import { useIsMobile } from "../hooks/useIsMobile";
import { useT } from "../i18n";
import type { TFunc } from "../i18n";
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
    const img = <img className={`thumb thumb-${size}`} src={thumbnailUrl(f)} alt="" loading="lazy" onError={() => setFailed(true)} />;
    if (!f.mime_type.startsWith("video/")) return img;
    return (
      <span className={`thumb-video thumb-video-${size}`}>
        {img}
        <span className="thumb-play" aria-hidden="true" />
      </span>
    );
  }
  return <Icon name={fileIcon(f.mime_type, f.name)} size={size === "lg" ? 48 : 18} />;
}

// Підпис у панелі завантажень відображає актуальний статус файлу зі списку
function doneLabel(t: TFunc, status?: string): string {
  switch (status) {
    case "scanning":
      return t("files.upload.scanning");
    case "clean":
      return t("files.upload.clean");
    case "infected":
      return t("files.upload.infected");
    case "unscanned":
      return t("files.upload.unscanned");
    case "failed":
      return t("files.upload.failed");
    default:
      return t("files.upload.done");
  }
}

type Dialog =
  | { kind: "newFolder" }
  | { kind: "rename"; type: "file" | "folder"; id: string; name: string }
  | { kind: "move"; type: "file" | "folder"; id: string; name: string }
  | { kind: "share"; type: "file" | "folder"; id: string; name: string; downloadable?: boolean }
  | { kind: "info"; file: FileItem }
  | { kind: "versions"; file: FileItem }
  | null;

export default function FilesPage() {
  const [params, setParams] = useSearchParams();
  const folderId = params.get("folder") || "root";
  // Добірки: ?view=starred | recent | tag&tag=<id> (замість вмісту теки)
  const view = params.get("view") as "starred" | "recent" | "tag" | null;
  const tagId = params.get("tag");
  const collection = view === "starred" || view === "recent" || (view === "tag" && !!tagId);
  // ?share=<id> — дані з меню «Поділитися» Android (див. public/sw.js, lib/shareInbox.ts)
  const shareId = params.get("share");
  const mobile = useIsMobile();
  const toast = useToast();
  const t = useT();
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
  const versionInputRef = useRef<HTMLInputElement>(null);
  const [versionTarget, setVersionTarget] = useState<FileItem | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  // Телефон: дії з файлом/текою — у нижній шторці замість ряду дрібних іконок
  const [actionSheet, setActionSheet] = useState<{ file?: FileItem; folder?: Folder } | null>(null);
  const [tags, setTags] = useState<FileTag[]>([]);
  const [tagsDialog, setTagsDialog] = useState<{ type: "file" | "folder"; id: string; name: string; tags: string[] } | "manage" | null>(null);

  const loadTags = useCallback(() => {
    get<FileTag[]>("/api/files/tags/").then(setTags).catch(() => {});
  }, []);
  useEffect(loadTags, [loadTags]);
  const tagById = new Map(tags.map((tg) => [tg.id, tg]));

  const load = useCallback(async () => {
    try {
      setError("");
      if (collection) {
        const url = view === "starred" ? "/api/files/starred/" : view === "recent" ? "/api/files/recent/" : `/api/files/tags/${tagId}/items/`;
        const r = await get<{ folders: Folder[]; files: FileItem[] }>(url);
        setData({ folder: null, writable: false, owner: "", breadcrumbs: [], folders: r.folders, files: r.files });
      } else {
        setData(await get<BrowseResult>(`/api/files/browse/?folder=${encodeURIComponent(folderId)}`));
      }
    } catch (e) {
      setError(errorText(e));
    }
  }, [folderId, collection, view, tagId]);

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
    const timer = setTimeout(() => {
      get<{ folders: Folder[]; files: FileItem[] }>(`/api/files/search/?q=${encodeURIComponent(query.trim())}`)
        .then(setSearchResults)
        .catch(() => setSearchResults(null));
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const openFolder = (id: string | null) => {
    setQuery("");
    setParams(id ? { folder: id } : {});
  };

  const openView = (v: "starred" | "recent" | "tag" | null, tag?: string) => {
    setQuery("");
    setParams(v ? { view: v, ...(tag ? { tag } : {}) } : {});
  };

  const toggleStar = async (type: "file" | "folder", item: { id: string; starred?: boolean }) => {
    const starred = !item.starred;
    // Одразу в інтерфейсі, потім на сервері
    setData((cur) =>
      cur && {
        ...cur,
        folders: type === "folder" ? cur.folders.map((x) => (x.id === item.id ? { ...x, starred } : x)) : cur.folders,
        files: type === "file" ? cur.files.map((x) => (x.id === item.id ? { ...x, starred } : x)) : cur.files,
      },
    );
    try {
      await post("/api/files/star/", { [type]: item.id, starred });
      if (view === "starred") load();
    } catch (e) {
      toast(errorText(e), "error");
      load();
    }
  };

  const starButton = (type: "file" | "folder", item: { id: string; name: string; starred?: boolean }) => (
    <button
      className={`icon-btn star-btn ${item.starred ? "on" : ""}`}
      onClick={(e) => {
        e.stopPropagation();
        toggleStar(type, item);
      }}
      aria-pressed={!!item.starred}
      aria-label={item.starred ? t("org.unstar") : t("org.star")}
      title={item.starred ? t("org.unstar") : t("org.star")}
    >
      <Icon name="star" size={15} />
    </button>
  );

  const tagDots = (ids?: string[]) => {
    const list = (ids || []).map((id) => tagById.get(id)).filter((x): x is FileTag => !!x);
    return list.length ? (
      <span className="tag-dots" title={list.map((x) => x.name).join(", ")}>
        {list.map((x) => (
          <TagDot key={x.id} tag={x} size={9} />
        ))}
      </span>
    ) : null;
  };

  const editTags = (type: "file" | "folder", item: { id: string; name: string; tags?: string[] }) =>
    setTagsDialog({ type, id: item.id, name: item.name, tags: item.tags || [] });

  const startUploads = async (files: FileList | File[]) => {
    if (!data?.writable) return;
    const target = data.folder?.id || null;
    const list = Array.from(files);
    // Файли з іменами, що вже є в папці: одне питання на всю партію —
    // зберегти як нові версії чи (як раніше) обидва з різними іменами
    const existing = new Map(data.files.map((f) => [f.name, f]));
    const conflicts = list.filter((f) => existing.has(f.name));
    let asVersions = false;
    if (conflicts.length > 0) {
      const names = conflicts.slice(0, 5).map((f) => `«${f.name}»`).join(", ") + (conflicts.length > 5 ? ", …" : "");
      asVersions = window.confirm(t("files2.replaceConfirm", { n: conflicts.length, names }));
    }
    abortRef.current = new AbortController();
    for (const file of list) {
      const replace = asVersions ? existing.get(file.name) : undefined;
      try {
        await uploadFile(
          file,
          target,
          (p) => setUploads((u) => ({ ...u, [p.id]: p })),
          abortRef.current.signal,
          replace ? { replaceFileId: replace.id } : {},
        );
      } catch (e) {
        toast(`${file.name}: ${errorText(e)}`, "error");
      }
    }
    load();
    refreshUser();
  };

  /** Нова версія конкретного файлу (дія в рядку або в діалозі історії). */
  const uploadVersion = async (picked: File, target: FileItem) => {
    abortRef.current = new AbortController();
    try {
      await uploadFile(
        picked,
        target.folder,
        (p) => setUploads((u) => ({ ...u, [p.id]: { ...p, name: target.name } })),
        abortRef.current.signal,
        { replaceFileId: target.id },
      );
      toast(t("files2.versionUploaded", { name: target.name }), "success");
    } catch (e) {
      toast(`${target.name}: ${errorText(e)}`, "error");
    }
    load();
    refreshUser();
  };

  /** ZIP: спершу перевірка лімітів (?check=1), щоб помилку показати тут, а не сирим JSON. */
  const downloadZip = async (url: string) => {
    try {
      const info = await get<{ files: number; bytes: number; skipped: number }>(`${url}${url.includes("?") ? "&" : "?"}check=1`);
      if (info.files === 0 && info.skipped === 0) {
        toast(t("files2.zipEmpty"), "error");
        return;
      }
      toast(info.skipped > 0 ? t("files2.zipSkipped", { n: info.skipped }) : t("files2.zipPreparing"), "info");
      const a = document.createElement("a");
      a.href = url;
      a.rel = "noopener";
      document.body.appendChild(a);
      a.click();
      a.remove();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (e.dataTransfer.files.length) startUploads(e.dataTransfer.files);
  };

  const remove = async (type: "file" | "folder", id: string, name: string) => {
    if (!window.confirm(t("files.confirmTrash", { name }))) return;
    try {
      await del(type === "file" ? `/api/files/items/${id}/` : `/api/files/folders/${id}/`);
      toast(t("files.movedToTrash"), "success");
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

  const moreButton = (target: { file?: FileItem; folder?: Folder }, name: string) => (
    <div className="row-actions">
      <button className="icon-btn" title={t("mobile.actionsFor", { name })} aria-label={t("mobile.actionsFor", { name })} onClick={() => setActionSheet(target)}>
        <Icon name="more" />
      </button>
    </div>
  );

  const fileActions = (f: FileItem) => mobile ? moreButton({ file: f }, f.name) : (
    <div className="row-actions">
      {collection && (
        <button className="icon-btn" title={t("org.openFolder")} aria-label={t("org.openFolder")} onClick={() => openFolder(f.folder)}>
          <Icon name="folder" size={16} />
        </button>
      )}
      <button className="icon-btn" title={t("org.editTags")} aria-label={t("org.editTags")} onClick={() => editTags("file", f)}>
        <Icon name="tag" size={16} />
      </button>
      {f.downloadable && (
        <a className="icon-btn" title={t("files.download")} href={downloadUrl(f)}>
          <Icon name="download" size={16} />
        </a>
      )}
      {writable && (
        <>
          <button className="icon-btn" title={t("files2.versions")} aria-label={t("files2.versions")} onClick={() => setDialog({ kind: "versions", file: f })}>
            <Icon name="clock" size={16} />
          </button>
          <button
            className="icon-btn"
            title={t("files2.uploadVersion")}
            aria-label={t("files2.uploadVersion")}
            onClick={() => {
              setVersionTarget(f);
              versionInputRef.current?.click();
            }}
          >
            <Icon name="upload" size={16} />
          </button>
          <button className="icon-btn" title={t("files.share")} onClick={() => setDialog({ kind: "share", type: "file", id: f.id, name: f.name, downloadable: f.downloadable })}>
            <Icon name="share" size={16} />
          </button>
          <button className="icon-btn" title={t("files.rename")} onClick={() => setDialog({ kind: "rename", type: "file", id: f.id, name: f.name })}>
            <Icon name="edit" size={16} />
          </button>
          <button className="icon-btn" title={t("files.move")} onClick={() => setDialog({ kind: "move", type: "file", id: f.id, name: f.name })}>
            <Icon name="move" size={16} />
          </button>
          <button className="icon-btn danger" title={t("files.toTrash")} onClick={() => remove("file", f.id, f.name)}>
            <Icon name="trash" size={16} />
          </button>
        </>
      )}
    </div>
  );

  const statusPills = (f: FileItem) => (
    <>
      {f.status === "scanning" && <span className="pill">{t("files.pill.scanning")}</span>}
      {f.status === "infected" && <span className="pill pill-danger">{t("files.pill.infected")}</span>}
      {f.status === "unscanned" && <span className="pill pill-warning">{t("files.pill.unscanned")}</span>}
      {f.status === "failed" && <span className="pill pill-danger">{t("files.pill.failed")}</span>}
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
          {collection ? (
            <span className="crumb crumb-current">
              {view === "starred" ? t("org.starred") : view === "recent" ? t("org.recent") : tagById.get(tagId || "")?.name || t("org.tags")}
            </span>
          ) : (
          <button className="crumb" onClick={() => openFolder(null)}>
            {data && !data.writable ? t("files.sharedFrom", { owner: data.owner }) : t("files.myFiles")}
          </button>
          )}
          {data?.breadcrumbs.map((b) => (
            <span key={b.id}>
              <Icon name="chevronRight" size={14} />
              <button className="crumb" onClick={() => openFolder(b.id)}>
                {b.name}
              </button>
            </span>
          ))}
          {data && !data.writable && !collection && <span className="pill">{t("files.readOnly")}</span>}
        </div>
        <div className="toolbar">
          <div className="search">
            <Icon name="search" size={16} />
            <input placeholder={t("files.search")} value={query} onChange={(e) => setQuery(e.target.value)} />
          </div>
          <div className="segmented" role="group" aria-label={t("files.view")}>
            <button className={viewMode === "list" ? "active" : ""} onClick={() => switchView("list")} aria-label={t("files.viewList")} title={t("files.viewList")}>
              <Icon name="list" size={16} />
            </button>
            <button className={viewMode === "grid" ? "active" : ""} onClick={() => switchView("grid")} aria-label={t("files.viewGrid")} title={t("files.viewGrid")}>
              <Icon name="grid" size={16} />
            </button>
          </div>
          {data?.writable && (
            <>
              <button className="btn" onClick={() => setDialog({ kind: "newFolder" })} title={t("files.newFolder")} aria-label={t("files.newFolder")}>
                <Icon name="folderPlus" size={16} /> <span className="hide-narrow">{t("files.folder")}</span>
              </button>
              <button className="btn btn-primary" onClick={() => inputRef.current?.click()}>
                <Icon name="upload" size={16} /> {t("files.upload")}
              </button>
              {/* Телефон: одразу камера (звичайний вибір файлів — кнопка «Завантажити») */}
              <label className="btn mobile-only" title={t("mobile.takePhoto")} aria-label={t("mobile.takePhoto")}>
                <Icon name="camera" size={16} />
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  hidden
                  onChange={(e) => {
                    if (e.target.files) startUploads(e.target.files);
                    e.target.value = "";
                  }}
                />
              </label>
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
              <input
                ref={versionInputRef}
                type="file"
                hidden
                onChange={(e) => {
                  const picked = e.target.files?.[0];
                  e.target.value = "";
                  if (picked && versionTarget) uploadVersion(picked, versionTarget);
                  setVersionTarget(null);
                }}
              />
            </>
          )}
          {data?.folder && !searchResults && (
            <button className="btn" title={t("files2.zipFolder")} onClick={() => downloadZip(`/api/files/folders/${data.folder!.id}/zip/`)}>
              <Icon name="archive" size={16} /> {t("files2.zip")}
            </button>
          )}
        </div>
      </div>

      <nav className="org-nav" aria-label={t("org.tags")}>
        <button className={`org-chip ${!collection && folderId === "root" ? "active" : ""}`} onClick={() => openFolder(null)}>
          <Icon name="folder" size={14} /> {t("org.myFiles")}
        </button>
        <button className={`org-chip ${view === "starred" ? "active" : ""}`} onClick={() => openView("starred")}>
          <Icon name="star" size={14} /> {t("org.starred")}
        </button>
        <button className={`org-chip ${view === "recent" ? "active" : ""}`} onClick={() => openView("recent")}>
          <Icon name="clock" size={14} /> {t("org.recent")}
        </button>
        {tags.map((tg) => (
          <button key={tg.id} className={`org-chip ${view === "tag" && tagId === tg.id ? "active" : ""}`} onClick={() => openView("tag", tg.id)}>
            <TagDot tag={tg} /> {tg.name}
          </button>
        ))}
        <button className="icon-btn" title={t("org.manageTags")} aria-label={t("org.manageTags")} onClick={() => setTagsDialog("manage")}>
          <Icon name="tag" size={16} />
        </button>
      </nav>

      {error && <div className="banner banner-danger">{error}</div>}

      {(activeUploads.length > 0 || finishedUploads.length > 0) && (
        <div className="uploads card">
          <div className="row-between">
            <strong>{t("files.uploads")}</strong>
            <div>
              {activeUploads.length > 0 && (
                <button className="link-btn danger" onClick={() => abortRef.current?.abort()}>
                  {t("common.cancel")}
                </button>
              )}
              {activeUploads.length === 0 && (
                <button className="link-btn" onClick={() => setUploads({})}>
                  {t("files.clear")}
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
                {u.state === "done" && doneLabel(t, data?.files.find((f) => f.id === u.id)?.status)}
                {u.state === "error" && (u.error || t("files.upload.error"))}
                {u.state === "cancelled" && t("files.upload.cancelled")}
              </span>
            </div>
          ))}
        </div>
      )}

      <PullToRefresh enabled={mobile} onRefresh={load}>
      {viewMode === "grid" ? (
        <div className="file-grid">
          {data?.folder && !searchResults && (
            <button className="grid-card grid-back" onClick={() => openFolder(data.breadcrumbs.length > 1 ? data.breadcrumbs[data.breadcrumbs.length - 2].id : null)}>
              <div className="grid-preview">
                <Icon name="chevronLeft" size={40} />
              </div>
              <div className="grid-name muted">{t("files.back")}</div>
            </button>
          )}
          {listFolders.map((f) => (
            <div key={f.id} className="grid-card">
              <button className="grid-preview" onClick={() => openFolder(f.id)} aria-label={f.name}>
                <Icon name="folder" size={52} className="ico-folder" />
              </button>
              <div className="grid-name truncate">
                {tagDots(f.tags)} {f.name}
              </div>
              {starButton("folder", f)}
            </div>
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
                {tagDots(f.tags)}
                {statusPills(f)}
              </div>
              {starButton("file", f)}
              <div className="grid-actions">{fileActions(f)}</div>
            </div>
          ))}
          {data && listFolders.length === 0 && listFiles.length === 0 && (
            <div className="empty grid-empty">
              <Icon name={searchResults ? "search" : "upload"} size={32} />
              <div>
                {searchResults
                  ? t("files.nothingFound")
                  : view === "starred"
                    ? t("org.starredEmpty")
                    : view === "recent"
                      ? t("org.recentEmpty")
                      : view === "tag"
                        ? t("org.tagEmpty")
                        : data.writable
                          ? t("files.dropHint")
                          : t("files.emptyFolder")}
              </div>
            </div>
          )}
        </div>
      ) : (
      <div className="card table-card">
        <table className="table files-table">
          <thead>
            <tr>
              <th>{t("files.colName")}</th>
              <th className="col-size">{t("files.colSize")}</th>
              <th className="col-date">{t("files.colModified")}</th>
              <th className="col-actions" aria-label={t("files.colActions")} />
            </tr>
          </thead>
          <tbody>
            {data?.folder && !searchResults && (
              <tr className="clickable" onClick={() => openFolder(data.breadcrumbs.length > 1 ? data.breadcrumbs[data.breadcrumbs.length - 2].id : null)}>
                <td colSpan={4} className="muted">
                  <Icon name="chevronLeft" size={16} /> {t("files.back")}
                </td>
              </tr>
            )}
            {listFolders.map((f) => (
              <tr key={f.id} className="clickable" onDoubleClick={() => openFolder(f.id)}>
                <td onClick={() => openFolder(f.id)}>
                  <span className="name-cell">
                    <Icon name="folder" className="ico-folder" />
                    <span className="truncate">{f.name}</span>
                    {tagDots(f.tags)}
                    {starButton("folder", f)}
                  </span>
                </td>
                <td className="col-size muted">—</td>
                <td className="col-date muted">{formatDate(f.updated_at)}</td>
                <td className="col-actions">
                  {mobile ? moreButton({ folder: f }, f.name) : (
                  <div className="row-actions">
                    <button className="icon-btn" title={t("org.editTags")} aria-label={t("org.editTags")} onClick={() => editTags("folder", f)}>
                      <Icon name="tag" size={16} />
                    </button>
                    <button className="icon-btn" title={t("files2.zipFolder")} aria-label={t("files2.zipFolder")} onClick={() => downloadZip(`/api/files/folders/${f.id}/zip/`)}>
                      <Icon name="archive" size={16} />
                    </button>
                    {writable && (
                    <>
                      <button className="icon-btn" title={t("files.share")} onClick={() => setDialog({ kind: "share", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="share" size={16} />
                      </button>
                      <button className="icon-btn" title={t("files.rename")} onClick={() => setDialog({ kind: "rename", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="edit" size={16} />
                      </button>
                      <button className="icon-btn" title={t("files.move")} onClick={() => setDialog({ kind: "move", type: "folder", id: f.id, name: f.name })}>
                        <Icon name="move" size={16} />
                      </button>
                      <button className="icon-btn danger" title={t("files.toTrash")} onClick={() => remove("folder", f.id, f.name)}>
                        <Icon name="trash" size={16} />
                      </button>
                    </>
                    )}
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
                    {tagDots(f.tags)}
                    {starButton("file", f)}
                  </span>
                </td>
                <td className="col-size muted">{formatBytes(f.size)}</td>
                <td className="col-date muted">
                  {f.recent_at ? (
                    <>
                      {formatDate(f.recent_at)}
                      <div className="recent-reason">{t(`org.reason.${f.recent_reason || "opened"}` as const)}</div>
                    </>
                  ) : (
                    formatDate(f.updated_at)
                  )}
                </td>
                <td className="col-actions">{fileActions(f)}</td>
              </tr>
            ))}
            {data && listFolders.length === 0 && listFiles.length === 0 && (
              <tr>
                <td colSpan={4}>
                  <div className="empty">
                    <Icon name={searchResults ? "search" : "upload"} size={32} />
                    <div>
                      {searchResults
                        ? t("files.nothingFound")
                        : view === "starred"
                          ? t("org.starredEmpty")
                          : view === "recent"
                            ? t("org.recentEmpty")
                            : view === "tag"
                              ? t("org.tagEmpty")
                              : data.writable
                                ? t("files.dropHint")
                                : t("files.emptyFolder")}
                    </div>
                  </div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      )}
      </PullToRefresh>

      {dialog?.kind === "newFolder" && (
        <NameDialog
          title={t("files.newFolder")}
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
          title={t("files.rename")}
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
      {tagsDialog && (
        <TagsDialog
          target={tagsDialog === "manage" ? null : tagsDialog}
          tags={tags}
          onClose={() => setTagsDialog(null)}
          onChanged={() => {
            loadTags();
            load();
          }}
        />
      )}
      {viewerIndex !== null && viewable[viewerIndex] && (
        <FileViewer
          files={viewable}
          index={viewerIndex}
          officeEnabled={officeEnabled}
          onIndex={setViewerIndex}
          onClose={() => setViewerIndex(null)}
        />
      )}
      {actionSheet && (
        <Modal title={actionSheet.file?.name || actionSheet.folder?.name || ""} onClose={() => setActionSheet(null)}>
          <div className="action-sheet">
            {actionSheet.file && (() => {
              const f = actionSheet.file;
              const run = (fn: () => void) => () => {
                setActionSheet(null);
                fn();
              };
              return (
                <>
                  {f.downloadable && (
                    <a className="menu-item" href={downloadUrl(f)} onClick={() => setActionSheet(null)}>
                      <Icon name="download" /> {t("files.download")}
                    </a>
                  )}
                  {writable && (
                    <>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "share", type: "file", id: f.id, name: f.name, downloadable: f.downloadable }))}>
                        <Icon name="share" /> {t("files.share")}
                      </button>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "rename", type: "file", id: f.id, name: f.name }))}>
                        <Icon name="edit" /> {t("files.rename")}
                      </button>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "move", type: "file", id: f.id, name: f.name }))}>
                        <Icon name="move" /> {t("files.move")}
                      </button>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "versions", file: f }))}>
                        <Icon name="clock" /> {t("files2.versions")}
                      </button>
                      <button
                        className="menu-item"
                        onClick={run(() => {
                          setVersionTarget(f);
                          versionInputRef.current?.click();
                        })}
                      >
                        <Icon name="upload" /> {t("files2.uploadVersion")}
                      </button>
                      <button className="menu-item danger" onClick={run(() => remove("file", f.id, f.name))}>
                        <Icon name="trash" /> {t("files.toTrash")}
                      </button>
                    </>
                  )}
                  <button className="menu-item" onClick={run(() => toggleStar("file", f))}>
                    <Icon name="star" /> {f.starred ? t("org.unstar") : t("org.star")}
                  </button>
                  <button className="menu-item" onClick={run(() => editTags("file", f))}>
                    <Icon name="tag" /> {t("org.editTags")}
                  </button>
                  {collection && (
                    <button className="menu-item" onClick={run(() => openFolder(f.folder))}>
                      <Icon name="folder" /> {t("org.openFolder")}
                    </button>
                  )}
                  <button className="menu-item" onClick={run(() => setDialog({ kind: "info", file: f }))}>
                    <Icon name="file" /> {t("mobile.details")} · {formatBytes(f.size)}
                  </button>
                </>
              );
            })()}
            {actionSheet.folder && (() => {
              const f = actionSheet.folder;
              const run = (fn: () => void) => () => {
                setActionSheet(null);
                fn();
              };
              return (
                <>
                  <button className="menu-item" onClick={run(() => openFolder(f.id))}>
                    <Icon name="folder" /> {t("mobile.open")}
                  </button>
                  <button className="menu-item" onClick={run(() => toggleStar("folder", f))}>
                    <Icon name="star" /> {f.starred ? t("org.unstar") : t("org.star")}
                  </button>
                  <button className="menu-item" onClick={run(() => editTags("folder", f))}>
                    <Icon name="tag" /> {t("org.editTags")}
                  </button>
                  <button className="menu-item" onClick={run(() => downloadZip(`/api/files/folders/${f.id}/zip/`))}>
                    <Icon name="archive" /> {t("files2.zipFolder")}
                  </button>
                  {writable && (
                    <>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "share", type: "folder", id: f.id, name: f.name }))}>
                        <Icon name="share" /> {t("files.share")}
                      </button>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "rename", type: "folder", id: f.id, name: f.name }))}>
                        <Icon name="edit" /> {t("files.rename")}
                      </button>
                      <button className="menu-item" onClick={run(() => setDialog({ kind: "move", type: "folder", id: f.id, name: f.name }))}>
                        <Icon name="move" /> {t("files.move")}
                      </button>
                      <button className="menu-item danger" onClick={run(() => remove("folder", f.id, f.name))}>
                        <Icon name="trash" /> {t("files.toTrash")}
                      </button>
                    </>
                  )}
                </>
              );
            })()}
          </div>
        </Modal>
      )}
      {shareId && (
        <ShareImportDialog
          shareId={shareId}
          initialFolder={folderId === "root" ? null : folderId}
          onClose={() => setParams(folderId === "root" ? {} : { folder: folderId })}
          onDone={(target) => {
            setParams(target ? { folder: target } : {});
            load();
            refreshUser();
          }}
        />
      )}
      {dialog?.kind === "info" && <InfoDialog file={dialog.file} onClose={() => setDialog(null)} />}
      {dialog?.kind === "versions" && (
        <VersionsDialog
          file={dialog.file}
          onClose={() => setDialog(null)}
          onChanged={() => {
            load();
            refreshUser();
          }}
          onUploadVersion={(picked) => uploadVersion(picked, dialog.file)}
        />
      )}
      {dragging && (
        <div className="drop-overlay">
          <Icon name="upload" size={40} />
          {t("files.dropToUpload")}
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
  const t = useT();
  const [name, setName] = useState(initial);
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const clean = name.trim();
    if (!clean) return setError(t("files.nameRequired"));
    if (/[/\\]/.test(clean)) return setError(t("files.nameSlash"));
    if (clean.length > 255) return setError(t("files.nameTooLong"));
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
            {t("common.cancel")}
          </button>
          <button className="btn btn-primary">{t("common.save")}</button>
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
  const t = useT();
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
    <Modal title={t("files.moveTitle", { name: item.name })} onClose={onClose}>
      <div className="breadcrumbs small">
        <button className="crumb" onClick={() => setCurrent("root")}>
          {t("files.myFiles")}
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
        {data && data.folders.length === 0 && <div className="muted small">{t("files.noSubfolders")}</div>}
      </div>
      {error && <div className="form-error">{error}</div>}
      <div className="modal-actions">
        <button className="btn" onClick={onClose}>
          {t("common.cancel")}
        </button>
        <button className="btn btn-primary" onClick={move}>
          {t("files.moveHere")}
        </button>
      </div>
    </Modal>
  );
}

function InfoDialog({ file, onClose }: { file: FileItem; onClose: () => void }) {
  const t = useT();
  return (
    <Modal title={file.name} onClose={onClose}>
      <dl className="info-list">
        <dt>{t("files.info.size")}</dt>
        <dd>{formatBytes(file.size)}</dd>
        <dt>{t("files.info.type")}</dt>
        <dd>{file.mime_type}</dd>
        <dt>{t("files.info.status")}</dt>
        <dd>
          {file.status_display}
          {file.scan_detail && <div className="small muted">{file.scan_detail}</div>}
        </dd>
        <dt>SHA-256</dt>
        <dd>
          <code className="break small">{file.sha256 || "—"}</code>
        </dd>
        <dt>{t("files.info.owner")}</dt>
        <dd>{file.owner}</dd>
        <dt>{t("files.info.created")}</dt>
        <dd>{formatDate(file.created_at)}</dd>
      </dl>
      <div className="modal-actions">
        {file.downloadable ? (
          <a className="btn btn-primary" href={`/api/files/items/${file.id}/download/`}>
            <Icon name="download" size={16} /> {t("files.download")}
          </a>
        ) : (
          <span className="muted small">{t("files.notDownloadable")}</span>
        )}
      </div>
      <p className="small muted">
        {t("files.encryptedNote")} <Link to="/trash">{t("nav.trash")}</Link>
      </p>
    </Modal>
  );
}
