import { FormEvent, useCallback, useEffect, useState } from "react";
import { del, errorText, get, patch } from "../api/client";
import { useT } from "../i18n";
import { formatDate } from "../lib/format";
import { biometricKey, isAlreadyRegistered, isCancelled, passkeysSupported, platformAuthenticatorAvailable, registerPasskey } from "../lib/webauthn";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";

interface PasskeyItem {
  id: string;
  name: string;
  provider: string;
  synced: boolean;
  created_at: string;
  last_used_at: string | null;
}

type Dialog =
  | { kind: "add"; platform: boolean }
  | { kind: "rename"; item: PasskeyItem }
  | { kind: "remove"; item: PasskeyItem }
  | null;

/** Налаштування → Безпека → «Ключі доступу» (Face ID / Touch ID / Windows Hello / апаратні ключі). */
export default function PasskeysCard({ hasTotp, onChange }: { hasTotp: boolean; onChange: () => void }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<PasskeyItem[] | null>(null);
  const [platform, setPlatform] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const supported = passkeysSupported();
  const method = t(biometricKey());

  const load = useCallback(() => {
    get<PasskeyItem[]>("/api/account/security/passkeys/").then(setItems).catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
    platformAuthenticatorAvailable().then(setPlatform);
  }, [load]);

  const openDialog = (d: Dialog) => {
    setDialog(d);
    setPassword("");
    setError("");
    setName(d && d.kind === "rename" ? d.item.name : "");
  };

  const changed = () => {
    load();
    onChange();
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!dialog) return;
    setError("");
    setBusy(true);
    try {
      if (dialog.kind === "add") {
        await registerPasskey({ password, platform: dialog.platform });
        toast(t("passkey.added"), "success");
      } else if (dialog.kind === "rename") {
        await patch(`/api/account/security/passkeys/${dialog.item.id}/`, { name });
      } else {
        await del(`/api/account/security/passkeys/${dialog.item.id}/`, { password });
        toast(t("passkey.removed"), "info");
      }
      setDialog(null);
      changed();
    } catch (err) {
      if (isAlreadyRegistered(err)) setError(t("passkey.prompt.already"));
      else if (!isCancelled(err)) setError(err instanceof DOMException ? t("passkey.failed") : errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card stack">
      <h3>
        <Icon name="lock" /> {t("passkey.title")}
      </h3>
      <p className="small muted">{t("passkey.intro")}</p>

      {items && items.length === 0 && <p className="small muted">{t("passkey.empty")}</p>}
      {items && items.length > 0 && (
        <ul className="passkey-list">
          {items.map((it) => (
            <li key={it.id} className="passkey-item">
              <Icon name="lock" size={18} />
              <div className="passkey-meta">
                <span className="passkey-name">
                  {it.name} {it.synced && <span className="pill pill-success">{t("passkey.synced")}</span>}
                </span>
                <span className="small muted">
                  {t("passkey.created", { date: formatDate(it.created_at, false) })} ·{" "}
                  {it.last_used_at ? t("passkey.lastUsed", { date: formatDate(it.last_used_at) }) : t("passkey.neverUsed")}
                </span>
              </div>
              <div className="passkey-actions">
                <button className="icon-btn" onClick={() => openDialog({ kind: "rename", item: it })} aria-label={t("passkey.rename")} title={t("passkey.rename")}>
                  <Icon name="edit" size={16} />
                </button>
                <button className="icon-btn danger" onClick={() => openDialog({ kind: "remove", item: it })} aria-label={t("passkey.remove")} title={t("passkey.remove")}>
                  <Icon name="trash" size={16} />
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {supported ? (
        <div className="passkey-add">
          {platform && (
            <button className="btn btn-primary" onClick={() => openDialog({ kind: "add", platform: true })}>
              {t("passkey.addThisDevice", { method })}
            </button>
          )}
          <button className="btn" onClick={() => openDialog({ kind: "add", platform: false })}>
            <Icon name="plus" size={16} /> {t("passkey.add")}
          </button>
        </div>
      ) : (
        <p className="small muted">{t("passkey.unsupported")}</p>
      )}

      {dialog && (
        <Modal
          title={
            dialog.kind === "add"
              ? dialog.platform
                ? t("passkey.addThisDevice", { method })
                : t("passkey.add")
              : dialog.kind === "rename"
                ? t("passkey.rename")
                : t("passkey.removeTitle", { name: dialog.item.name })
          }
          onClose={() => setDialog(null)}
        >
          <form className="stack" onSubmit={submit}>
            {dialog.kind === "remove" && (
              <>
                <p>{t("passkey.removeText")}</p>
                {items?.length === 1 && !hasTotp && <div className="banner banner-warning">{t("passkey.lastWarning")}</div>}
              </>
            )}
            {dialog.kind === "rename" ? (
              <label>
                {t("passkey.renamePrompt")}
                <input autoFocus maxLength={64} value={name} onChange={(e) => setName(e.target.value)} />
              </label>
            ) : (
              <label>
                {dialog.kind === "add" ? t("passkey.passwordForAdd") : t("security.currentPassword")}
                <input type="password" autoFocus autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </label>
            )}
            {error && <div className="form-error">{error}</div>}
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                {t("common.cancel")}
              </button>
              <button className={`btn ${dialog.kind === "remove" ? "btn-danger" : "btn-primary"}`} disabled={busy || (dialog.kind === "rename" ? !name.trim() : !password)}>
                {dialog.kind === "remove" ? t("passkey.remove") : dialog.kind === "rename" ? t("common.save") : t("passkey.prompt.enable")}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}
