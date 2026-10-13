// Налаштування → Пошта: заблоковані відправники (по скриньках), розблокування і ручне додавання.
import { FormEvent, useEffect, useState } from "react";
import { del, errorText, get, post } from "../api/client";
import { useT } from "../i18n";
import { formatDate } from "../lib/format";
import Icon from "./Icon";
import { useToast } from "./Toast";

interface Blocked {
  id: number;
  mailbox: string;
  mailbox_address: string;
  address: string;
  created_at: string;
}

interface MailboxInfo {
  id: string;
  address: string;
}

export default function BlockedSendersSettings() {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<Blocked[] | null>(null);
  const [boxes, setBoxes] = useState<MailboxInfo[]>([]);
  const [box, setBox] = useState("");
  const [address, setAddress] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () =>
    get<Blocked[]>("/api/mail/blocked/")
      .then((r) => setItems(Array.isArray(r) ? r : []))
      .catch((e) => setError(errorText(e)));

  useEffect(() => {
    load();
    get<MailboxInfo[]>("/api/mail/mailboxes/")
      .then((r) => {
        setBoxes(r);
        if (r.length) setBox(r[0].id);
      })
      .catch(() => {});
  }, []);

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const value = address.trim();
    if (!value || !box) return;
    setError("");
    setBusy(true);
    try {
      const r = await post<{ moved: number }>(`/api/mail/blocked/?mailbox=${encodeURIComponent(box)}`, { address: value });
      toast(t("mail2.blockedToast", { address: value.toLowerCase(), n: r.moved || 0 }), "success");
      setAddress("");
      load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const unblock = async (b: Blocked) => {
    if (!window.confirm(t("mail2.confirmUnblock", { address: b.address }))) return;
    try {
      await del(`/api/mail/blocked/${b.id}/`);
      toast(t("mail2.unblocked", { address: b.address }), "success");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const groups = new Map<string, Blocked[]>();
  for (const b of items || []) groups.set(b.mailbox_address, [...(groups.get(b.mailbox_address) || []), b]);

  return (
    <div className="stack blocked-settings">
      <p className="small muted">{t("mail2.blockedHint")}</p>
      <form className="row gap wrap blocked-form" onSubmit={add}>
        {boxes.length > 1 && (
          <select className="select-sm" aria-label={t("mail.mailbox")} value={box} onChange={(e) => setBox(e.target.value)}>
            {boxes.map((b) => (
              <option key={b.id} value={b.id}>
                {b.address}
              </option>
            ))}
          </select>
        )}
        <input
          className="grow"
          inputMode="email"
          maxLength={320}
          placeholder={t("mail2.blockPlaceholder")}
          aria-label={t("mail2.blockPlaceholder")}
          value={address}
          onChange={(e) => setAddress(e.target.value)}
        />
        <button className="btn" disabled={busy || !address.trim() || !box}>
          <Icon name="shield" size={16} /> {t("mail2.block")}
        </button>
      </form>
      {error && <div className="form-error">{error}</div>}
      {items === null && !error && <div className="empty-small">{t("common.loading")}</div>}
      {items && items.length === 0 && <div className="empty-small">{t("mail2.blockedEmpty")}</div>}
      {Array.from(groups.entries()).map(([mailbox, list]) => (
        <div key={mailbox} className="stack-sm">
          {groups.size > 1 || boxes.length > 1 ? <strong className="small">{mailbox}</strong> : null}
          <div className="blocked-list">
            {list.map((b) => (
              <div key={b.id} className="blocked-item">
                <div className="blocked-addr">
                  <span className="truncate">{b.address}</span>
                  <span className="small muted">{t("mail2.blockedSince", { date: formatDate(b.created_at) })}</span>
                </div>
                <button type="button" className="btn btn-sm" onClick={() => unblock(b)}>
                  {t("mail2.unblock")}
                </button>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
