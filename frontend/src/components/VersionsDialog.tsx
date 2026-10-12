import { useCallback, useEffect, useRef, useState } from "react";
import { api, errorText, get, post } from "../api/client";
import type { FileItem } from "../api/types";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";

export interface FileVersion {
  id: string;
  number: number;
  created_at: string;
  size: number;
  author: string;
  status: FileItem["status"];
  status_display: string;
  downloadable: boolean;
  is_current: boolean;
}

/**
 * Історія версій файлу (лише для власника): завантаження, відновлення, видалення
 * попередніх версій і завантаження нової версії. Відновлення створює нову поточну
 * версію-копію, яка знову проходить антивірус.
 */
export default function VersionsDialog({
  file,
  onClose,
  onChanged,
  onUploadVersion,
}: {
  file: FileItem;
  onClose: () => void;
  onChanged: () => void;
  onUploadVersion?: (picked: File) => Promise<void>;
}) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<FileVersion[] | null>(null);
  const [maxVersions, setMaxVersions] = useState(0);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const base = `/api/files/items/${file.id}/versions/`;

  const load = useCallback(async () => {
    try {
      const r = await get<{ max_versions: number; versions: FileVersion[] }>(base);
      setItems(r.versions);
      setMaxVersions(r.max_versions);
      setError("");
    } catch (e) {
      setError(errorText(e));
    }
  }, [base]);

  useEffect(() => {
    load();
  }, [load]);

  const run = async (action: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await action();
      toast(done, "success");
      onChanged();
      await load();
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setBusy(false);
    }
  };

  const restore = (v: FileVersion) => {
    if (!window.confirm(t("files2.restoreConfirm", { n: v.number, date: formatDate(v.created_at) }))) return;
    run(() => post(`${base}${v.id}/restore/`), t("files2.restored"));
  };

  const remove = (v: FileVersion) => {
    if (!window.confirm(t("files2.deleteConfirm", { n: v.number, date: formatDate(v.created_at) }))) return;
    run(() => api(`${base}${v.id}/`, { method: "DELETE" }), t("files2.deleted"));
  };

  const statusPill = (v: FileVersion) => {
    if (v.status === "clean") return null;
    const cls = v.status === "scanning" ? "pill" : v.status === "unscanned" ? "pill pill-warning" : "pill pill-danger";
    return <span className={cls}>{v.status_display}</span>;
  };

  return (
    <Modal title={t("files2.versionsTitle", { name: file.name })} onClose={onClose} wide>
      {error && <div className="banner banner-danger">{error}</div>}
      {items === null && !error && <div className="muted">{t("common.loading")}</div>}
      {items && (
        <div className="table-card">
          <table className="table">
            <thead>
              <tr>
                <th>{t("files2.colVersion")}</th>
                <th>{t("files2.colDate")}</th>
                <th className="col-size">{t("files2.colSize")}</th>
                <th>{t("files2.colAuthor")}</th>
                <th className="col-actions" aria-label={t("files.colActions")} />
              </tr>
            </thead>
            <tbody>
              {items.map((v) => (
                <tr key={v.id}>
                  <td>
                    <span className="name-cell">
                      <span>v{v.number}</span>
                      {v.is_current && <span className="pill pill-success">{t("files2.current")}</span>}
                      {statusPill(v)}
                    </span>
                  </td>
                  <td className="muted">{formatDate(v.created_at)}</td>
                  <td className="col-size muted">{formatBytes(v.size)}</td>
                  <td className="muted">{v.author || "—"}</td>
                  <td className="col-actions">
                    <div className="row-actions">
                      {v.downloadable ? (
                        <a className="icon-btn" title={t("files2.download")} aria-label={t("files2.download")} href={`${base}${v.id}/download/`}>
                          <Icon name="download" size={16} />
                        </a>
                      ) : (
                        <span className="small muted" title={t("files2.notDownloadable", { status: v.status_display })}>
                          <Icon name="lock" size={14} />
                        </span>
                      )}
                      {!v.is_current && (
                        <>
                          <button
                            className="icon-btn"
                            title={t("files2.restore")}
                            aria-label={t("files2.restore")}
                            disabled={busy || !v.downloadable}
                            onClick={() => restore(v)}
                          >
                            <Icon name="restore" size={16} />
                          </button>
                          <button
                            className="icon-btn danger"
                            title={t("files2.delete")}
                            aria-label={t("files2.delete")}
                            disabled={busy}
                            onClick={() => remove(v)}
                          >
                            <Icon name="trash" size={16} />
                          </button>
                        </>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {items && items.length <= 1 && <p className="small muted">{t("files2.noVersions")}</p>}
      {maxVersions > 0 && <p className="small muted">{t("files2.retentionHint", { n: maxVersions })}</p>}
      <div className="modal-actions">
        {onUploadVersion && (
          <>
            <button className="btn" disabled={busy} onClick={() => inputRef.current?.click()}>
              <Icon name="upload" size={16} /> {t("files2.uploadVersion")}
            </button>
            <input
              ref={inputRef}
              type="file"
              hidden
              onChange={async (e) => {
                const picked = e.target.files?.[0];
                e.target.value = "";
                if (!picked) return;
                setBusy(true);
                try {
                  await onUploadVersion(picked);
                  await load();
                } finally {
                  setBusy(false);
                }
              }}
            />
          </>
        )}
        <button className="btn btn-primary" onClick={onClose}>
          {t("common.close")}
        </button>
      </div>
    </Modal>
  );
}
