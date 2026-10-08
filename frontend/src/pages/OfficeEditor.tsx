import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { errorText, get } from "../api/client";
import type { FileItem } from "../api/types";
import Icon from "../components/Icon";

declare global {
  interface Window {
    DocsAPI?: { DocEditor: new (id: string, config: unknown) => { destroyEditor: () => void } };
  }
}

const SCRIPT_ID = "onlyoffice-api";

function loadScript(src: string): Promise<void> {
  if (window.DocsAPI) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null;
    const s = existing || document.createElement("script");
    s.id = SCRIPT_ID;
    s.src = src; // https://office.<домен>/... — дозволено в CSP script-src лише цей піддомен
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("Сервер документів недоступний"));
    if (!existing) document.body.appendChild(s);
  });
}

/** Повноекранний онлайн-редактор Word/Excel/PowerPoint (ONLYOFFICE). */
export default function OfficeEditorPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [file, setFile] = useState<FileItem | null>(null);
  const [error, setError] = useState("");
  const editor = useRef<{ destroyEditor: () => void } | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [info, cfg] = await Promise.all([
          get<FileItem>(`/api/files/items/${id}/`),
          get<{ config: Record<string, unknown>; api_url: string }>(`/api/office/config/${id}/`),
        ]);
        if (cancelled) return;
        setFile(info);
        await loadScript(cfg.api_url);
        if (cancelled || !window.DocsAPI) return;
        editor.current = new window.DocsAPI.DocEditor("bc-office-editor", {
          ...cfg.config,
          width: "100%",
          height: "100%",
          type: window.matchMedia("(max-width: 820px)").matches ? "mobile" : "desktop",
        });
      } catch (e) {
        if (!cancelled) setError(errorText(e));
      }
    })();
    return () => {
      cancelled = true;
      editor.current?.destroyEditor();
      editor.current = null;
    };
  }, [id]);

  const back = () => (file?.folder ? navigate(`/files?folder=${file.folder}`) : navigate("/files"));

  return (
    <div className="office-page">
      <header className="viewer-bar office-bar">
        <button className="icon-btn" onClick={back} aria-label="Назад до файлів" title="Назад до файлів">
          <Icon name="chevronLeft" />
        </button>
        <div className="viewer-title">
          <span className="truncate">{file?.name || "Документ"}</span>
          <span className="muted small">Зміни зберігаються автоматично як нова версія файлу</span>
        </div>
      </header>
      {error ? (
        <div className="viewer-empty">
          <Icon name="alert" size={40} />
          <div>{error}</div>
          <button className="btn" onClick={back}>
            Повернутися до файлів
          </button>
        </div>
      ) : (
        <div className="office-frame">
          <div id="bc-office-editor" />
        </div>
      )}
    </div>
  );
}
