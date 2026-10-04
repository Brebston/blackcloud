export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 Б";
  const units = ["Б", "КБ", "МБ", "ГБ", "ТБ"];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  const value = bytes / 1024 ** i;
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`;
}

export function formatDate(iso: string | null | undefined, withTime = true): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  if (sameDay && withTime) return d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleString("uk-UA", {
    day: "2-digit",
    month: "short",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}

export function fileIcon(mime: string, name: string): string {
  const ext = name.split(".").pop()?.toLowerCase() || "";
  if (mime.startsWith("image/")) return "image";
  if (mime.startsWith("video/")) return "video";
  if (mime.startsWith("audio/")) return "music";
  if (mime === "application/pdf" || ext === "pdf") return "pdf";
  if (["zip", "rar", "7z", "gz", "tar", "bz2", "xz"].includes(ext)) return "archive";
  if (["doc", "docx", "odt", "rtf", "txt", "md"].includes(ext)) return "doc";
  if (["xls", "xlsx", "ods", "csv"].includes(ext)) return "sheet";
  if (["js", "ts", "py", "go", "rs", "java", "c", "cpp", "html", "css", "json", "sh"].includes(ext)) return "code";
  return "file";
}

export function senderName(from: string): string {
  const m = from.match(/^\s*"?([^"<]+?)"?\s*<[^>]+>\s*$/);
  return m ? m[1] : from;
}
