import { useEffect, useState } from "react";
import { errorText, get, post } from "../api/client";
import type { FileItem, Folder } from "../api/types";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";

export default function TrashPage() {
  const toast = useToast();
  const t = useT();
  const { refresh } = useAuth();
  const [data, setData] = useState<{ retention_days: number; folders: Folder[]; files: FileItem[] } | null>(null);

  const load = () => get<typeof data>("/api/files/trash/").then(setData).catch((e) => toast(errorText(e), "error"));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const act = async (op: "restore" | "purge", type: "file" | "folder", id: string, name: string) => {
    if (op === "purge" && !window.confirm(t("files.trash.confirmPurge", { name }))) return;
    try {
      await post(`/api/files/trash/${op}/`, { type, id });
      toast(op === "restore" ? t("files.trash.restored") : t("files.trash.purged"), "success");
      load();
      refresh();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const empty = async () => {
    if (!window.confirm(t("files.trash.confirmEmpty"))) return;
    await post("/api/files/trash/empty/");
    toast(t("files.trash.emptied"), "success");
    load();
    refresh();
  };

  const isEmpty = data && data.files.length === 0 && data.folders.length === 0;

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h2>{t("nav.trash")}</h2>
          {data && <p className="muted small">{t("files.trash.retention", { n: data.retention_days })}</p>}
        </div>
        {!isEmpty && (
          <button className="btn btn-danger" onClick={empty}>
            <Icon name="trash" size={16} /> {t("files.trash.empty")}
          </button>
        )}
      </div>
      <div className="card table-card">
        <table className="table">
          <tbody>
            {isEmpty && (
              <tr>
                <td>
                  <div className="empty">
                    <Icon name="trash" size={32} />
                    {t("files.trash.isEmpty")}
                  </div>
                </td>
              </tr>
            )}
            {data?.folders.map((f) => (
              <tr key={f.id}>
                <td>
                  <span className="name-cell">
                    <Icon name="folder" className="ico-folder" /> {f.name}
                  </span>
                </td>
                <td className="col-size muted">—</td>
                <td className="col-date muted">{formatDate(f.deleted_at)}</td>
                <td className="col-actions">
                  <div className="row-actions">
                    <button className="icon-btn" title={t("files.trash.restore")} onClick={() => act("restore", "folder", f.id, f.name)}>
                      <Icon name="restore" size={16} />
                    </button>
                    <button className="icon-btn danger" title={t("files.trash.purge")} onClick={() => act("purge", "folder", f.id, f.name)}>
                      <Icon name="x" size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
            {data?.files.map((f) => (
              <tr key={f.id}>
                <td>
                  <span className="name-cell">
                    <Icon name="file" /> {f.name}
                  </span>
                </td>
                <td className="col-size muted">{formatBytes(f.size)}</td>
                <td className="col-date muted">{formatDate(f.deleted_at)}</td>
                <td className="col-actions">
                  <div className="row-actions">
                    <button className="icon-btn" title={t("files.trash.restore")} onClick={() => act("restore", "file", f.id, f.name)}>
                      <Icon name="restore" size={16} />
                    </button>
                    <button className="icon-btn danger" title={t("files.trash.purge")} onClick={() => act("purge", "file", f.id, f.name)}>
                      <Icon name="x" size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
