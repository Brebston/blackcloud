import { useEffect, useState } from "react";
import { get } from "../api/client";
import type { FileItem } from "../api/types";
import { tr } from "../i18n";

export type ViewKind = "image" | "pdf" | "video" | "audio" | "text" | "office";

// Те, що браузер показує нативно (HEIC/TIFF/BMP — через велике WebP-зображення з сервера)
const BROWSER_IMAGES = ["image/png", "image/jpeg", "image/gif", "image/webp", "image/avif"];
// .MOV з iPhone — той самий контейнер, що й MP4; відтворення залежить від кодека й браузера
export const BROWSER_VIDEOS = ["video/mp4", "video/webm", "video/quicktime"];
const OFFICE_EXT = ["docx", "doc", "docm", "dotx", "odt", "rtf", "xlsx", "xls", "xlsm", "ods", "csv", "pptx", "ppt", "ppsx", "odp"];

export function extension(name: string): string {
  const i = name.lastIndexOf(".");
  return i >= 0 ? name.slice(i + 1).toLowerCase() : "";
}

export function viewKind(f: FileItem, officeEnabled: boolean): ViewKind | null {
  if (!f.downloadable) return null;
  if (BROWSER_IMAGES.includes(f.mime_type) || (f.mime_type.startsWith("image/") && (f.has_preview || f.has_thumbnail))) return "image";
  if (f.mime_type === "application/pdf") return "pdf";
  if (BROWSER_VIDEOS.includes(f.mime_type)) return "video";
  if (["audio/mpeg", "audio/ogg", "audio/wav"].includes(f.mime_type)) return "audio";
  if (officeEnabled && OFFICE_EXT.includes(extension(f.name))) return "office";
  if (f.mime_type === "text/plain") return "text";
  return null;
}

/** Чи потрібен окремий origin (usercontent) для показу: усе, крім мініатюр. */
export function needsPreviewUrl(f: FileItem, kind: ViewKind | null): boolean {
  if (kind === "image") return BROWSER_IMAGES.includes(f.mime_type);
  return kind === "pdf" || kind === "video" || kind === "audio" || kind === "text";
}

/**
 * Адреса для перегляду в браузері на піддомені usercontent.<домен>.
 * Вміст користувачів (PDF, текст, відео) ніколи не відкривається в origin основного сайту:
 * навіть шкідливий файл не зможе прочитати cookie чи викликати API.
 */
export function usePreviewUrl(f: FileItem | undefined, kind: ViewKind | null) {
  const [state, setState] = useState<{ id: string; url: string; error: string } | null>(null);
  const needed = !!f && needsPreviewUrl(f, kind);
  const id = f?.id;
  const version = f?.content_version;
  useEffect(() => {
    if (!needed || !id) return;
    let cancelled = false;
    setState(null);
    get<{ url: string }>(`/api/files/items/${id}/preview/`)
      .then((r) => !cancelled && setState({ id, url: r.url, error: "" }))
      .catch(() => !cancelled && setState({ id, url: "", error: tr("files.viewer.previewFailed") }));
    return () => {
      cancelled = true;
    };
  }, [needed, id, version]);
  if (!needed || !f) return { url: "", loading: false, error: "" };
  if (!state || state.id !== f.id) return { url: "", loading: true, error: "" };
  return { url: state.url, loading: false, error: state.error };
}

export function imageSrc(f: FileItem, previewUrl: string): string {
  if (BROWSER_IMAGES.includes(f.mime_type)) return previewUrl;
  // Мініатюра (480 px) — лише запасний варіант, поки велике зображення ще не створено
  return f.has_preview ? previewImageUrl(f) : thumbnailUrl(f);
}

export const downloadUrl = (f: FileItem) => `/api/files/items/${f.id}/download/`;
export const thumbnailUrl = (f: FileItem) => `/api/files/items/${f.id}/thumbnail/?v=${f.content_version}`;
export const previewImageUrl = (f: FileItem) => `/api/files/items/${f.id}/preview-image/?v=${f.content_version}`;
