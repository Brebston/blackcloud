import { FormEvent, useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, del, errorText, get, patch } from "../api/client";
import type { MailFolder, MailMessage, MailSummary } from "../api/types";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { formatBytes, formatDate, senderName } from "../lib/format";

const FOLDER_LABELS: Record<string, string> = {
  "\\Inbox": "Вхідні",
  "\\Sent": "Надіслані",
  "\\Drafts": "Чернетки",
  "\\Trash": "Кошик",
  "\\Junk": "Спам",
  "\\Archive": "Архів",
};
const FOLDER_ICONS: Record<string, string> = {
  "\\Inbox": "inbox",
  "\\Sent": "send",
  "\\Drafts": "edit",
  "\\Trash": "trash",
  "\\Junk": "alert",
  "\\Archive": "archive",
};

interface ListResult {
  total: number;
  page: number;
  page_size: number;
  results: MailSummary[];
}

interface ComposeInit {
  to?: string;
  cc?: string;
  subject?: string;
  body?: string;
  in_reply_to?: string;
  references?: string;
  in_reply_to_uid?: number;
  in_reply_to_folder?: string;
}

export default function MailPage() {
  const toast = useToast();
  const [mailbox, setMailbox] = useState<{ address: string } | null>(null);
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

  const loadFolders = useCallback(() => get<MailFolder[]>("/api/mail/folders/").then(setFolders), []);
  const loadList = useCallback(async () => {
    setLoading(true);
    try {
      setList(
        await get<ListResult>(
          `/api/mail/messages/?folder=${encodeURIComponent(folder)}&page=${page}&q=${encodeURIComponent(appliedQuery)}`,
        ),
      );
    } catch (e) {
      toast(errorText(e), "error");
    } finally {
      setLoading(false);
    }
  }, [folder, page, appliedQuery, toast]);

  useEffect(() => {
    get<{ address: string }>("/api/mail/mailbox/")
      .then((mb) => {
        setMailbox(mb);
        loadFolders().catch((e) => setUnavailable(errorText(e)));
      })
      .catch((e) => setUnavailable(errorText(e)));
  }, [loadFolders]);

  useEffect(() => {
    if (mailbox) loadList();
  }, [mailbox, loadList]);

  const openMessage = async (m: MailSummary) => {
    try {
      setRemoteImages(false);
      setOpen(await get<MailMessage>(`/api/mail/messages/${m.uid}/?folder=${encodeURIComponent(folder)}`));
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
    const quoted = open.text
      .split("\n")
      .map((l) => `> ${l}`)
      .join("\n");
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
      body: `\n\n${formatDate(open.date)}, ${open.from} пише:\n${quoted}`,
      in_reply_to: open.message_id,
      references: [open.references, open.message_id].filter(Boolean).join(" "),
      in_reply_to_uid: open.uid,
      in_reply_to_folder: folder,
    });
  };

  const forward = () => {
    if (!open) return;
    setCompose({
      subject: `Fwd: ${open.subject}`,
      body: `\n\n---------- Переслане повідомлення ----------\nВід: ${open.from}\nДата: ${formatDate(open.date)}\nТема: ${open.subject}\nКому: ${open.to}\n\n${open.text}`,
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

  return (
    <div className="page mail-page">
      <div className="page-head">
        <div>
          <h2>Пошта</h2>
          {mailbox && <div className="muted small">{mailbox.address}</div>}
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
            <input placeholder="Пошук у теці…" value={query} onChange={(e) => setQuery(e.target.value)} />
          </form>
          <button className="icon-btn" onClick={() => { loadList(); loadFolders(); }} aria-label="Оновити">
            <Icon name="refresh" />
          </button>
          <button className="btn btn-primary" onClick={() => setCompose({})}>
            <Icon name="edit" size={16} /> Написати
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
              <span className="truncate">{FOLDER_LABELS[f.special || ""] || f.name}</span>
              {f.unseen > 0 && <span className="count">{f.unseen}</span>}
            </button>
          ))}
          <Link to="/settings?tab=mail" className="small muted mail-settings-link">
            Налаштування IMAP/SMTP
          </Link>
        </nav>

        <section className={`card mail-list ${open ? "has-open" : ""}`}>
          {loading && !list && <div className="empty-small">Завантаження…</div>}
          {list && list.results.length === 0 && <div className="empty-small">Листів немає</div>}
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
              Оберіть лист
            </div>
          )}
          {open && (
            <>
              <div className="reader-actions">
                <button className="icon-btn mobile-only" onClick={() => setOpen(null)} aria-label="Назад">
                  <Icon name="chevronLeft" />
                </button>
                <button className="btn btn-sm" onClick={() => reply(false)}>
                  <Icon name="reply" size={14} /> Відповісти
                </button>
                <button className="btn btn-sm" onClick={() => reply(true)}>
                  Усім
                </button>
                <button className="btn btn-sm" onClick={forward}>
                  Переслати
                </button>
                <button
                  className="icon-btn"
                  title="Позначити"
                  onClick={() => action(() => patch(`/api/mail/messages/${open.uid}/`, { folder, flagged: true }), "Позначено")}
                >
                  <Icon name="star" size={16} />
                </button>
                <button
                  className="icon-btn"
                  title="Непрочитаний"
                  onClick={() => action(() => patch(`/api/mail/messages/${open.uid}/`, { folder, seen: false }), "Позначено непрочитаним")}
                >
                  <Icon name="eye" size={16} />
                </button>
                <select
                  className="select-sm"
                  value=""
                  onChange={(e) =>
                    e.target.value &&
                    action(() => patch(`/api/mail/messages/${open.uid}/`, { folder, move_to: e.target.value }), "Переміщено")
                  }
                >
                  <option value="">Перемістити…</option>
                  {folders
                    .filter((f) => f.name !== folder)
                    .map((f) => (
                      <option key={f.name} value={f.name}>
                        {FOLDER_LABELS[f.special || ""] || f.name}
                      </option>
                    ))}
                </select>
                <button
                  className="icon-btn danger"
                  title="Видалити"
                  onClick={() => action(() => del(`/api/mail/messages/${open.uid}/?folder=${encodeURIComponent(folder)}`), "Видалено")}
                >
                  <Icon name="trash" size={16} />
                </button>
              </div>
              <h3 className="reader-subject">{open.subject || "(без теми)"}</h3>
              <div className="reader-meta">
                <div>
                  <strong>{open.from}</strong>
                </div>
                <div className="small muted">
                  Кому: {open.to}
                  {open.cc && ` · Копія: ${open.cc}`}
                </div>
                <div className="small muted">{formatDate(open.date)}</div>
              </div>
              {open.attachments.length > 0 && (
                <div className="attachments">
                  {open.attachments.map((a) => (
                    <a
                      key={a.index}
                      className="attachment"
                      href={`/api/mail/messages/${open.uid}/attachments/${a.index}/?folder=${encodeURIComponent(folder)}`}
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
                      Віддалені зображення заблоковано для захисту приватності.{" "}
                      <button className="link-btn" onClick={() => setRemoteImages(true)}>
                        Показати
                      </button>
                    </div>
                  )}
                  <iframe
                    className="mail-frame"
                    title="Вміст листа"
                    sandbox="allow-popups allow-popups-to-escape-sandbox"
                    referrerPolicy="no-referrer"
                    src={`/api/mail/render/?folder=${encodeURIComponent(folder)}&uid=${open.uid}${remoteImages ? "&remote=1" : ""}`}
                  />
                </>
              ) : (
                <pre className="mail-text">{open.text}</pre>
              )}
            </>
          )}
        </section>
      </div>

      {compose && (
        <ComposeDialog
          init={compose}
          onClose={() => setCompose(null)}
          onSent={() => {
            setCompose(null);
            toast("Лист надіслано", "success");
            loadFolders();
          }}
        />
      )}
    </div>
  );
}

function ComposeDialog({ init, onClose, onSent }: { init: ComposeInit; onClose: () => void; onSent: () => void }) {
  const [form, setForm] = useState({
    to: init.to || "",
    cc: init.cc || "",
    bcc: "",
    subject: init.subject || "",
    body: init.body || "",
  });
  const [files, setFiles] = useState<File[]>([]);
  const [showCc, setShowCc] = useState(!!init.cc);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const totalSize = files.reduce((s, f) => s + f.size, 0);
  const emailRe = /[^@\s<>",]+@[^@\s<>",]+\.[^@\s<>",]+/;

  const send = async (e: FormEvent) => {
    e.preventDefault();
    setError("");
    if (!form.to.trim() && !form.cc.trim() && !form.bcc.trim()) return setError("Вкажіть отримувача.");
    for (const field of ["to", "cc", "bcc"] as const) {
      const parts = form[field].split(",").map((p) => p.trim()).filter(Boolean);
      const bad = parts.find((p) => !emailRe.test(p));
      if (bad) return setError(`Невірна адреса: ${bad}`);
    }
    if (totalSize > 15 * 1024 * 1024) return setError("Вкладення завеликі (максимум 15 МБ разом).");
    if (!form.subject.trim() && !window.confirm("Надіслати лист без теми?")) return;

    const data = new FormData();
    Object.entries(form).forEach(([k, v]) => data.append(k, v));
    if (init.in_reply_to) data.append("in_reply_to", init.in_reply_to);
    if (init.references) data.append("references", init.references);
    if (init.in_reply_to_uid) data.append("in_reply_to_uid", String(init.in_reply_to_uid));
    if (init.in_reply_to_folder) data.append("in_reply_to_folder", init.in_reply_to_folder);
    files.forEach((f) => data.append("attachments", f));
    setBusy(true);
    try {
      await api("/api/mail/send/", { method: "POST", body: data });
      onSent();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  return (
    <Modal title="Новий лист" onClose={onClose} wide>
      <form className="stack" onSubmit={send}>
        <div className="row gap">
          <input className="grow" placeholder="Кому (через кому)" value={form.to} onChange={set("to")} autoFocus={!init.to} />
          {!showCc && (
            <button type="button" className="link-btn" onClick={() => setShowCc(true)}>
              Копія
            </button>
          )}
        </div>
        {showCc && (
          <>
            <input placeholder="Копія" value={form.cc} onChange={set("cc")} />
            <input placeholder="Прихована копія" value={form.bcc} onChange={set("bcc")} />
          </>
        )}
        <input placeholder="Тема" value={form.subject} onChange={set("subject")} maxLength={300} />
        <textarea rows={12} value={form.body} onChange={set("body")} autoFocus={!!init.to} />
        <div className="row gap wrap">
          <label className="btn btn-sm">
            <Icon name="paperclip" size={14} /> Вкласти файли
            <input
              type="file"
              multiple
              hidden
              onChange={(e) => {
                if (e.target.files) setFiles([...files, ...Array.from(e.target.files as FileList)].slice(0, 20));
                e.target.value = "";
              }}
            />
          </label>
          {files.map((f, i) => (
            <span key={i} className="pill">
              {f.name} ({formatBytes(f.size)})
              <button type="button" className="pill-x" onClick={() => setFiles(files.filter((_, j) => j !== i))} aria-label="Прибрати">
                ×
              </button>
            </span>
          ))}
        </div>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button type="button" className="btn" onClick={onClose}>
            Скасувати
          </button>
          <button className="btn btn-primary" disabled={busy}>
            <Icon name="send" size={16} /> {busy ? "Надсилання…" : "Надіслати"}
          </button>
        </div>
      </form>
    </Modal>
  );
}
