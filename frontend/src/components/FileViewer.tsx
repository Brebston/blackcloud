import { useEffect } from "react";
import { useNavigate } from "react-router-dom";
import type { FileItem } from "../api/types";
import { formatBytes } from "../lib/format";
import { downloadUrl, imageSrc, needsPreviewUrl, usePreviewUrl, viewKind } from "../lib/viewer";
import Icon from "./Icon";
import ImageZoom from "./ImageZoom";

const needsUrl = (f: FileItem) => needsPreviewUrl(f, "image");

/** Повноекранний переглядач з гортанням між файлами папки (← →). */
export default function FileViewer({
  files,
  index,
  officeEnabled,
  onIndex,
  onClose,
}: {
  files: FileItem[];
  index: number;
  officeEnabled: boolean;
  onIndex: (i: number) => void;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const file = files[index];
  const kind = file ? viewKind(file, officeEnabled) : null;
  const preview = usePreviewUrl(file, kind);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight" && index < files.length - 1) onIndex(index + 1);
      if (e.key === "ArrowLeft" && index > 0) onIndex(index - 1);
    };
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [index, files.length, onClose, onIndex]);

  if (!file) return null;

  return (
    <div className="viewer" role="dialog" aria-modal="true" aria-label={file.name}>
      <header className="viewer-bar">
        <button className="icon-btn" onClick={onClose} aria-label="Закрити" title="Закрити (Esc)">
          <Icon name="x" />
        </button>
        <div className="viewer-title">
          <span className="truncate">{file.name}</span>
          <span className="muted small">
            {formatBytes(file.size)} · {index + 1} / {files.length}
          </span>
        </div>
        <div className="viewer-actions">
          {kind === "office" && (
            <button className="btn btn-sm btn-primary" onClick={() => navigate(`/edit/${file.id}`)}>
              <Icon name="edit" size={14} /> Відкрити в редакторі
            </button>
          )}
          {kind === "pdf" && preview.url && (
            <a className="btn btn-sm" href={preview.url} target="_blank" rel="noopener noreferrer">
              <Icon name="external" size={14} /> Нова вкладка
            </a>
          )}
          <a className="btn btn-sm" href={downloadUrl(file)}>
            <Icon name="download" size={14} /> Завантажити
          </a>
        </div>
      </header>

      <div className="viewer-body">
        {index > 0 && (
          <button className="viewer-nav prev" onClick={() => onIndex(index - 1)} aria-label="Попередній">
            <Icon name="chevronLeft" size={28} />
          </button>
        )}
        {preview.loading && <div className="viewer-empty muted">Відкриття…</div>}
        {preview.error && (
          <div className="viewer-empty">
            <Icon name="alert" size={40} />
            <div>{preview.error}</div>
          </div>
        )}
        {!preview.loading && !preview.error && (
          <>
            {kind === "image" && (preview.url || !needsUrl(file)) && (
              <ImageZoom key={file.id} src={imageSrc(file, preview.url)} alt={file.name} />
            )}
            {kind === "pdf" && preview.url && <iframe key={file.id} className="viewer-pdf" src={preview.url} title={file.name} />}
            {kind === "video" && preview.url && (
              <video key={file.id} className="viewer-media" src={preview.url} controls autoPlay />
            )}
            {kind === "audio" && preview.url && <audio key={file.id} src={preview.url} controls autoPlay />}
            {kind === "text" && preview.url && (
              <iframe key={file.id} className="viewer-text" src={preview.url} title={file.name} sandbox="" />
            )}
          </>
        )}
        {(kind === "office" || kind === null) && (
          <div className="viewer-empty">
            <Icon name="file" size={48} />
            <div>{kind === "office" ? "Документ відкривається в онлайн-редакторі" : "Попередній перегляд недоступний"}</div>
          </div>
        )}
        {index < files.length - 1 && (
          <button className="viewer-nav next" onClick={() => onIndex(index + 1)} aria-label="Наступний">
            <Icon name="chevronRight" size={28} />
          </button>
        )}
      </div>
    </div>
  );
}
