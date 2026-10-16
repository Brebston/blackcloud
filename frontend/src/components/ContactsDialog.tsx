import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { del, errorText, get, patch, post } from "../api/client";
import { useT } from "../i18n";
import Icon from "./Icon";
import Modal from "./Modal";
import { useToast } from "./Toast";

// Адресна книга (особисті контакти). Відкривається з пошти; «Написати» відкриває новий лист.

interface EmailItem {
  address: string;
  label: string;
}
interface PhoneItem {
  number: string;
  label: string;
}
export interface Contact {
  id: string;
  first_name: string;
  last_name: string;
  nickname: string;
  display_name: string;
  emails: EmailItem[];
  phones: PhoneItem[];
  company: string;
  job_title: string;
  birthday: string | null;
  address: string;
  notes: string;
  starred: boolean;
}
export type ContactDraft = Partial<Omit<Contact, "id" | "display_name">>;

const EMPTY: ContactDraft = {
  first_name: "",
  last_name: "",
  nickname: "",
  emails: [{ address: "", label: "" }],
  phones: [],
  company: "",
  job_title: "",
  birthday: null,
  address: "",
  notes: "",
  starred: false,
};

function initials(c: Contact): string {
  const parts = (c.display_name || "?").split(/[\s.@_-]+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

function recipientOf(c: Contact): string {
  const addr = c.emails[0]?.address;
  if (!addr) return "";
  const name = [c.first_name, c.last_name].filter(Boolean).join(" ") || c.nickname;
  return name ? `${/[,;<>"@]/.test(name) ? `"${name.replace(/["\\]/g, "")}"` : name} <${addr}>, ` : `${addr}, `;
}

/** Адресна книга без обгортки: і у вікні з пошти, і як окрема сторінка «Контакти». */
export function ContactsBook({
  draft,
  onWrite,
  onCancelDraft,
}: {
  draft?: ContactDraft;
  onWrite: (to: string) => void;
  /** Скасування нового контакту, відкритого одразу (напр., «Додати до контактів» з листа). */
  onCancelDraft?: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const [list, setList] = useState<Contact[] | null>(null);
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState<(ContactDraft & { id?: string }) | null>(draft ? { ...EMPTY, ...draft } : null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    get<Contact[]>("/api/mail/contacts/")
      .then(setList)
      .catch((e) => setError(errorText(e)));
  }, []);
  useEffect(load, [load]);

  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const all = list || [];
    const match = needle
      ? all.filter((c) =>
          [c.display_name, c.nickname, c.company, ...c.emails.map((e) => e.address), ...c.phones.map((p) => p.number)]
            .join(" ")
            .toLowerCase()
            .includes(needle),
        )
      : all;
    return [...match].sort((a, b) => Number(b.starred) - Number(a.starred) || a.display_name.localeCompare(b.display_name));
  }, [list, q]);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setError("");
    const body = {
      ...editing,
      emails: (editing.emails || []).filter((x) => x.address.trim()).map((x) => ({ address: x.address.trim(), label: x.label })),
      phones: (editing.phones || []).filter((x) => x.number.trim()).map((x) => ({ number: x.number.trim(), label: x.label })),
      birthday: editing.birthday || null,
    };
    delete (body as { id?: string }).id;
    const named = [body.first_name, body.last_name, body.nickname, body.company].some((v) => (v || "").trim());
    if (!named && body.emails.length === 0) return setError(t("contacts.nameOrEmail"));
    setBusy(true);
    try {
      if (editing.id) await patch(`/api/mail/contacts/${editing.id}/`, body);
      else await post("/api/mail/contacts/", body);
      toast(t("contacts.saved"), "success");
      setEditing(null);
      load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c: Contact) => {
    if (!window.confirm(t("contacts.deleteConfirm", { name: c.display_name }))) return;
    try {
      await del(`/api/mail/contacts/${c.id}/`);
      toast(t("contacts.deleted"), "success");
      setEditing(null);
      load();
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const toggleStar = async (c: Contact) => {
    try {
      await patch(`/api/mail/contacts/${c.id}/`, { starred: !c.starred });
      load();
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const setField = (k: keyof ContactDraft, v: unknown) => setEditing((cur) => (cur ? { ...cur, [k]: v } : cur));

  if (editing) {
    const emails = editing.emails || [];
    const phones = editing.phones || [];
    return (
      <div className="stack">
        <h3>{editing.id ? t("contacts.edit") : t("contacts.add")}</h3>
        <form className="stack contact-form" onSubmit={save}>
          <div className="grid-2">
            <label>
              {t("contacts.firstName")}
              <input value={editing.first_name || ""} maxLength={100} autoFocus onChange={(e) => setField("first_name", e.target.value)} />
            </label>
            <label>
              {t("contacts.lastName")}
              <input value={editing.last_name || ""} maxLength={100} onChange={(e) => setField("last_name", e.target.value)} />
            </label>
          </div>
          <fieldset className="contact-multi">
            <legend>{t("contacts.emails")}</legend>
            {emails.map((item, i) => (
              <div className="row gap-sm" key={i}>
                <input
                  className="grow"
                  type="email"
                  inputMode="email"
                  value={item.address}
                  maxLength={320}
                  onChange={(e) => setField("emails", emails.map((x, j) => (j === i ? { ...x, address: e.target.value } : x)))}
                />
                <input
                  className="contact-label"
                  list="contact-email-labels"
                  placeholder={t("contacts.label")}
                  value={item.label}
                  maxLength={40}
                  onChange={(e) => setField("emails", emails.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
                />
                <button type="button" className="icon-btn" aria-label={t("contacts.delete")} onClick={() => setField("emails", emails.filter((_x, j) => j !== i))}>
                  <Icon name="x" size={14} />
                </button>
              </div>
            ))}
            {emails.length < 10 && (
              <button type="button" className="link-btn" onClick={() => setField("emails", [...emails, { address: "", label: "" }])}>
                {t("contacts.addEmail")}
              </button>
            )}
          </fieldset>
          <fieldset className="contact-multi">
            <legend>{t("contacts.phones")}</legend>
            {phones.map((item, i) => (
              <div className="row gap-sm" key={i}>
                <input
                  className="grow"
                  type="tel"
                  inputMode="tel"
                  value={item.number}
                  maxLength={40}
                  onChange={(e) => setField("phones", phones.map((x, j) => (j === i ? { ...x, number: e.target.value } : x)))}
                />
                <input
                  className="contact-label"
                  list="contact-phone-labels"
                  placeholder={t("contacts.label")}
                  value={item.label}
                  maxLength={40}
                  onChange={(e) => setField("phones", phones.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)))}
                />
                <button type="button" className="icon-btn" aria-label={t("contacts.delete")} onClick={() => setField("phones", phones.filter((_x, j) => j !== i))}>
                  <Icon name="x" size={14} />
                </button>
              </div>
            ))}
            {phones.length < 10 && (
              <button type="button" className="link-btn" onClick={() => setField("phones", [...phones, { number: "", label: "" }])}>
                {t("contacts.addPhone")}
              </button>
            )}
          </fieldset>
          <datalist id="contact-email-labels">
            <option value={t("contacts.labelWork")} />
            <option value={t("contacts.labelHome")} />
          </datalist>
          <datalist id="contact-phone-labels">
            <option value={t("contacts.labelMobile")} />
            <option value={t("contacts.labelWork")} />
            <option value={t("contacts.labelHome")} />
          </datalist>
          <div className="grid-2">
            <label>
              {t("contacts.company")}
              <input value={editing.company || ""} maxLength={150} onChange={(e) => setField("company", e.target.value)} />
            </label>
            <label>
              {t("contacts.jobTitle")}
              <input value={editing.job_title || ""} maxLength={100} onChange={(e) => setField("job_title", e.target.value)} />
            </label>
            <label>
              {t("contacts.nickname")}
              <input value={editing.nickname || ""} maxLength={100} onChange={(e) => setField("nickname", e.target.value)} />
            </label>
            <label>
              {t("contacts.birthday")}
              <input type="date" value={editing.birthday || ""} onChange={(e) => setField("birthday", e.target.value || null)} />
            </label>
          </div>
          <label>
            {t("contacts.address")}
            <input value={editing.address || ""} maxLength={300} onChange={(e) => setField("address", e.target.value)} />
          </label>
          <label>
            {t("contacts.notes")}
            <textarea rows={3} maxLength={2000} value={editing.notes || ""} onChange={(e) => setField("notes", e.target.value)} />
          </label>
          {error && <div className="form-error">{error}</div>}
          <div className="modal-actions">
            {editing.id && (
              <button type="button" className="btn btn-danger-outline" onClick={() => remove(editing as Contact)}>
                <Icon name="trash" size={16} /> {t("contacts.delete")}
              </button>
            )}
            <div className="spacer" />
            <button type="button" className="btn" onClick={() => (draft && !editing.id && onCancelDraft ? onCancelDraft() : setEditing(null))}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-primary" disabled={busy}>
              {t("common.save")}
            </button>
          </div>
        </form>
      </div>
    );
  }

  return (
    <div className="stack">
        <div className="toolbar">
          <div className="search grow">
            <Icon name="search" size={16} />
            <input placeholder={t("contacts.search")} value={q} onChange={(e) => setQ(e.target.value)} autoFocus />
          </div>
          <button className="btn btn-primary" onClick={() => setEditing({ ...EMPTY })}>
            <Icon name="plus" size={16} /> {t("contacts.add")}
          </button>
        </div>
        {error && <div className="form-error">{error}</div>}
        {list === null && <div className="empty-small">{t("common.loading")}</div>}
        {list && list.length === 0 && <div className="empty-small">{t("contacts.empty")}</div>}
        {list && list.length > 0 && filtered.length === 0 && <div className="empty-small">{t("contacts.noResults")}</div>}
        <ul className="contact-list">
          {filtered.map((c) => (
            <li key={c.id} className="contact-row">
              <span className="recipient-av" aria-hidden="true">
                {initials(c)}
              </span>
              <button type="button" className="contact-main" onClick={() => setEditing({ ...c })}>
                <span className="truncate">
                  <strong>{c.display_name}</strong>
                  {c.company && <span className="muted"> · {c.company}</span>}
                </span>
                <span className="small muted truncate">{[c.emails[0]?.address, c.phones[0]?.number].filter(Boolean).join(" · ")}</span>
              </button>
              <button
                type="button"
                className={`icon-btn ${c.starred ? "starred" : ""}`}
                aria-label={c.starred ? t("contacts.unstar") : t("contacts.star")}
                title={c.starred ? t("contacts.unstar") : t("contacts.star")}
                onClick={() => toggleStar(c)}
              >
                <Icon name="star" size={16} />
              </button>
              {c.emails.length > 0 && (
                <button type="button" className="btn btn-sm contact-write" onClick={() => onWrite(recipientOf(c))} aria-label={t("contacts.write")} title={t("contacts.write")}>
                  <Icon name="send" size={14} /> <span className="contact-write-label">{t("contacts.write")}</span>
                </button>
              )}
            </li>
          ))}
        </ul>
        {list && list.length > 0 && <p className="small muted">{t("contacts.count", { n: list.length })}</p>}
    </div>
  );
}

export default function ContactsDialog({
  draft,
  onClose,
  onWrite,
}: {
  draft?: ContactDraft;
  onClose: () => void;
  onWrite: (to: string) => void;
}) {
  const t = useT();
  return (
    <Modal title={t("contacts.title")} onClose={onClose} wide>
      <ContactsBook draft={draft} onWrite={onWrite} onCancelDraft={onClose} />
    </Modal>
  );
}
