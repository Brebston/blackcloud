// Діалог «Зберегти в BlackCloud» для даних, надісланих через меню «Поділитися» Android (Web Share Target).
// Файли завантажуються звичайним чанковим завантажувачем (lib/upload.ts); після цього запис видаляється.
import { useEffect, useRef, useState } from "react";
import { errorText, get } from "../api/client";
import type { BrowseResult } from "../api/types";
import { useT } from "../i18n";
import { formatBytes } from "../lib/format";
import { deleteShare, readShare, SharedEntry } from "../lib/shareInbox";
import { uploadFile } from "../lib/upload";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";

function textFileName(title: string): string {
  const base = title.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ").trim().slice(0, 80) || "shared";
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${base} ${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}-${pad(d.getMinutes())}.txt`;
}

export default function ShareImportDialog({
  shareId,
  initialFolder,
  onClose,
  onDone,
}: {
  shareId: string;
  /** Поточна тека сторінки «Файли» (null — корінь). */
  initialFolder: string | null;
  onClose: () => void;
  onDone: (folder: string | null) => void;
}) {
  const t = useT();
  const toast = useToast();
  const [entry, setEntry] = useState<SharedEntry | null | undefined>(undefined);
  const [current, setCurrent] = useState<string>(initialFolder || "root");
  const [browse, setBrowse] = useState<BrowseResult | null>(null);
  const [saveText, setSaveText] = useState(true);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState({ index: 0, loaded: 0, total: 0 });
  const [error, setError] = useState("");
  const abort = useRef<AbortController | null>(null);

  useEffect(() => {
    readShare(shareId)
      .then((e) => {
        setEntry(e);
        if (e) setSaveText(e.files.length === 0);
      })
      .catch(() => setEntry(null));
    return () => abort.current?.abort();
  }, [shareId]);

  useEffect(() => {
    get<BrowseResult>(`/api/files/browse/?folder=${encodeURIComponent(current)}`)
      .then((r) => {
        // Чужа тека лише для читання — повертаємося до кореня
        if (!r.writable && current !== "root") setCurrent("root");
        else setBrowse(r);
      })
      .catch((e) => setError(errorText(e)));
  }, [current]);

  const sharedText = entry ? [entry.title, entry.text, entry.url].filter((x) => x && x.trim()).join("\n\n") : "";
  const files = entry ? entry.files.map((f) => new File([f.blob], f.name || "shared", { type: f.type || f.blob.type })) : [];
  const toUpload = [...files, ...(saveText && sharedText ? [new File([sharedText + "\n"], textFileName(entry?.title || ""), { type: "text/plain" })] : [])];

  const cancel = () => {
    abort.current?.abort();
    deleteShare(shareId).finally(onClose);
  };

  const save = async () => {
    if (!browse) return;
    setError("");
    setBusy(true);
    abort.current = new AbortController();
    const target = browse.folder?.id || null;
    let failed = 0;
    for (let i = 0; i < toUpload.length; i++) {
      const file = toUpload[i];
      setProgress({ index: i, loaded: 0, total: file.size });
      try {
        await uploadFile(file, target, (p) => setProgress({ index: i, loaded: p.loaded, total: p.total }), abort.current.signal);
      } catch (e) {
        failed++;
        toast(`${file.name}: ${errorText(e)}`, "error");
      }
      if (abort.current.signal.aborted) break;
    }
    setBusy(false);
    if (abort.current.signal.aborted) return;
    await deleteShare(shareId);
    if (failed < toUpload.length) toast(t("mobile.shareSaved", { n: toUpload.length - failed }), "success");
    onDone(target);
  };

  if (entry === undefined) return null;
  if (entry === null) {
    return (
      <Modal title={t("mobile.shareDialogTitle")} onClose={onClose}>
        <p>{t("mobile.shareMissing")}</p>
        <div className="modal-actions">
          <button className="btn btn-primary" onClick={onClose}>
            {t("common.ok")}
          </button>
        </div>
      </Modal>
    );
  }

  const totalSize = files.reduce((s, f) => s + f.size, 0);
  return (
    <Modal title={t("mobile.shareDialogTitle")} onClose={busy ? () => undefined : cancel}>
      {files.length > 0 && (
        <div className="stack share-files">
          <strong>{t("mobile.shareFiles", { n: files.length, size: formatBytes(totalSize) })}</strong>
          <ul className="plain-list small">
            {files.slice(0, 6).map((f, i) => (
              <li key={i} className="row gap-sm">
                <Icon name="file" size={14} /> <span className="truncate">{f.name}</span>
                <span className="muted nowrap">{formatBytes(f.size)}</span>
              </li>
            ))}
            {files.length > 6 && <li className="muted">{t("mobile.shareMoreFiles", { n: files.length - 6 })}</li>}
          </ul>
        </div>
      )}
      {sharedText && (
        <div className="stack">
          <pre className="share-text small">{sharedText}</pre>
          <label className="check">
            <input type="checkbox" checked={saveText} onChange={(e) => setSaveText(e.target.checked)} disabled={busy} />
            {t("mobile.shareSaveText")}
          </label>
        </div>
      )}

      <div className="stack">
        <span className="small muted">{t("mobile.shareTarget")}</span>
        <div className="breadcrumbs small">
          <button className="crumb" onClick={() => setCurrent("root")} disabled={busy}>
            {t("files.myFiles")}
          </button>
          {browse?.breadcrumbs.map((b) => (
            <span key={b.id}>
              <Icon name="chevronRight" size={12} />
              <button className="crumb" onClick={() => setCurrent(b.id)} disabled={busy}>
                {b.name}
              </button>
            </span>
          ))}
        </div>
        <div className="folder-picker">
          {browse?.folders.map((f) => (
            <button key={f.id} onClick={() => setCurrent(f.id)} disabled={busy}>
              <Icon name="folder" size={16} /> {f.name}
            </button>
          ))}
          {browse && browse.folders.length === 0 && <div className="muted small">{t("files.noSubfolders")}</div>}
        </div>
      </div>

      {busy && (
        <div className="stack">
          <span className="small muted">
            {t("mobile.shareUploading", { i: progress.index + 1, n: toUpload.length })}
          </span>
          <div className="progress">
            <div className="progress-fill" style={{ width: `${progress.total ? (progress.loaded / progress.total) * 100 : 100}%` }} />
          </div>
        </div>
      )}
      {error && <div className="form-error">{error}</div>}
      <div className="modal-actions">
        <button className="btn" onClick={cancel}>
          {t("common.cancel")}
        </button>
        <button className="btn btn-primary" onClick={save} disabled={busy || !browse || toUpload.length === 0}>
          <Icon name="upload" size={16} /> {t("mobile.shareSave", { n: toUpload.length })}
        </button>
      </div>
    </Modal>
  );
}
