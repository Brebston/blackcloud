import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, del, errorText, get, patch, post } from "../api/client";
import type { MailFolder, MailMessage, MailSummary } from "../api/types";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import RichEditor, { type RichEditorHandle } from "../components/RichEditor";
import { useToast } from "../components/Toast";
import { useT } from "../i18n";
import type { TFunc, TKey } from "../i18n";
import { cleanHtml, textToHtml } from "../lib/htmlClean";
import { formatBytes, formatDate, senderName } from "../lib/format";

const FOLDER_LABELS: Record<string, TKey> = {
  "\\Inbox": "mail.folder.inbox",
  "\\Sent": "mail.folder.sent",
  "\\Drafts": "mail.folder.drafts",
  "\\Trash": "mail.folder.trash",
  "\\Junk": "mail.folder.junk",
  "\\Archive": "mail.folder.archive",
};
const FOLDER_ICONS: Record<string, string> = {
  "\\Inbox": "inbox",
  "\\Sent": "send",
  "\\Drafts": "edit",
  "\\Trash": "trash",
  "\\Junk": "alert",
  "\\Archive": "archive",
};

const MAX_TOTAL_BYTES = 15 * 1024 * 1024;
const CONFIDENTIAL_DAYS = [1, 7, 30, 90];
const MAILBOX_KEY = "bc_mailbox";

interface ListResult {
  total: number;
  page: number;
  page_size: number;
  results: MailSummary[];
}

interface MailboxInfo {
  id: string;
  address: string;
  display_name: string;
}

interface Signature {
  id: string;
  name: string;
  html: string;
  is_default: boolean;
}

interface ComposeInit {
  to?: string;
  cc?: string;
  subject?: string;
  /** Цитата або переслане повідомлення (HTML) — йде після підпису. */
  quoteHtml?: string;
  in_reply_to?: string;
  references?: string;
  in_reply_to_uid?: number;
  in_reply_to_folder?: string;
}

interface SendResult {
  message_id: string;
  confidential?: { id: string; expires_at: string; passcode: string | null };
}

interface ConfidentialItem {
  id: string;
  subject: string;
  recipients: string[];
  from_address: string;
  created_at: string;
  expires_at: string;
  revoked_at: string | null;
  active: boolean;
  view_count: number;
  has_passcode: boolean;
}

const folderLabel = (t: TFunc, f: MailFolder) => (FOLDER_LABELS[f.special || ""] ? t(FOLDER_LABELS[f.special || ""]) : f.name);

function readStoredMailbox(): string {
  try {
    return localStorage.getItem(MAILBOX_KEY) || "";
  } catch {
    return "";
  }
}

export default function MailPage() {
  const toast = useToast();
  const t = useT();
  const [mailboxes, setMailboxes] = useState<MailboxInfo[]>([]);
  const [mailbox, setMailbox] = useState<MailboxInfo | null>(null);
  const [unavailable, setUnavailable] = useState("");
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [folder, setFolder] = useState("INBOX");
  const [list, setList] = useState<ListResult | null>(null);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [open, setOpen] = useState<MailMessage | null>(null);
  const [remoteImages, setRemoteImages] = useState(false);
  const [compose, setCompose] = useState<ComposeInit | null>(null);
  const [loading, setLoading] = useState(false);
  const [showSignatures, setShowSignatures] = useState(false);
  const [showConfidential, setShowConfidential] = useState(false);
  const [passcodeInfo, setPasscodeInfo] = useState<SendResult["confidential"] | null>(null);

  // Усі запити веб-пошти явно вказують скриньку (у користувача їх може бути кілька)
  const box = mailbox?.id || "";
  const withBox = useCallback(
    (path: string) => (box ? `${path}${path.includes("?") ? "&" : "?"}mailbox=${encodeURIComponent(box)}` : path),
    [box],
  );

  const loadFolders = useCallback(() => get<MailFolder[]>(withBox("/api/mail/folders/")).then(setFolders), [withBox]);
  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      setList(
        await get<ListResult>(
          withBox(`/api/mail/messages/?folder=${encodeURIComponent(folder)}&page=${page}&q=${encodeURIComponent(appliedQuery)}`),
        ),
      );
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setLoading(false);
    }
  }, [folder, page, appliedQuery, toast, withBox]);

  useEffect(() => {
    get<MailboxInfo[]>("/api/mail/mailboxes/")
      .then((boxes) => {
        if (!boxes.length) {
          setUnavailable(t("mail.noMailbox"));
          return;
        }
        setMailboxes(boxes);
        const stored = readStoredMailbox();
        setMailbox(boxes.find((b) => b.id === stored) || boxes[0]);
      })
      .catch((e) => setUnavailable(errorText(e)));
  }, [t]);

  useEffect(() => {
    if (!mailbox) return;
    setOpen(null);
    loadFolders().catch((e) => setUnavailable(errorText(e)));
  }, [mailbox, loadFolders]);

  useEffect(() => {
    if (mailbox) loadList();
  }, [mailbox, loadList]);

  const switchMailbox = (id: string) => {
    const next = mailboxes.find((b) => b.id === id);
    if (!next) return;
    try {
      localStorage.setItem(MAILBOX_KEY, id);
    } catch {
      /* ignore */
    }
    setFolder("INBOX");
    setPage(1);
    setList(null);
    setQuery("");
    setAppliedQuery("");
    setMailbox(next);
  };

  const openMessage = async (m: MailSummary) => {
    try {
      setRemoteImages(false);
      setOpen(await get<MailMessage>(withBox(`/api/mail/messages/${m.uid}/?folder=${encodeURIComponent(folder)}`)));
      if (!m.seen) {
        setList((l) => l && { ...l, results: l.results.map((x) => (x.uid === m.uid ? { ...x, seen: true } : x)) });
        loadFolders();
      }
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const action = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn();
      toast(msg, "success");
      setOpen(null);
      loadList();
      loadFolders();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  const reply = (all: boolean) => {
    if (!open) return;
    setCompose({
      to: open.reply_to || open.from,
      cc: all
        ? [open.to, open.cc]
            .join(",")
            .split(",")
            .map((a) => a.trim())
            .filter((a) => a && !a.toLowerCase().includes((mailbox?.address || "\u0000").toLowerCase()))
            .join(", ")
        : "",
      subject: open.subject.toLowerCase().startsWith("re:") ? open.subject : `Re: ${open.subject}`,
      quoteHtml:
        `<div class="bc-quote"><div>${textToHtml(t("mail.wrote", { date: formatDate(open.date), from: open.from }))}</div>` +
        `<blockquote>${textToHtml(open.text)}</blockquote></div>`,
      in_reply_to: open.message_id,
      references: [open.references, open.message_id].filter(Boolean).join(" "),
      in_reply_to_uid: open.uid,
      in_reply_to_folder: folder,
    });
  };

  const forward = () => {
    if (!open) return;
    const head = [
      t("mail.fwdHeader"),
      `${t("mail.from")}: ${open.from}`,
      `${t("mail.date")}: ${formatDate(open.date)}`,
      `${t("mail.subject")}: ${open.subject}`,
      `${t("mail.to")}: ${open.to}`,
    ].join("\n");
    setCompose({
      subject: `Fwd: ${open.subject}`,
      quoteHtml: `<div class="bc-quote"><div>${textToHtml(head)}</div><br><div>${textToHtml(open.text)}</div></div>`,
    });
  };

  if (unavailable) {
    return (
      <div className="page">
        <div className="empty">
          <Icon name="mail" size={36} />
          <div>{unavailable}</div>
        </div>
      </div>
    );
  }

  const totalPages = list ? Math.max(1, Math.ceil(list.total / list.page_size)) : 1;
  const folderParam = encodeURIComponent(folder);

  return (
    <div className="page mail-page">
      <div className="page-head">
        <div>
          <h2>{t("nav.mail")}</h2>
          {mailboxes.length > 1 ? (
            <select
              className="select-sm mailbox-switch"
              aria-label={t("mail.mailbox")}
              value={mailbox?.id || ""}
              onChange={(e) => switchMailbox(e.target.value)}
            >
              {mailboxes.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.display_name ? `${b.display_name} <${b.address}>` : b.address}
                </option>
              ))}
            </select>
          ) : (
            mailbox && <div className="muted small">{mailbox.address}</div>
          )}
        </div>
        <div className="toolbar">
          <form
            className="search"
            onSubmit={(e) => {
              e.preventDefault();
              setPage(1);
              setAppliedQuery(query.trim());
            }}
          >
            <Icon name="search" size={16} />
            <input placeholder={t("mail.searchFolder")} value={query} onChange={(e) => setQuery(e.target.value)} />
          </form>
          <button
            className="icon-btn"
            onClick={() => {
              loadList();
              loadFolders();
            }}
            aria-label={t("common.refresh")}
            title={t("common.refresh")}
          >
            <Icon name="refresh" />
          </button>
          <button className="icon-btn" onClick={() => setShowSignatures(true)} aria-label={t("mail.signatures")} title={t("mail.signatures")}>
            <Icon name="signature" />
          </button>
          <button className="icon-btn" onClick={() => setShowConfidential(true)} aria-label={t("mail.confidentialSent")} title={t("mail.confidentialSent")}>
            <Icon name="lock" />
          </button>
          <button className="btn btn-primary" onClick={() => setCompose({})} disabled={!mailbox}>
            <Icon name="edit" size={16} /> {t("mail.compose")}
          </button>
        </div>
      </div>

      <div className="mail-layout">
        <nav className="card mail-folders">
          {folders.map((f) => (
            <button
              key={f.name}
              className={`folder-btn ${f.name === folder ? "active" : ""}`}
              onClick={() => {
                setFolder(f.name);
                setPage(1);
                setOpen(null);
                setAppliedQuery("");
                setQuery("");
              }}
            >
              <Icon name={FOLDER_ICONS[f.special || ""] || "folder"} size={16} />
              <span className="truncate">{folderLabel(t, f)}</span>
              {f.unseen > 0 && <span className="count">{f.unseen}</span>}
            </button>
          ))}
          <Link to="/settings?tab=mail" className="small muted mail-settings-link">
            {t("mail.imapSettings")}
          </Link>
        </nav>

        <section className={`card mail-list ${open ? "has-open" : ""}`}>
          {loading && !list && <div className="empty-small">{t("common.loading")}</div>}
          {list && list.results.length === 0 && <div className="empty-small">{t("mail.empty")}</div>}
          {list?.results.map((m) => (
            <button key={m.uid} className={`mail-row ${m.seen ? "" : "unread"} ${open?.uid === m.uid ? "active" : ""}`} onClick={() => openMessage(m)}>
              <div className="row-between">
                <span className="truncate mail-from">{senderName(folder.toLowerCase().includes("sent") ? m.to : m.from)}</span>
                <span className="small muted nowrap">{formatDate(m.date)}</span>
              </div>
              <div className="row gap-sm">
                {m.flagged && <Icon name="star" size={12} className="flag" />}
                {m.has_attachments && <Icon name="paperclip" size={12} />}
                <span className="truncate mail-subject">{m.subject}</span>
              </div>
            </button>
          ))}
          {list && totalPages > 1 && (
            <div className="pager">
              <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
                ‹
              </button>
              <span className="small muted">
                {page} / {totalPages}
              </span>
              <button className="btn btn-sm" disabled={page >= totalPages} onClick={() => setPage(page + 1)}>
                ›
              </button>
            </div>
          )}
        </section>

        <section className="card mail-reader">
          {!open && (
            <div className="empty">
              <Icon name="mail" size={32} />
              {t("mail.pick")}
            </div>
          )}
          {open && (
            <>
              <div className="reader-actions">
                <button className="icon-btn mobile-only" onClick={() => setOpen(null)} aria-label={t("common.back")}>
                  <Icon name="chevronLeft" />
                </button>
                <button className="btn btn-sm" onClick={() => reply(false)}>
                  <Icon name="reply" size={14} /> {t("mail.reply")}
                </button>
                <button className="btn btn-sm" onClick={() => reply(true)}>
                  {t("mail.replyAll")}
                </button>
                <button className="btn btn-sm" onClick={forward}>
                  {t("mail.forward")}
                </button>
                <button
                  className="icon-btn"
                  title={t("mail.flag")}
                  onClick={() => action(() => patch(withBox(`/api/mail/messages/${open.uid}/`), { folder, flagged: true }), t("mail.flagged"))}
                >
                  <Icon name="star" size={16} />
                </button>
                <button
                  className="icon-btn"
                  title={t("mail.unread")}
                  onClick={() => action(() => patch(withBox(`/api/mail/messages/${open.uid}/`), { folder, seen: false }), t("mail.markedUnread"))}
                >
                  <Icon name="eye" size={16} />
                </button>
                <select
                  className="select-sm"
                  value=""
                  onChange={(e) =>
                    e.target.value &&
                    action(() => patch(withBox(`/api/mail/messages/${open.uid}/`), { folder, move_to: e.target.value }), t("mail.moved"))
                  }
                >
                  <option value="">{t("mail.moveTo")}</option>
                  {folders
                    .filter((f) => f.name !== folder)
                    .map((f) => (
                      <option key={f.name} value={f.name}>
                        {folderLabel(t, f)}
                      </option>
                    ))}
                </select>
                <button
                  className="icon-btn danger"
                  title={t("common.delete")}
                  onClick={() => action(() => del(withBox(`/api/mail/messages/${open.uid}/?folder=${folderParam}`)), t("mail.deleted"))}
                >
                  <Icon name="trash" size={16} />
                </button>
              </div>
              <h3 className="reader-subject">{open.subject || t("mail.noSubject")}</h3>
              <div className="reader-meta">
                <div>
                  <strong>{open.from}</strong>
                </div>
                <div className="small muted">
                  {t("mail.to")}: {open.to}
                  {open.cc && ` · ${t("mail.cc")}: ${open.cc}`}
                </div>
                <div className="small muted">{formatDate(open.date)}</div>
              </div>
              {open.attachments.length > 0 && (
                <div className="attachments">
                  {open.attachments.map((a) => (
                    <a
                      key={a.index}
                      className="attachment"
                      href={withBox(`/api/mail/messages/${open.uid}/attachments/${a.index}/?folder=${folderParam}`)}
                    >
                      <Icon name="paperclip" size={14} /> {a.filename} <span className="muted small">{formatBytes(a.size)}</span>
                    </a>
                  ))}
                </div>
              )}
              {open.has_html ? (
                <>
                  {!remoteImages && (
                    <div className="banner banner-info small">
                      {t("mail.remoteBlocked")}{" "}
                      <button className="link-btn" onClick={() => setRemoteImages(true)}>
                        {t("mail.show")}
                      </button>
                    </div>
                  )}
                  <iframe
                    className="mail-frame"
                    title={t("mail.content")}
                    sandbox="allow-popups allow-popups-to-escape-sandbox"
                    referrerPolicy="no-referrer"
                    src={withBox(`/api/mail/render/?folder=${folderParam}&uid=${open.uid}${remoteImages ? "&remote=1" : ""}`)}
                  />
                </>
              ) : (
                <pre className="mail-text">{open.text}</pre>
              )}
            </>
          )}
        </section>
      </div>

      {compose && mailbox && (
        <ComposeDialog
          init={compose}
          mailbox={mailbox}
          onClose={() => setCompose(null)}
          onSent={(result) => {
            setCompose(null);
            toast(t("mail.sent"), "success");
            if (result.confidential?.passcode) setPasscodeInfo(result.confidential);
            loadFolders();
          }}
        />
      )}
      {showSignatures && <SignaturesDialog onClose={() => setShowSignatures(false)} />}
      {showConfidential && <ConfidentialDialog onClose={() => setShowConfidential(false)} />}
      {passcodeInfo && (
        <Modal title={t("conf.passcodeTitle")} onClose={() => setPasscodeInfo(null)}>
          <div className="stack">
            <p>{t("conf.passcodeText")}</p>
            <code className="big-code">{passcodeInfo.passcode}</code>
            <p className="small muted">{t("conf.expires", { date: formatDate(passcodeInfo.expires_at) })}</p>
            <div className="modal-actions">
              <button className="btn btn-primary" onClick={() => setPasscodeInfo(null)}>
                {t("common.done")}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

function ComposeDialog({
  init,
  mailbox,
  onClose,
  onSent,
}: {
  init: ComposeInit;
  mailbox: MailboxInfo;
  onClose: () => void;
  onSent: (result: SendResult) => void;
}) {
  const t = useT();
  const editor = useRef<RichEditorHandle>(null);
  const [form, setForm] = useState({
    to: init.to || "",
    cc: init.cc || "",
    bcc: "",
    subject: init.subject || "",
  });
  const [files, setFiles] = useState<File[]>([]);
  const [showCc, setShowCc] = useState(!!init.cc);
  const [signatures, setSignatures] = useState<Signature[]>([]);
  const [signatureId, setSignatureId] = useState("");
  const [confidential, setConfidential] = useState(false);
  const [confDays, setConfDays] = useState(7);
  const [confPasscode, setConfPasscode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Підпис за замовчуванням вставляється одразу (як у Gmail); його можна змінити або прибрати
  useEffect(() => {
    get<Signature[]>("/api/mail/signatures/")
      .then((sigs) => {
        setSignatures(sigs);
        const def = sigs.find((s) => s.is_default);
        if (def) {
          setSignatureId(def.id);
          editor.current?.setSignature(def.html);
        }
      })
      .catch(() => {});
  }, []);

  const attachSize = files.reduce((s, f) => s + f.size, 0);
  const emailRe = /[^@\s<>",]+@[^@\s<>",]+\.[^@\s<>",]+/;

  const send = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    const ed = editor.current;
    if (!ed) return;
    if (!form.to.trim() && !form.cc.trim() && !form.bcc.trim()) return setError(t("compose.noRecipient"));
    for (const field of ["to", "cc", "bcc"] as const) {
      const parts = form[field].split(",").map((p) => p.trim()).filter(Boolean);
      const bad = parts.find((p) => !emailRe.test(p));
      if (bad) return setError(t("compose.badAddress", { address: bad }));
    }
    if (confidential && files.length) return setError(t("compose.confNoAttachments"));
    if (attachSize + ed.imageBytes() > MAX_TOTAL_BYTES) return setError(t("compose.tooBig"));
    if (ed.isEmpty() && !window.confirm(t("compose.confirmEmpty"))) return;
    if (!form.subject.trim() && !window.confirm(t("compose.confirmNoSubject"))) return;

    const data = new FormData();
    Object.entries(form).forEach(([k, v]) => data.append(k, v));
    data.append("mailbox", mailbox.id);
    data.append("html", ed.html());
    data.append("body", ed.text());
    if (confidential) {
      data.append("confidential", "1");
      data.append("confidential_days", String(confDays));
      if (confPasscode) data.append("confidential_passcode", "1");
    }
    if (init.in_reply_to) data.append("in_reply_to", init.in_reply_to);
    if (init.references) data.append("references", init.references);
    if (init.in_reply_to_uid) data.append("in_reply_to_uid", String(init.in_reply_to_uid));
    if (init.in_reply_to_folder) data.append("in_reply_to_folder", init.in_reply_to_folder);
    files.forEach((f) => data.append("attachments", f));
    setBusy(true);
    try {
      onSent(await api<SendResult>("/api/mail/send/", { method: "POST", body: data }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });
  const initialHtml = `<div><br></div>${init.quoteHtml ? `<br>${init.quoteHtml}` : ""}`;

  return (
    <Modal title={t("compose.title")} onClose={onClose} wide>
      <form className="stack compose" onSubmit={send}>
        <div className="small muted">
          {t("compose.from")}: {mailbox.display_name ? `${mailbox.display_name} <${mailbox.address}>` : mailbox.address}
        </div>
        <div className="row gap">
          <input className="grow" placeholder={t("compose.to")} value={form.to} onChange={set("to")} autoFocus={!init.to} />
          {!showCc && (
            <button type="button" className="link-btn" onClick={() => setShowCc(true)}>
              {t("mail.cc")}
            </button>
          )}
        </div>
        {showCc && (
          <>
            <input placeholder={t("mail.cc")} value={form.cc} onChange={set("cc")} />
            <input placeholder={t("compose.bcc")} value={form.bcc} onChange={set("bcc")} />
          </>
        )}
        <input placeholder={t("mail.subject")} value={form.subject} onChange={set("subject")} maxLength={300} />
        <RichEditor ref={editor} initialHtml={initialHtml} onError={setError} autoFocus={!!init.to} />
        <div className="row gap wrap">
          <label className={`btn btn-sm ${confidential ? "disabled" : ""}`} title={confidential ? t("compose.confNoAttachments") : undefined}>
            <Icon name="paperclip" size={14} /> {t("compose.attach")}
            <input
              type="file"
              multiple
              hidden
              disabled={confidential}
              onChange={(e) => {
                if (e.target.files) setFiles([...files, ...Array.from(e.target.files as FileList)].slice(0, 20));
                e.target.value = "";
              }}
            />
          </label>
          {files.map((f, i) => (
            <span key={i} className="pill">
              {f.name} ({formatBytes(f.size)})
              <button type="button" className="pill-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label={t("common.remove")}>
                ×
              </button>
            </span>
          ))}
          <span className="grow" />
          <label className="row gap-sm small">
            <Icon name="signature" size={14} />
            <select
              className="select-sm"
              aria-label={t("compose.signature")}
              value={signatureId}
              onChange={(e) => {
                setSignatureId(e.target.value);
                editor.current?.setSignature(signatures.find((s) => s.id === e.target.value)?.html || null);
              }}
            >
              <option value="">{t("compose.noSignature")}</option>
              {signatures.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            className={`btn btn-sm ${confidential ? "btn-active" : ""}`}
            aria-pressed={confidential}
            onClick={() => setConfidential(!confidential)}
          >
            <Icon name="lock" size={14} /> {t("compose.confidential")}
          </button>
        </div>
        {confidential && (
          <div className="banner banner-info stack small">
            <div className="row gap wrap">
              <label className="row gap-sm">
                {t("compose.confExpires")}
                <select className="select-sm" value={confDays} onChange={(e) => setConfDays(Number(e.target.value))}>
                  {CONFIDENTIAL_DAYS.map((d) => (
                    <option key={d} value={d}>
                      {t("compose.days", { n: d })}
                    </option>
                  ))}
                </select>
              </label>
              <label className="check">
                <input type="checkbox" checked={confPasscode} onChange={(e) => setConfPasscode(e.target.checked)} />
                {t("compose.confPasscode")}
              </label>
            </div>
            <div>{t("compose.confExplain")}</div>
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            {t("common.cancel")}
          </button>
          <button className="btn btn-primary" disabled={busy}>
            <Icon name="send" size={16} /> {busy ? t("compose.sending") : t("compose.send")}
          </button>
        </div>
      </form>
    </Modal>
  );
}

function SignaturesDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<Signature[] | null>(null);
  const [editing, setEditing] = useState<Partial<Signature> | null>(null);
  const [error, setError] = useState("");
  const editor = useRef<RichEditorHandle>(null);

  const load = () =>
    get<Signature[]>("/api/mail/signatures/")
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    load();
  }, []);

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!editing) return;
    setError("");
    const body = { name: (editing.name || "").trim(), html: editor.current?.html() || "", is_default: !!editing.is_default };
    if (!body.name) return setError(t("sig.nameRequired"));
    try {
      if (editing.id) await patch(`/api/mail/signatures/${editing.id}/`, body);
      else await post("/api/mail/signatures/", body);
      setEditing(null);
      toast(t("common.saved"), "success");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const remove = async (s: Signature) => {
    if (!window.confirm(t("sig.confirmDelete", { name: s.name }))) return;
    try {
      await del(`/api/mail/signatures/${s.id}/`);
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={t("mail.signatures")} onClose={onClose} wide>
      {editing ? (
        <form className="stack" onSubmit={save}>
          <label>
            {t("sig.name")}
            <input value={editing.name || ""} maxLength={100} autoFocus onChange={(e) => setEditing({ ...editing, name: e.target.value })} />
          </label>
          <RichEditor ref={editor} initialHtml={editing.html || ""} onError={setError} compact />
          <label className="check">
            <input type="checkbox" checked={!!editing.is_default} onChange={(e) => setEditing({ ...editing, is_default: e.target.checked })} />
            {t("sig.default")}
          </label>
          {error && <div className="form-error">{error}</div>}
          <div className="modal-actions">
            <button type="button" className="btn" onClick={() => setEditing(null)}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-primary">{t("common.save")}</button>
          </div>
        </form>
      ) : (
        <div className="stack">
          {items === null && !error && <div className="empty-small">{t("common.loading")}</div>}
          {items && items.length === 0 && <div className="empty-small">{t("sig.empty")}</div>}
          {items?.map((s) => (
            <div key={s.id} className="card sig-item">
              <div className="row-between">
                <strong>
                  {s.name} {s.is_default && <span className="pill">{t("sig.defaultPill")}</span>}
                </strong>
                <div className="row gap-sm">
                  <button className="btn btn-sm" onClick={() => setEditing(s)}>
                    {t("common.edit")}
                  </button>
                  <button className="btn btn-sm btn-danger" onClick={() => remove(s)}>
                    {t("common.delete")}
                  </button>
                </div>
              </div>
              {/* HTML уже очищений сервером (nh3); додатково — клієнтський фільтр */}
              <div className="sig-preview" dangerouslySetInnerHTML={{ __html: cleanHtml(s.html) }} />
            </div>
          ))}
          {error && <div className="form-error">{error}</div>}
          <div className="modal-actions">
            <button className="btn btn-primary" onClick={() => setEditing({ name: "", html: "", is_default: !items?.length })}>
              <Icon name="plus" size={16} /> {t("sig.new")}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ConfidentialDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<ConfidentialItem[] | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    get<ConfidentialItem[]>("/api/mail/confidential/")
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    load();
  }, []);

  const revoke = async (item: ConfidentialItem) => {
    if (!window.confirm(t("conf.confirmRevoke"))) return;
    try {
      await post(`/api/mail/confidential/${item.id}/revoke/`);
      toast(t("conf.revoked"), "success");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={t("mail.confidentialSent")} onClose={onClose} wide>
      <div className="stack">
        <p className="small muted">{t("conf.listHint")}</p>
        {items === null && !error && <div className="empty-small">{t("common.loading")}</div>}
        {items && items.length === 0 && <div className="empty-small">{t("conf.empty")}</div>}
        {items && items.length > 0 && (
          <div className="table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("mail.subject")}</th>
                  <th>{t("conf.recipients")}</th>
                  <th>{t("conf.status")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.id} className={m.active ? "" : "inactive"}>
                    <td>
                      <div className="truncate">{m.subject || t("mail.noSubject")}</div>
                      <div className="small muted">{formatDate(m.created_at)}</div>
                    </td>
                    <td className="small">{m.recipients.join(", ")}</td>
                    <td className="small">
                      {m.revoked_at ? (
                        <span className="pill pill-danger">{t("conf.statusRevoked")}</span>
                      ) : m.active ? (
                        <span className="pill pill-success">{t("conf.until", { date: formatDate(m.expires_at) })}</span>
                      ) : (
                        <span className="pill">{t("conf.statusExpired")}</span>
                      )}
                      <div className="muted">
                        {t("conf.views", { n: m.view_count })}
                        {m.has_passcode && ` · ${t("conf.withPasscode")}`}
                      </div>
                    </td>
                    <td>
                      {m.active && (
                        <button className="btn btn-sm btn-danger" onClick={() => revoke(m)}>
                          {t("conf.revoke")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
      </div>
    </Modal>
  );
}
