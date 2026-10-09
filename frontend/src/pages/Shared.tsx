import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { del, errorText, get } from "../api/client";
import type { Share } from "../api/types";
import Icon from "../components/Icon";
import { useToast } from "../components/Toast";
import { useT } from "../i18n";
import { fileIcon, formatBytes, formatDate } from "../lib/format";

export default function SharedPage() {
  const toast = useToast();
  const t = useT();
  const [incoming, setIncoming] = useState<Share[]>([]);
  const [outgoing, setOutgoing] = useState<Share[]>([]);
  const [error, setError] = useState("");

  const load = async () => {
    try {
      const [inc, out] = await Promise.all([get<Share[]>("/api/files/shared-with-me/"), get<Share[]>("/api/files/shares/")]);
      setIncoming(inc);
      setOutgoing(out);
    } catch (e) {
      setError(errorText(e));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const revoke = async (id: string) => {
    await del(`/api/files/shares/${id}/`);
    toast(t("files.shared.revoked"), "success");
    load();
  };

  return (
    <div className="page">
      <div className="page-head">
        <h2>{t("files.shared.title")}</h2>
      </div>
      {error && <div className="banner banner-danger">{error}</div>}

      <h3 className="section-title">{t("files.shared.withMe")}</h3>
      <div className="card table-card">
        <table className="table">
          <tbody>
            {incoming.length === 0 && (
              <tr>
                <td className="empty-small">{t("files.shared.nothingYet")}</td>
              </tr>
            )}
            {incoming.map((s) => (
              <tr key={s.id}>
                <td>
                  <span className="name-cell">
                    {s.target_type === "folder" ? (
                      <>
                        <Icon name="folder" className="ico-folder" />
                        <Link to={`/files?folder=${s.folder}`}>{s.target_name}</Link>
                      </>
                    ) : (
                      <>
                        <Icon name={fileIcon(s.file_info?.mime_type || "", s.target_name)} />
                        <span>{s.target_name}</span>
                      </>
                    )}
                  </span>
                </td>
                <td className="muted">{t("files.shared.from", { owner: s.owner })}</td>
                <td className="col-size muted">{s.file_info ? formatBytes(s.file_info.size) : ""}</td>
                <td className="col-date muted">{formatDate(s.created_at)}</td>
                <td className="col-actions">
                  <div className="row-actions">
                    {s.file_info?.downloadable && (
                      <a className="icon-btn" title={t("files.download")} href={`/api/files/items/${s.file}/download/`}>
                        <Icon name="download" size={16} />
                      </a>
                    )}
                    <button className="icon-btn danger" title={t("files.shared.removeFromList")} onClick={() => revoke(s.id)}>
                      <Icon name="x" size={16} />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="section-title">{t("files.shared.byMe")}</h3>
      <div className="card table-card">
        <table className="table">
          <tbody>
            {outgoing.length === 0 && (
              <tr>
                <td className="empty-small">{t("files.shared.noneByMe")}</td>
              </tr>
            )}
            {outgoing.map((s) => (
              <tr key={s.id}>
                <td>
                  <span className="name-cell">
                    <Icon name={s.target_type === "folder" ? "folder" : "file"} />
                    {s.target_name}
                  </span>
                </td>
                <td className="muted">{t("files.shared.to", { recipient: s.recipient })}</td>
                <td className="col-date muted">{formatDate(s.created_at)}</td>
                <td className="col-actions">
                  <button className="link-btn danger" onClick={() => revoke(s.id)}>
                    {t("files.shared.revoke")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
