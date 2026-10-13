// Фото профілю: вибір файлу з попереднім переглядом, завантаження, видалення.
// Сервер приймає PNG/JPEG/WebP/HEIC до 5 МБ; квадрат 256×256 без EXIF готує ізольований
// сервіс thumbnailer у фоні, тож після завантаження чекаємо, доки фото буде готове.
import { useEffect, useRef, useState } from "react";
import { api, del, errorText, get } from "../api/client";
import { useAuth } from "../hooks/useAuth";
import { useEvents } from "../hooks/useEvents";
import { useT } from "../i18n";
import Avatar from "./Avatar";
import Icon from "./Icon";
import { useToast } from "./Toast";

const MAX_BYTES = 5 * 1024 * 1024;
const ACCEPT = "image/png,image/jpeg,image/webp,image/heic,image/heif,.heic,.heif";

interface AvatarStatus {
  status: "none" | "processing" | "ready" | "failed";
  avatar_url: string | null;
}

export default function AvatarSettings() {
  const { user, refresh } = useAuth();
  const t = useT();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [previewFailed, setPreviewFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!file) return setPreview(null);
    const url = URL.createObjectURL(file);
    setPreview(url);
    setPreviewFailed(false);
    return () => URL.revokeObjectURL(url);
  }, [file]);

  // Стан обробки: подія WebSocket (avatar.updated) або опитування як запасний варіант
  useEffect(() => {
    if (!processing) return;
    let stopped = false;
    let tries = 0;
    const tick = async () => {
      if (stopped) return;
      tries += 1;
      try {
        const st = await get<AvatarStatus>("/api/account/avatar/");
        if (st.status !== "processing") return finish(st.status);
      } catch {
        /* повторимо */
      }
      if (tries < 30) timer = window.setTimeout(tick, 1500);
      else {
        setProcessing(false);
        setError(t("admin2.avatar.slow"));
      }
    };
    let timer = window.setTimeout(tick, 1200);
    return () => {
      stopped = true;
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [processing]);

  useEvents((event, payload) => {
    if (event === "avatar.updated" && processing && payload?.status && payload.status !== "processing") finish(payload.status);
  });

  const finish = async (status: AvatarStatus["status"]) => {
    setProcessing(false);
    await refresh();
    if (status === "ready") {
      setFile(null);
      toast(t("admin2.avatar.saved"), "success");
    } else if (status === "failed") {
      setError(t("admin2.avatar.failed"));
    }
  };

  const pick = (f: File | undefined) => {
    setError("");
    if (!f) return;
    if (f.size > MAX_BYTES) {
      setError(t("admin2.avatar.tooBig"));
      return;
    }
    setFile(f);
  };

  const upload = async () => {
    if (!file) return;
    setBusy(true);
    setError("");
    try {
      const fd = new FormData();
      fd.append("file", file);
      const st = await api<AvatarStatus>("/api/account/avatar/", { method: "POST", body: fd });
      if (st.status === "processing") setProcessing(true);
      else await finish(st.status);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!window.confirm(t("admin2.avatar.removeConfirm"))) return;
    try {
      await del("/api/account/avatar/");
      await refresh();
      toast(t("admin2.avatar.removed"), "info");
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  if (!user) return null;
  const name = user.display_name || user.username;

  return (
    <div className="card stack avatar-settings">
      <h3>{t("admin2.avatar.title")}</h3>
      <div className="avatar-settings-row">
        <div className="avatar-preview-wrap">
          {preview && !previewFailed ? (
            <span className="avatar bc-avatar has-photo avatar-preview">
              <img src={preview} alt={t("admin2.avatar.previewAlt")} onError={() => setPreviewFailed(true)} />
            </span>
          ) : (
            <Avatar name={name} email={user.email} url={file ? null : user.avatar_url} size={96} className="avatar-preview" />
          )}
          {processing && (
            <span className="avatar-processing" role="status">
              <span className="bc-spinner" aria-hidden="true" />
              <span className="sr-only">{t("admin2.avatar.processing")}</span>
            </span>
          )}
        </div>
        <div className="stack grow avatar-settings-body">
          {file ? (
            <>
              <div className="small">
                <strong className="break">{file.name}</strong>
                {previewFailed && <div className="muted">{t("admin2.avatar.noPreview")}</div>}
              </div>
              <p className="small muted">{t("admin2.avatar.cropHint")}</p>
              <div className="row gap-sm wrap">
                <button type="button" className="btn btn-primary" onClick={upload} disabled={busy || processing}>
                  <Icon name="upload" size={16} /> {processing ? t("admin2.avatar.processing") : t("admin2.avatar.save")}
                </button>
                <button type="button" className="btn" onClick={() => setFile(null)} disabled={busy || processing}>
                  {t("common.cancel")}
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="small muted">{t("admin2.avatar.hint")}</p>
              <div className="row gap-sm wrap">
                <button type="button" className="btn" onClick={() => input.current?.click()} disabled={processing}>
                  <Icon name="camera" size={16} /> {user.avatar_url ? t("admin2.avatar.change") : t("admin2.avatar.choose")}
                </button>
                {user.avatar_url && (
                  <button type="button" className="btn btn-danger-outline" onClick={remove} disabled={processing}>
                    <Icon name="trash" size={16} /> {t("admin2.avatar.remove")}
                  </button>
                )}
              </div>
            </>
          )}
          <input
            ref={input}
            type="file"
            accept={ACCEPT}
            hidden
            onChange={(e) => {
              pick(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
          {error && <div className="form-error">{error}</div>}
        </div>
      </div>
    </div>
  );
}
