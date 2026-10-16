import { FormEvent, ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { api, del, errorText, get, patch, post } from "../api/client";
import type { MailFolder, MailMessage, MailSummary } from "../api/types";
import ErrorBoundary from "../components/ErrorBoundary";
import Icon from "../components/Icon";
import MailAvatar, { parseSender, useSenderPhotos } from "../components/MailAvatar";
import Modal from "../components/Modal";
import ContactsDialog, { type ContactDraft } from "../components/ContactsDialog";
import PullToRefresh from "../components/PullToRefresh";
import RecipientInput from "../components/RecipientInput";
import RichEditor, { type RichEditorHandle } from "../components/RichEditor";
import SwipeRow from "../components/SwipeRow";
import { useToast } from "../components/Toast";
import { useIsMobile } from "../hooks/useIsMobile";
import { useT } from "../i18n";
import type { TFunc, TKey } from "../i18n";
import { setBadge } from "../lib/badges";
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
const POLL_MS = 15_000;
const MAILBOX_KEY = "bc_mailbox";
/** Віртуальна тека «З зірочкою» (позначені листи з усіх тек, крім «Кошика» і «Спаму»). */
const STARRED = "__starred__";

/** Рядок списку; folder — справжня тека листа (важливо для «З зірочкою»). */
type Summary = MailSummary & { folder?: string };
/** Відкритий лист; flagged — з нового API (старий не повертав). */
type Message = MailMessage & { flagged?: boolean };

interface ListResult {
  total: number;
  page: number;
  page_size: number;
  results: Summary[];
  truncated?: boolean;
}

type BulkAction = "delete" | "move" | "seen" | "unseen" | "flag" | "unflag" | "spam" | "notspam" | "archive";

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

interface DraftAttachment {
  index: number;
  filename: string;
  content_type?: string;
  size: number;
}

interface ComposeInit {
  to?: string;
  cc?: string;
  bcc?: string;
  subject?: string;
  /** Збережена чернетка: її UID у теці «Чернетки», повний HTML і вкладення на сервері. */
  draftUid?: number;
  html?: string;
  draftAttachments?: DraftAttachment[];
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
  scheduled?: { id: string; send_at: string };
}

interface ScheduledItem {
  id: string;
  send_at: string;
  status: "pending" | "sending" | "failed";
  to: string;
  subject: string;
  confidential: boolean;
}

const AUTOSAVE_MS = 2500;

/** Пресети «надіслати пізніше» (місцевий час), як у Gmail. */
function schedulePresets(): { key: TKey; date: Date }[] {
  const at = (days: number, hour: number) => {
    const d = new Date();
    d.setDate(d.getDate() + days);
    d.setHours(hour, 0, 0, 0);
    return d;
  };
  const daysToMonday = ((8 - new Date().getDay()) % 7) || 7;
  return [
    { key: "compose.schedTomorrowMorning", date: at(1, 8) },
    { key: "compose.schedTomorrowAfternoon", date: at(1, 13) },
    { key: "compose.schedMonday", date: at(daysToMonday, 8) },
  ];
}

/** Значення для <input type="datetime-local"> у місцевому часі. */
function toLocalInput(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

interface ConfidentialItem {
  id: string;
  subject: string;
  /** Список (новий API) або рядок «a@x, b@y» (старий) — показуємо обидва. */
  recipients: string[] | string | null;
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

/** Відкритий лист живе в хеші URL (#m=<uid>&f=<тека>[&v=__starred__]), щоб працювала кнопка «Назад» браузера.
 *  v — список, з якого відкрито лист (віртуальна тека), f — справжня тека листа. */
function parseMailHash(hash: string): { uid: number; folder: string; view: string } | null {
  const p = new URLSearchParams(hash.replace(/^#/, ""));
  const uid = Number(p.get("m"));
  const f = p.get("f");
  if (!Number.isSafeInteger(uid) || uid <= 0 || !f || f === STARRED) return null;
  return { uid, folder: f, view: p.get("v") === STARRED ? STARRED : "" };
}

const mailHash = (uid: number, folder: string, view?: string) =>
  `#${new URLSearchParams({ m: String(uid), f: folder, ...(view ? { v: view } : {}) }).toString()}`;

/** Ім'я для рядка списку: без лапок (будь-яких), інакше адреса. */
const displayName = (from: string) => {
  const p = parseSender(from);
  return p.name || p.email || senderName(from);
};

const recipientsText = (r: ConfidentialItem["recipients"]) => (Array.isArray(r) ? r.join(", ") : String(r ?? ""));

/** Кнопка «⋯» з випадним меню; закривається дотиком поза ним і після вибору пункту. */
function MoreMenu({ label, className, children }: { label: string; className?: string; children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent | TouchEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("touchstart", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("touchstart", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div className="dropdown" ref={ref}>
      <button
        type="button"
        className={`icon-btn ${open ? "active" : ""}`}
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
      >
        <Icon name="more" />
      </button>
      {open && (
        <div className={`dropdown-menu mail-menu ${className || ""}`} role="menu">
          {children(() => setOpen(false))}
        </div>
      )}
    </div>
  );
}

const isToday = (iso: string | null) => !!iso && new Date(iso).toDateString() === new Date().toDateString();

export default function MailPage() {
  const toast = useToast();
  const t = useT();
  const location = useLocation();
  const navigate = useNavigate();
  const mobile = useIsMobile();
  const [mailboxes, setMailboxes] = useState<MailboxInfo[]>([]);
  const [mailbox, setMailbox] = useState<MailboxInfo | null>(null);
  const [unavailable, setUnavailable] = useState("");
  const [folders, setFolders] = useState<MailFolder[]>([]);
  const [folder, setFolder] = useState(() => {
    const h = parseMailHash(window.location.hash);
    return h ? h.view || h.folder : "INBOX";
  });
  const [list, setList] = useState<ListResult | null>(null);
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState("");
  const [appliedQuery, setAppliedQuery] = useState("");
  const [open, setOpen] = useState<Message | null>(null);
  const [remoteImages, setRemoteImages] = useState(false);
  const [compose, setCompose] = useState<ComposeInit | null>(null);
  const [loading, setLoading] = useState(false);
  const [showSignatures, setShowSignatures] = useState(false);
  // Адресна книга: {} — просто відкрити; draft — одразу новий контакт (напр., відправник листа)
  const [contactsOpen, setContactsOpen] = useState<{ draft?: ContactDraft } | null>(null);
  // «Написати» з розділу «Контакти»: navigate("/mail", { state: { compose: { to } } })
  useEffect(() => {
    const state = location.state as { compose?: ComposeInit } | null;
    if (state?.compose) {
      setCompose(state.compose);
      navigate(location.pathname + location.search + location.hash, { replace: true, state: null });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [location.state]);
  const [showConfidential, setShowConfidential] = useState(false);
  const [showScheduled, setShowScheduled] = useState(false);
  const [passcodeInfo, setPasscodeInfo] = useState<SendResult["confidential"] | null>(null);
  // Вибрані листи: ключ «тека\nuid» (у «З зірочкою» листи з різних тек)
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkBusy, setBulkBusy] = useState(false);

  const pageRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  // Позиція прокрутки списку до відкриття листа (відновлюється після «Назад»)
  const savedScroll = useRef<{ list: number; page: number } | null>(null);
  // Лист відкрито нашим pushState — «Назад» робить history.back(), інакше replace
  const pushedOpen = useRef(false);
  const refreshFoldersOnOpen = useRef(false);
  const openReq = useRef(0);

  const target = parseMailHash(location.hash);
  const targetUid = target?.uid ?? 0;
  const targetFolder = target?.folder ?? "";
  const targetView = target?.view ?? "";
  const viewing = targetUid > 0;
  const baseUrl = location.pathname + location.search;

  // Усі запити веб-пошти явно вказують скриньку (у користувача їх може бути кілька)
  const box = mailbox?.id || "";
  const withBox = useCallback(
    (path: string) => (box ? `${path}${path.includes("?") ? "&" : "?"}mailbox=${encodeURIComponent(box)}` : path),
    [box],
  );

  const loadFolders = useCallback(() => get<MailFolder[]>(withBox("/api/mail/folders/")).then(setFolders), [withBox]);
  // silent — фонове оновлення (нові листи): без індикатора завантаження й без повідомлень про помилки
  const loadList = useCallback(
    async (silent = false) => {
      if (!silent) setLoading(true);
      try {
        setList(
          await get<ListResult>(
            withBox(`/api/mail/messages/?folder=${encodeURIComponent(folder)}&page=${page}&q=${encodeURIComponent(appliedQuery)}`),
          ),
        );
      } catch (e) {
        if (!silent) toast(errorText(e), "error");
      } finally {
        if (!silent) setLoading(false);
      }
    },
    [folder, page, appliedQuery, toast, withBox],
  );

  // Нові листи з'являються самі (як у Gmail): поки вкладка відкрита, кожні POLL_MS перевіряємо
  // лічильники тек (один легкий IMAP-запит STATUS) і, якщо в поточній теці щось змінилося,
  // тихо оновлюємо список. Прихована вкладка не опитує сервер; при поверненні — перевірка одразу.
  const foldersRef = useRef<MailFolder[]>([]);
  foldersRef.current = folders;
  const folderRef = useRef(folder);
  folderRef.current = folder;
  const loadListRef = useRef(loadList);
  loadListRef.current = loadList;
  useEffect(() => {
    if (!mailbox) return;
    let stopped = false;
    let busy = false;
    const check = async () => {
      if (busy || document.visibilityState !== "visible") return;
      busy = true;
      try {
        const fresh = await get<MailFolder[]>(withBox("/api/mail/folders/"));
        if (stopped) return;
        const sig = (list: MailFolder[]) => list.map((f) => `${f.name}:${f.messages}:${f.unseen}`).join("|");
        const before = foldersRef.current.find((f) => f.name === folderRef.current);
        const after = fresh.find((f) => f.name === folderRef.current);
        const changed =
          folderRef.current === STARRED
            ? foldersRef.current.length > 0 && sig(foldersRef.current) !== sig(fresh)
            : !!before && !!after && (before.messages !== after.messages || before.unseen !== after.unseen);
        setFolders(fresh);
        if (changed) await loadListRef.current(true);
      } catch {
        /* сервер тимчасово недоступний — спробуємо наступного разу */
      } finally {
        busy = false;
      }
    };
    const timer = window.setInterval(check, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState === "visible") check();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    return () => {
      stopped = true;
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
    };
  }, [mailbox, withBox]);

  // Кількість непрочитаних у «Вхідних» — у заголовку вкладки, як у Gmail
  const inboxUnseen = folders.find((f) => f.special === "\\Inbox" || f.name === "INBOX")?.unseen || 0;
  useEffect(() => {
    const base = document.title.replace(/^\(\d+\) /, "");
    document.title = inboxUnseen > 0 ? `(${inboxUnseen}) ${base}` : base;
    return () => {
      document.title = document.title.replace(/^\(\d+\) /, "");
    };
  }, [inboxUnseen]);
  // Значок на вкладці «Пошта» нижньої панелі (мобільна версія)
  useEffect(() => {
    if (folders.length) setBadge("mail", inboxUnseen);
  }, [inboxUnseen, folders.length]);

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
    loadFolders().catch((e) => setUnavailable(errorText(e)));
  }, [mailbox, loadFolders]);

  useEffect(() => {
    if (mailbox) loadList();
  }, [mailbox, loadList]);

  const scrollParent = (): HTMLElement | null =>
    (pageRef.current?.closest(".content") as HTMLElement | null) || (document.scrollingElement as HTMLElement | null);

  /** Повернення до списку (кнопка «Назад», Escape, після дій з листом). */
  const closeMessage = () => {
    if (!viewing) return;
    if (pushedOpen.current) {
      pushedOpen.current = false;
      window.history.back();
    } else {
      navigate(baseUrl, { replace: true });
    }
  };

  // Завантаження листа з хеша: клік у списку, кнопки браузера «Назад/Вперед», пряме посилання
  useEffect(() => {
    const req = ++openReq.current;
    if (!box) return;
    if (!targetUid) {
      pushedOpen.current = false;
      setOpen(null);
      return;
    }
    const listFolder = targetView || targetFolder;
    if (listFolder !== folder) {
      setFolder(listFolder);
      setPage(1);
      setQuery("");
      setAppliedQuery("");
    }
    setRemoteImages(false);
    get<Message>(withBox(`/api/mail/messages/${targetUid}/?folder=${encodeURIComponent(targetFolder)}`))
      .then((msg) => {
        if (req !== openReq.current) return;
        setOpen(msg);
        if (refreshFoldersOnOpen.current) {
          refreshFoldersOnOpen.current = false;
          loadFolders().catch(() => {});
        }
      })
      .catch((e) => {
        if (req !== openReq.current) return;
        toast(errorText(e), "error");
        pushedOpen.current = false;
        navigate(baseUrl, { replace: true });
      });
  }, [box, targetUid, targetFolder, targetView]);

  // Вибір скидається при зміні теки, сторінки, пошуку чи скриньки
  useEffect(() => {
    setSelected(new Set());
  }, [folder, page, appliedQuery, box]);
  // Після оновлення списку лишаємо вибраними лише листи, що ще є на сторінці
  useEffect(() => {
    setSelected((cur) => {
      if (!cur.size) return cur;
      const keys = new Set((list?.results || []).map((m) => `${m.folder || folderRef.current}\n${m.uid}`));
      const next = new Set(Array.from(cur).filter((k) => keys.has(k)));
      return next.size === cur.size ? cur : next;
    });
  }, [list]);

  // Відкрили лист — читаємо з початку; повернулися — відновлюємо прокрутку списку
  useLayoutEffect(() => {
    const parent = scrollParent();
    if (viewing) {
      if (parent) parent.scrollTop = 0;
      return;
    }
    const s = savedScroll.current;
    if (!s) return;
    savedScroll.current = null;
    if (listRef.current) listRef.current.scrollTop = s.list;
    if (parent) parent.scrollTop = s.page;
  }, [viewing]);

  // Escape — назад до списку (діалоги закриваються власним обробником Modal)
  const dialogOpen = !!compose || showSignatures || showScheduled || showConfidential || !!passcodeInfo;
  useEffect(() => {
    if (dialogOpen) return;
    // У списку Escape знімає вибір
    if (!viewing) {
      if (!selected.size) return;
      const onKeyList = (e: KeyboardEvent) => {
        if (e.key !== "Escape" || e.defaultPrevented) return;
        setSelected(new Set());
      };
      window.addEventListener("keydown", onKeyList);
      return () => window.removeEventListener("keydown", onKeyList);
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return;
      const el = e.target as HTMLElement | null;
      if (el && /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName)) return;
      closeMessage();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const switchMailbox = (id: string) => {
    const next = mailboxes.find((b) => b.id === id);
    if (!next) return;
    try {
      localStorage.setItem(MAILBOX_KEY, id);
    } catch {
      /* ignore */
    }
    // Синхронно прибираємо лист із URL, щоб він не підвантажився з нової скриньки
    pushedOpen.current = false;
    if (viewing) navigate(baseUrl, { replace: true });
    savedScroll.current = null;
    setFolder("INBOX");
    setPage(1);
    setList(null);
    setQuery("");
    setAppliedQuery("");
    setMailbox(next);
  };

  const selectFolder = (name: string) => {
    closeMessage();
    savedScroll.current = null;
    setFolder(name);
    setPage(1);
    setAppliedQuery("");
    setQuery("");
    if (listRef.current) listRef.current.scrollTop = 0;
  };

  const goPage = (p: number) => {
    setPage(p);
    if (listRef.current) listRef.current.scrollTop = 0;
    const parent = scrollParent();
    if (parent) parent.scrollTop = 0;
  };

  const specialOf = (name: string) => folders.find((f) => f.name === name)?.special || "";
  const isDraftsFolder = specialOf(folder) === "\\Drafts";
  const isSentName = (name: string) => specialOf(name) === "\\Sent" || name.toLowerCase().includes("sent");
  const itemFolder = (m: Summary) => m.folder || folder;
  const selKey = (m: Summary) => `${itemFolder(m)}\n${m.uid}`;
  const trashFolder = folders.find((f) => f.special === "\\Trash")?.name || "Trash";
  const junkFolder = folders.find((f) => f.special === "\\Junk")?.name || "Junk";
  const ownAddresses = new Set(mailboxes.map((b) => b.address.toLowerCase()));

  const openMessage = async (m: Summary) => {
    if (specialOf(itemFolder(m)) === "\\Drafts") {
      // Чернетка відкривається в редакторі, а не в режимі читання
      try {
        const d = await get<{
          uid: number;
          to: string;
          cc: string;
          bcc: string;
          subject: string;
          html: string;
          in_reply_to: string;
          references: string;
          attachments: DraftAttachment[];
        }>(withBox(`/api/mail/drafts/${m.uid}/`));
        setCompose({
          draftUid: d.uid,
          to: d.to,
          cc: d.cc,
          bcc: d.bcc,
          subject: d.subject,
          html: d.html,
          in_reply_to: d.in_reply_to || undefined,
          references: d.references || undefined,
          draftAttachments: d.attachments,
        });
      } catch (e) {
        toast(errorText(e), "error");
      }
      return;
    }
    savedScroll.current = { list: listRef.current?.scrollTop || 0, page: scrollParent()?.scrollTop || 0 };
    if (!m.seen) {
      setList((l) => l && { ...l, results: l.results.map((x) => (x.uid === m.uid ? { ...x, seen: true } : x)) });
      refreshFoldersOnOpen.current = true;
    }
    pushedOpen.current = true;
    navigate(baseUrl + mailHash(m.uid, itemFolder(m), folder === STARRED ? STARRED : undefined));
  };

  /** Зірочка в рядку списку: перемикає \Flagged, не відкриваючи лист. */
  const toggleStar = async (m: Summary) => {
    const flagged = !m.flagged;
    const key = selKey(m);
    const setFlag = (v: boolean) =>
      setList((l) => l && { ...l, results: l.results.map((x) => (selKey(x) === key ? { ...x, flagged: v } : x)) });
    setFlag(flagged);
    try {
      await patch(withBox(`/api/mail/messages/${m.uid}/`), { folder: itemFolder(m), flagged });
      // «З зірочкою»: знята зірочка прибирає лист зі списку
      if (folder === STARRED && !flagged) {
        setList((l) => l && { ...l, total: Math.max(0, l.total - 1), results: l.results.filter((x) => selKey(x) !== key) });
      }
    } catch (e) {
      setFlag(!flagged);
      toast(errorText(e), "error");
    }
  };

  const toggleSelect = (m: Summary) => {
    const key = selKey(m);
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const pageItems = list?.results || [];
  const selectedItems = pageItems.filter((m) => selected.has(selKey(m)));
  const selCount = selectedItems.length;
  const allSelected = pageItems.length > 0 && selCount === pageItems.length;
  const toggleAll = () => setSelected(selCount > 0 ? new Set() : new Set(pageItems.map(selKey)));

  /** Групова дія: окремий запит на кожну теку вибраних листів (у «З зірочкою» їх може бути кілька). */
  const runBulk = async (act: BulkAction, items: Summary[], target?: string) => {
    if (!items.length || bulkBusy) return;
    const permanent = act === "delete" && items.every((m) => itemFolder(m) === trashFolder);
    if (permanent && !window.confirm(t("mail2.confirmPurge", { n: items.length }))) return;
    const dest = act === "archive" ? archiveFolder : act === "spam" ? junkFolder : act === "notspam" ? "INBOX" : act === "move" ? target : undefined;
    const groups = new Map<string, number[]>();
    for (const m of items) {
      const f = itemFolder(m);
      if (dest && f === dest) continue; // уже там
      groups.set(f, [...(groups.get(f) || []), m.uid]);
    }
    setBulkBusy(true);
    let n = 0;
    try {
      for (const [f, uids] of Array.from(groups.entries())) {
        const r = await post<{ count: number }>(withBox("/api/mail/messages/bulk/"), { folder: f, uids, action: act, target });
        n += r.count || 0;
      }
      const msgKey: Record<BulkAction, TKey> = {
        delete: permanent ? "mail2.done.purged" : "mail2.done.deleted",
        move: "mail2.done.moved",
        archive: "mail2.done.archived",
        spam: "mail2.done.spam",
        notspam: "mail2.done.notspam",
        seen: "mail2.done.seen",
        unseen: "mail2.done.unseen",
        flag: "mail2.done.flag",
        unflag: "mail2.done.unflag",
      };
      toast(t(msgKey[act], { n }), "success");
      return true;
    } catch (e) {
      toast(errorText(e), "error");
      return false;
    } finally {
      setBulkBusy(false);
      setSelected(new Set());
      loadList(true);
      loadFolders().catch(() => {});
    }
  };

  /** Блокування відправників (точна адреса): нові листи — у «Спам», наявні у «Вхідних» — одразу туди. */
  const blockSenders = async (froms: string[]): Promise<boolean> => {
    const addresses = Array.from(new Set(froms.map((f) => parseSender(f).email).filter((a) => a && !ownAddresses.has(a))));
    if (!addresses.length) {
      toast(t("mail2.nothingToBlock"), "info");
      return false;
    }
    const shownList = addresses.slice(0, 5).join(", ") + (addresses.length > 5 ? ` (+${addresses.length - 5})` : "");
    if (!window.confirm(t("mail2.confirmBlock", { n: addresses.length, addresses: shownList }))) return false;
    try {
      const r = await post<{ moved: number }>(withBox("/api/mail/blocked/"), { addresses });
      toast(t("mail2.blockedToast", { address: shownList, n: r.moved || 0 }), "success");
      setSelected(new Set());
      loadList(true);
      loadFolders().catch(() => {});
      return true;
    } catch (e) {
      toast(errorText(e), "error");
      return false;
    }
  };

  const action = async (fn: () => Promise<unknown>, msg: string) => {
    try {
      await fn();
      toast(msg, "success");
      closeMessage();
      loadList();
      loadFolders();
    } catch (e) {
      toast(errorText(e), "error");
    }
  };

  // Свайп у списку (телефон): ліворуч — у кошик (як кнопка «Видалити»), праворуч — в архів (якщо є тека \Archive)
  const archiveFolder = folders.find((f) => f.special === "\\Archive")?.name;
  const swipeAction = async (m: Summary, kind: "delete" | "archive") => {
    const key = selKey(m);
    const from = itemFolder(m);
    setList((l) => l && { ...l, total: Math.max(0, l.total - 1), results: l.results.filter((x) => selKey(x) !== key) });
    try {
      if (kind === "delete") await del(withBox(`/api/mail/messages/${m.uid}/?folder=${encodeURIComponent(from)}`));
      else await patch(withBox(`/api/mail/messages/${m.uid}/`), { folder: from, move_to: archiveFolder });
      toast(kind === "delete" ? t("mail.deleted") : t("mobile.archived"), "success");
      loadFolders().catch(() => {});
    } catch (e) {
      toast(errorText(e), "error");
      loadList(true);
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
      in_reply_to_folder: targetFolder || folder,
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

  // Фото відправників (локальні користувачі): один пакетний запит на сторінку списку
  const photoOf = useSenderPhotos([
    ...(list?.results || []).map((m) => parseSender(isSentName(itemFolder(m)) ? m.to.split(",")[0] || "" : m.from).email),
    ...(open ? [parseSender(open.from).email] : []),
  ]);

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
  // Дії в режимі читання — над справжньою текою листа (не над «З зірочкою»)
  const readerFolder = targetFolder || folder;
  const folderParam = encodeURIComponent(readerFolder);
  const shown = open && open.uid === targetUid ? open : null;
  const readerSpecial = specialOf(readerFolder);
  const listSpecial = folder === STARRED ? "" : specialOf(folder);
  const shownSender = shown ? parseSender(shown.from).email : "";
  const canBlockShown = !!shownSender && !ownAddresses.has(shownSender) && readerSpecial !== "\\Sent" && readerSpecial !== "\\Drafts";

  /** Дія над відкритим листом через груповий ендпоінт (один UID). */
  const readerBulk = (act: BulkAction, msg: string, target?: string) =>
    shown &&
    action(
      () => post(withBox("/api/mail/messages/bulk/"), { folder: readerFolder, uids: [shown.uid], action: act, target }),
      msg,
    );

  const toggleReaderStar = async () => {
    if (!shown) return;
    const flagged = !shown.flagged;
    setOpen({ ...shown, flagged });
    try {
      await patch(withBox(`/api/mail/messages/${shown.uid}/`), { folder: readerFolder, flagged });
      loadList(true);
    } catch (e) {
      setOpen({ ...shown, flagged: !flagged });
      toast(errorText(e), "error");
    }
  };

  const moveOptions = (exclude: string) =>
    folders
      .filter((f) => f.name !== exclude)
      .map((f) => (
        <option key={f.name} value={f.name}>
          {folderLabel(t, f)}
        </option>
      ));

  // ── Панель групових дій (замість звичайної панелі списку, коли щось вибрано) ──
  const inTrash = listSpecial === "\\Trash";
  const inJunk = listSpecial === "\\Junk";
  const anyUnread = selectedItems.some((m) => !m.seen);
  const anyUnflagged = selectedItems.some((m) => !m.flagged);
  const canArchive = !!archiveFolder && selectedItems.some((m) => itemFolder(m) !== archiveFolder);
  const selectAllBox = (
    <label className="mail-check mail-check-all" title={allSelected ? t("mail2.selectNone") : t("mail2.selectAll")}>
      <input
        type="checkbox"
        checked={allSelected}
        disabled={!pageItems.length}
        aria-label={allSelected || selCount > 0 ? t("mail2.selectNone") : t("mail2.selectAll")}
        aria-checked={selCount > 0 && !allSelected ? "mixed" : allSelected}
        ref={(el: HTMLInputElement | null) => {
          if (el) el.indeterminate = selCount > 0 && !allSelected;
        }}
        onChange={toggleAll}
      />
    </label>
  );
  const bulkBar = (
    <div className="mail-toolbar mail-bulkbar" role="toolbar" aria-label={t("mail2.bulkActions")}>
      {selectAllBox}
      <span className="mail-selcount nowrap" aria-live="polite">
        {t("mail2.selected", { n: selCount })}
      </span>
      <span className="spacer" />
      {canArchive && (
        <button className="icon-btn" disabled={bulkBusy} onClick={() => runBulk("archive", selectedItems)} aria-label={t("mobile.archive")} title={t("mobile.archive")}>
          <Icon name="archive" />
        </button>
      )}
      {!mobile &&
        (inJunk ? (
          <button className="icon-btn" disabled={bulkBusy} onClick={() => runBulk("notspam", selectedItems)} aria-label={t("mail2.notSpam")} title={t("mail2.notSpam")}>
            <Icon name="inbox" />
          </button>
        ) : (
          <button className="icon-btn" disabled={bulkBusy} onClick={() => runBulk("spam", selectedItems)} aria-label={t("mail2.reportSpam")} title={t("mail2.reportSpam")}>
            <Icon name="alert" />
          </button>
        ))}
      <button
        className="icon-btn danger"
        disabled={bulkBusy}
        onClick={() => runBulk("delete", selectedItems)}
        aria-label={inTrash ? t("mail2.deleteForever") : t("common.delete")}
        title={inTrash ? t("mail2.deleteForever") : t("common.delete")}
      >
        <Icon name="trash" />
      </button>
      <button
        className="icon-btn"
        disabled={bulkBusy}
        onClick={() => runBulk(anyUnread ? "seen" : "unseen", selectedItems)}
        aria-label={anyUnread ? t("mail2.markRead") : t("mail2.markUnread")}
        title={anyUnread ? t("mail2.markRead") : t("mail2.markUnread")}
      >
        <Icon name={anyUnread ? "eye" : "mail"} />
      </button>
      <MoreMenu label={t("mobile.moreActions")}>
        {(close) => (
          <>
            <button role="menuitem" className="menu-item" onClick={() => (close(), runBulk(anyUnread ? "unseen" : "seen", selectedItems))}>
              <Icon name={anyUnread ? "mail" : "eye"} size={18} /> {anyUnread ? t("mail2.markUnread") : t("mail2.markRead")}
            </button>
            <button role="menuitem" className="menu-item" onClick={() => (close(), runBulk(anyUnflagged ? "flag" : "unflag", selectedItems))}>
              <Icon name="star" size={18} /> {anyUnflagged ? t("mailui.star") : t("mailui.unstar")}
            </button>
            {mobile &&
              (inJunk ? (
                <button role="menuitem" className="menu-item" onClick={() => (close(), runBulk("notspam", selectedItems))}>
                  <Icon name="inbox" size={18} /> {t("mail2.notSpam")}
                </button>
              ) : (
                <button role="menuitem" className="menu-item" onClick={() => (close(), runBulk("spam", selectedItems))}>
                  <Icon name="alert" size={18} /> {t("mail2.reportSpam")}
                </button>
              ))}
            <label className="menu-item menu-select">
              <Icon name="move" size={18} />
              <select
                aria-label={t("mail.moveTo")}
                value=""
                onChange={(e) => {
                  const dest = e.target.value;
                  close();
                  if (dest) runBulk("move", selectedItems, dest);
                }}
              >
                <option value="">{t("mail.moveTo")}</option>
                {moveOptions(folder === STARRED ? "" : folder)}
              </select>
            </label>
            {!inJunk && (
              <>
                <div className="menu-sep" />
                <button role="menuitem" className="menu-item danger" onClick={() => (close(), blockSenders(selectedItems.map((m) => m.from)))}>
                  <Icon name="shield" size={18} /> {t("mail2.blockSenders")}
                </button>
              </>
            )}
          </>
        )}
      </MoreMenu>
      <button className="icon-btn" onClick={() => setSelected(new Set())} aria-label={t("mail2.clearSelection")} title={t("mail2.clearSelection")}>
        <Icon name="x" />
      </button>
    </div>
  );

  const listView = (
    <>
      {selCount > 0 ? (
        bulkBar
      ) : (
        <div className="mail-toolbar">
          {selectAllBox}
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
          {loading && list && <span className="small muted">{t("common.loading")}</span>}
          <span className="spacer" />
          {list && list.total > 0 && (
            <span className="small muted nowrap">
              {t("mailui.range", {
                from: (list.page - 1) * list.page_size + 1,
                to: Math.min(list.page * list.page_size, list.total),
                total: list.total,
              })}
            </span>
          )}
          <button className="icon-btn" disabled={page <= 1} onClick={() => goPage(page - 1)} aria-label={t("mailui.prevPage")} title={t("mailui.prevPage")}>
            <Icon name="chevronLeft" />
          </button>
          <button
            className="icon-btn"
            disabled={page >= totalPages}
            onClick={() => goPage(page + 1)}
            aria-label={t("mailui.nextPage")}
            title={t("mailui.nextPage")}
          >
            <Icon name="chevronRight" />
          </button>
        </div>
      )}
      <div className="mail-list" ref={listRef}>
        <PullToRefresh enabled={mobile && selCount === 0} onRefresh={() => Promise.all([loadList(), loadFolders().catch(() => {})])}>
        {folder === STARRED && list?.truncated && <div className="banner banner-info small mail-list-note">{t("mail2.starredTruncated")}</div>}
        {loading && !list && <div className="empty-small">{t("common.loading")}</div>}
        {list && list.results.length === 0 && (
          <div className="empty">
            <Icon name={folder === STARRED ? "star" : FOLDER_ICONS[listSpecial] || "mail"} size={32} />
            {folder === STARRED ? t("mail2.starredEmpty") : t("mail.empty")}
          </div>
        )}
        {list?.results.map((m) => {
          const f = itemFolder(m);
          const sent = isSentName(f);
          const who = sent ? m.to.split(",")[0] || m.to : m.from;
          const isSel = selected.has(selKey(m));
          const name = sent ? `${t("mail.to")}: ${displayName(m.to)}` : displayName(m.from);
          const tagFolder = folder === STARRED && specialOf(f) !== "\\Inbox" ? folders.find((x) => x.name === f) : undefined;
          return (
            <SwipeRow
              key={selKey(m)}
              className={`mail-row ${m.seen ? "" : "unread"} ${isSel ? "selected" : ""}`}
              left={mobile && selCount === 0 ? { icon: "trash", label: t("common.delete"), tone: "swipe-danger", run: () => swipeAction(m, "delete") } : undefined}
              right={
                mobile && selCount === 0 && archiveFolder && archiveFolder !== f
                  ? { icon: "archive", label: t("mobile.archive"), tone: "swipe-archive", run: () => swipeAction(m, "archive") }
                  : undefined
              }
            >
              {!mobile && (
                <label className="mail-check">
                  <input type="checkbox" checked={isSel} onChange={() => toggleSelect(m)} aria-label={t("mail2.selectMessage", { subject: m.subject || t("mail.noSubject") })} />
                </label>
              )}
              <button
                className={`mail-star ${m.flagged ? "on" : ""}`}
                aria-pressed={m.flagged}
                aria-label={m.flagged ? t("mailui.unstar") : t("mailui.star")}
                title={m.flagged ? t("mailui.unstar") : t("mailui.star")}
                onClick={() => toggleStar(m)}
              >
                <Icon name="star" size={16} />
              </button>
              <MailAvatar
                from={who}
                photo={photoOf(parseSender(who).email)}
                selected={isSel}
                onToggle={() => toggleSelect(m)}
                label={isSel ? t("mail2.deselectMessage", { name }) : t("mail2.selectMessage", { subject: name })}
                size={mobile ? 40 : 32}
              />
              <button className="mail-row-open" onClick={() => (mobile && selCount > 0 ? toggleSelect(m) : openMessage(m))}>
                <span className="mail-from truncate">{name}</span>
                <span className="mail-subject truncate">
                  {tagFolder && <span className="mail-folder-tag">{folderLabel(t, tagFolder)}</span>}
                  {m.subject || t("mail.noSubject")}
                </span>
                {m.has_attachments && (
                  <span className="mail-clip" title={t("mailui.hasAttachments")}>
                    <Icon name="paperclip" size={14} />
                  </span>
                )}
                <span className="mail-date" title={formatDate(m.date)}>
                  {formatDate(m.date, isToday(m.date))}
                </span>
              </button>
            </SwipeRow>
          );
        })}
        </PullToRefresh>
      </div>
    </>
  );

  const replyBtns = mobile ? (
    <>
      <button className="icon-btn" onClick={() => reply(false)} aria-label={t("mail.reply")} title={t("mail.reply")}>
        <Icon name="reply" />
      </button>
      <button className="icon-btn" onClick={() => reply(true)} aria-label={t("mail2.replyAll")} title={t("mail2.replyAll")}>
        <Icon name="users" />
      </button>
      <button className="icon-btn icon-flip" onClick={forward} aria-label={t("mail.forward")} title={t("mail.forward")}>
        <Icon name="reply" />
      </button>
    </>
  ) : (
    <>
      <button className="btn btn-sm" onClick={() => reply(false)}>
        <Icon name="reply" size={14} /> {t("mail.reply")}
      </button>
      <button className="btn btn-sm" onClick={() => reply(true)} title={t("mail2.replyAll")}>
        <Icon name="users" size={14} /> {t("mail.replyAll")}
      </button>
      <button className="btn btn-sm" onClick={forward}>
        <span className="icon-flip">
          <Icon name="reply" size={14} />
        </span>{" "}
        {t("mail.forward")}
      </button>
    </>
  );

  const readerView = (
    <>
      <div className="reader-toolbar" role="toolbar" aria-label={t("mail2.messageActions")}>
        <button className="icon-btn" onClick={closeMessage} aria-label={t("mailui.backToList")} title={t("mailui.backToList")}>
          <Icon name="chevronLeft" />
        </button>
        {shown && (
          <>
            {!mobile && <span className="reader-sep" />}
            {!mobile && replyBtns}
            <span className="spacer" />
            {mobile && replyBtns}
            <button
              className={`icon-btn mail-star-btn ${shown.flagged ? "on" : ""}`}
              aria-pressed={!!shown.flagged}
              title={shown.flagged ? t("mailui.unstar") : t("mailui.star")}
              aria-label={shown.flagged ? t("mailui.unstar") : t("mailui.star")}
              onClick={toggleReaderStar}
            >
              <Icon name="star" size={mobile ? 20 : 16} />
            </button>
            {!mobile && (
              <>
                <button
                  className="icon-btn"
                  title={t("mail2.markUnread")}
                  aria-label={t("mail2.markUnread")}
                  onClick={() => readerBulk("unseen", t("mail.markedUnread"))}
                >
                  <Icon name="mail" size={16} />
                </button>
                {archiveFolder && archiveFolder !== readerFolder && (
                  <button className="icon-btn" title={t("mobile.archive")} aria-label={t("mobile.archive")} onClick={() => readerBulk("archive", t("mobile.archived"))}>
                    <Icon name="archive" size={16} />
                  </button>
                )}
                {readerSpecial === "\\Junk" ? (
                  <button className="icon-btn" title={t("mail2.notSpam")} aria-label={t("mail2.notSpam")} onClick={() => readerBulk("notspam", t("mail2.done.notspam", { n: 1 }))}>
                    <Icon name="inbox" size={16} />
                  </button>
                ) : (
                  <button className="icon-btn" title={t("mail2.reportSpam")} aria-label={t("mail2.reportSpam")} onClick={() => readerBulk("spam", t("mail2.done.spam", { n: 1 }))}>
                    <Icon name="alert" size={16} />
                  </button>
                )}
                <button
                  className="icon-btn danger"
                  title={t("common.delete")}
                  aria-label={t("common.delete")}
                  onClick={() => action(() => del(withBox(`/api/mail/messages/${shown.uid}/?folder=${folderParam}`)), t("mail.deleted"))}
                >
                  <Icon name="trash" size={16} />
                </button>
              </>
            )}
            <MoreMenu label={t("mobile.moreActions")}>
              {(close) => (
                <>
                  {mobile && (
                    <>
                      <button role="menuitem" className="menu-item" onClick={() => (close(), readerBulk("unseen", t("mail.markedUnread")))}>
                        <Icon name="mail" size={18} /> {t("mail2.markUnread")}
                      </button>
                      {archiveFolder && archiveFolder !== readerFolder && (
                        <button role="menuitem" className="menu-item" onClick={() => (close(), readerBulk("archive", t("mobile.archived")))}>
                          <Icon name="archive" size={18} /> {t("mobile.archive")}
                        </button>
                      )}
                      {readerSpecial === "\\Junk" ? (
                        <button role="menuitem" className="menu-item" onClick={() => (close(), readerBulk("notspam", t("mail2.done.notspam", { n: 1 })))}>
                          <Icon name="inbox" size={18} /> {t("mail2.notSpam")}
                        </button>
                      ) : (
                        <button role="menuitem" className="menu-item" onClick={() => (close(), readerBulk("spam", t("mail2.done.spam", { n: 1 })))}>
                          <Icon name="alert" size={18} /> {t("mail2.reportSpam")}
                        </button>
                      )}
                    </>
                  )}
                  <label className="menu-item menu-select">
                    <Icon name="move" size={18} />
                    <select
                      aria-label={t("mail.moveTo")}
                      value=""
                      onChange={(e) => {
                        const dest = e.target.value;
                        close();
                        if (dest) action(() => patch(withBox(`/api/mail/messages/${shown.uid}/`), { folder: readerFolder, move_to: dest }), t("mail.moved"));
                      }}
                    >
                      <option value="">{t("mail.moveTo")}</option>
                      {moveOptions(readerFolder)}
                    </select>
                  </label>
                  {canBlockShown && (
                    <button
                      role="menuitem"
                      className="menu-item"
                      onClick={() => {
                        close();
                        blockSenders([shown.from]).then((ok) => ok && closeMessage());
                      }}
                    >
                      <Icon name="shield" size={18} /> {t("mail2.blockSender", { address: shownSender })}
                    </button>
                  )}
                  {mobile && (
                    <>
                      <div className="menu-sep" />
                      <button
                        role="menuitem"
                        className="menu-item danger"
                        onClick={() => (close(), action(() => del(withBox(`/api/mail/messages/${shown.uid}/?folder=${folderParam}`)), t("mail.deleted")))}
                      >
                        <Icon name="trash" size={18} /> {t("common.delete")}
                      </button>
                    </>
                  )}
                </>
              )}
            </MoreMenu>
          </>
        )}
      </div>
      <div className="reader-body">
        {!shown && <div className="empty-small">{t("common.loading")}</div>}
        {shown && (
          <>
            <h3 className="reader-subject">{shown.subject || t("mail.noSubject")}</h3>
            <div className="reader-meta reader-meta-av">
              <MailAvatar from={shown.from} photo={photoOf(shownSender)} size={40} />
              <div className="reader-meta-text">
                <div className="row-between reader-from">
                  <span className="reader-addr">
                    <strong>{shown.from}</strong>{" "}
                    {shownSender && !ownAddresses.has(shownSender) && (
                      <button
                        type="button"
                        className="link-btn small"
                        onClick={() => {
                          const p = parseSender(shown.from);
                          const [first, ...rest] = p.name.split(/\s+/).filter(Boolean);
                          setContactsOpen({ draft: { first_name: first || "", last_name: rest.join(" "), emails: [{ address: p.email, label: "" }] } });
                        }}
                      >
                        {t("contacts.addSender")}
                      </button>
                    )}
                  </span>
                  <span className="small muted nowrap">{formatDate(shown.date)}</span>
                </div>
                <div className="small muted reader-addr">
                  {t("mail.to")}: {shown.to}
                  {shown.cc && ` · ${t("mail.cc")}: ${shown.cc}`}
                </div>
              </div>
            </div>
            {shown.attachments.length > 0 && (
              <div className="attachments">
                {shown.attachments.map((a) => (
                  <a
                    key={a.index}
                    className="attachment"
                    href={withBox(`/api/mail/messages/${shown.uid}/attachments/${a.index}/?folder=${folderParam}`)}
                  >
                    <Icon name="paperclip" size={14} /> {a.filename} <span className="muted small">{formatBytes(a.size)}</span>
                  </a>
                ))}
              </div>
            )}
            {shown.has_html ? (
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
                  src={withBox(`/api/mail/render/?folder=${folderParam}&uid=${shown.uid}${remoteImages ? "&remote=1" : ""}`)}
                />
              </>
            ) : (
              <pre className="mail-text">{shown.text}</pre>
            )}
          </>
        )}
      </div>
    </>
  );

  const dialogFallback = (onClose: () => void) => (error: Error) => (
    <Modal title={t("mail2.dialogErrorTitle")} onClose={onClose}>
      <div className="stack">
        <p>{t("mail2.dialogError")}</p>
        <p className="small muted">{error.message}</p>
        <div className="modal-actions">
          <button className="btn btn-primary" onClick={onClose}>
            {t("common.close")}
          </button>
        </div>
      </div>
    </Modal>
  );

  return (
    <div className="page mail-page" ref={pageRef}>
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
              closeMessage();
              savedScroll.current = null;
              setPage(1);
              setAppliedQuery(query.trim());
            }}
          >
            <Icon name="search" size={16} />
            <input placeholder={t("mail.searchFolder")} value={query} onChange={(e) => setQuery(e.target.value)} />
          </form>
          <button className="icon-btn" onClick={() => setContactsOpen({})} aria-label={t("contacts.open")} title={t("contacts.open")}>
            <Icon name="users" />
          </button>
          <button className="icon-btn" onClick={() => setShowSignatures(true)} aria-label={t("mail.signatures")} title={t("mail.signatures")}>
            <Icon name="signature" />
          </button>
          <button className="icon-btn" onClick={() => setShowScheduled(true)} aria-label={t("mail.scheduled")} title={t("mail.scheduled")}>
            <Icon name="clock" />
          </button>
          <button className="icon-btn" onClick={() => setShowConfidential(true)} aria-label={t("mail2.confTooltip")} title={t("mail2.confTooltip")}>
            <Icon name="lock" />
          </button>
        </div>
      </div>

      <div className="mail-layout">
        <aside className="mail-side">
          <button className="btn btn-primary mail-compose" onClick={() => setCompose({})} disabled={!mailbox}>
            <Icon name="edit" size={16} /> {t("mail.compose")}
          </button>
          <nav className="mail-folders" aria-label={t("mailui.folders")}>
            {folders.map((f) => {
              const btn = (
                <button
                  key={f.name}
                  className={`folder-btn ${f.name === folder ? "active" : ""}`}
                  aria-current={f.name === folder ? "true" : undefined}
                  onClick={() => selectFolder(f.name)}
                >
                  <Icon name={FOLDER_ICONS[f.special || ""] || "folder"} size={16} />
                  <span className="truncate">{folderLabel(t, f)}</span>
                  {f.unseen > 0 && <span className="count">{f.unseen}</span>}
                </button>
              );
              if (f.special !== "\\Inbox") return btn;
              // «З зірочкою» — одразу після «Вхідних», як у Gmail
              return [
                btn,
                <button
                  key={STARRED}
                  className={`folder-btn folder-starred ${folder === STARRED ? "active" : ""}`}
                  aria-current={folder === STARRED ? "true" : undefined}
                  onClick={() => selectFolder(STARRED)}
                >
                  <Icon name="star" size={16} />
                  <span className="truncate">{t("mail2.starred")}</span>
                </button>,
              ];
            })}
          </nav>
          <Link to="/settings?tab=mail" className="small muted mail-settings-link">
            {t("mail.imapSettings")}
          </Link>
        </aside>

        <section className={`card mail-main ${viewing ? "is-reading" : ""}`}>
          <ErrorBoundary resetKey={viewing ? `m${targetUid}` : `l${folder}`}>{viewing ? readerView : listView}</ErrorBoundary>
        </section>
      </div>

      {compose && mailbox && (
        <ComposeDialog
          init={compose}
          mailbox={mailbox}
          onClose={(savedDraft) => {
            setCompose(null);
            if (savedDraft) toast(t("compose.draftSaved"), "info");
            loadFolders();
            if (isDraftsFolder) loadList();
          }}
          onSent={(result) => {
            setCompose(null);
            toast(
              result.scheduled ? t("compose.scheduledToast", { date: formatDate(result.scheduled.send_at) }) : t("mail.sent"),
              "success",
            );
            if (result.confidential?.passcode) setPasscodeInfo(result.confidential);
            loadFolders();
            if (isDraftsFolder) loadList();
          }}
        />
      )}
      {showScheduled && mailbox && (
        <ErrorBoundary fallback={dialogFallback(() => setShowScheduled(false))}>
          <ScheduledDialog withBox={withBox} onClose={() => setShowScheduled(false)} />
        </ErrorBoundary>
      )}
      {contactsOpen && (
        <ErrorBoundary fallback={dialogFallback(() => setContactsOpen(null))}>
          <ContactsDialog
            draft={contactsOpen.draft}
            onClose={() => setContactsOpen(null)}
            onWrite={(to) => {
              setContactsOpen(null);
              setCompose({ to });
            }}
          />
        </ErrorBoundary>
      )}
      {showSignatures && (
        <ErrorBoundary fallback={dialogFallback(() => setShowSignatures(false))}>
          <SignaturesDialog onClose={() => setShowSignatures(false)} />
        </ErrorBoundary>
      )}
      {showConfidential && (
        <ErrorBoundary fallback={dialogFallback(() => setShowConfidential(false))}>
          <ConfidentialDialog onClose={() => setShowConfidential(false)} />
        </ErrorBoundary>
      )}
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
  /** savedDraft — чернетку збережено на сервері (показати підказку). */
  onClose: (savedDraft: boolean) => void;
  onSent: (result: SendResult) => void;
}) {
  const t = useT();
  const mobile = useIsMobile();
  const editor = useRef<RichEditorHandle>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const cameraRef = useRef<HTMLInputElement>(null);
  const [form, setForm] = useState({
    to: init.to || "",
    cc: init.cc || "",
    bcc: init.bcc || "",
    subject: init.subject || "",
  });
  const [files, setFiles] = useState<File[]>([]);
  // Вкладення, що вже лежать у збереженій чернетці на сервері
  const [kept, setKept] = useState<DraftAttachment[]>(init.draftAttachments || []);
  const [showCc, setShowCc] = useState(!!(init.cc || init.bcc));
  const [signatures, setSignatures] = useState<Signature[]>([]);
  const [signatureId, setSignatureId] = useState("");
  const [confidential, setConfidential] = useState(false);
  const [confDays, setConfDays] = useState(7);
  const [confPasscode, setConfPasscode] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [customWhen, setCustomWhen] = useState(() => toLocalInput(schedulePresets()[0].date));
  const [saveState, setSaveState] = useState<"" | "saving" | "saved" | "error">(init.draftUid ? "saved" : "");
  const [savedAt, setSavedAt] = useState<Date | null>(null);

  // Автозбереження: актуальні значення — у ref, щоб таймер бачив останній стан
  const draftUid = useRef<number | undefined>(init.draftUid);
  const latest = useRef({ form, files, kept });
  latest.current = { form, files, kept };
  const dirty = useRef(false);
  const timer = useRef<number | undefined>(undefined);
  const saving = useRef<Promise<void> | null>(null);
  const closed = useRef(false);

  // Підпис за замовчуванням вставляється одразу (як у Gmail); у збереженій чернетці він уже є
  useEffect(() => {
    get<Signature[]>("/api/mail/signatures/")
      .then((sigs) => {
        setSignatures(sigs);
        const def = sigs.find((s) => s.is_default);
        if (def && !init.draftUid) {
          setSignatureId(def.id);
          editor.current?.setSignature(def.html);
          dirty.current = false;
        }
      })
      .catch(() => {});
    return () => window.clearTimeout(timer.current);
  }, []);

  // Меню «⋯» (мобільна версія) закривається дотиком поза ним
  useEffect(() => {
    if (!menuOpen) return;
    const onDown = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [menuOpen]);

  const replyFields = (data: FormData) => {
    if (init.in_reply_to) data.append("in_reply_to", init.in_reply_to);
    if (init.references) data.append("references", init.references);
  };

  const saveDraft = async (): Promise<void> => {
    window.clearTimeout(timer.current);
    if (saving.current) {
      await saving.current;
      if (!dirty.current) return;
    }
    const ed = editor.current;
    if (!ed || !dirty.current) return;
    const { form: f, files: sentFiles, kept: keptNow } = latest.current;
    const empty = !f.to.trim() && !f.cc.trim() && !f.bcc.trim() && !f.subject.trim() && ed.isEmpty() && !sentFiles.length && !keptNow.length;
    if (empty && !draftUid.current) return;
    dirty.current = false;
    const data = new FormData();
    Object.entries(f).forEach(([k, v]) => data.append(k, v));
    data.append("mailbox", mailbox.id);
    data.append("html", ed.html());
    data.append("body", ed.text());
    if (draftUid.current) data.append("draft_uid", String(draftUid.current));
    keptNow.forEach((a) => data.append("keep_attachments", String(a.index)));
    sentFiles.forEach((file) => data.append("attachments", file));
    replyFields(data);
    setSaveState("saving");
    const run = (async () => {
      try {
        const r = await api<{ uid: number | null; attachments: DraftAttachment[] }>("/api/mail/drafts/", { method: "POST", body: data });
        draftUid.current = r.uid ?? undefined;
        // Надіслані файли тепер у чернетці на сервері; додані під час збереження лишаються локальними
        setKept(r.attachments);
        setFiles((cur) => cur.filter((x) => !sentFiles.includes(x)));
        setSaveState("saved");
        setSavedAt(new Date());
      } catch {
        dirty.current = true;
        setSaveState("error");
      }
    })();
    saving.current = run;
    await run;
    saving.current = null;
  };

  const touch = () => {
    dirty.current = true;
    window.clearTimeout(timer.current);
    if (!closed.current) timer.current = window.setTimeout(() => saveDraft(), AUTOSAVE_MS);
  };

  const close = async () => {
    closed.current = true;
    window.clearTimeout(timer.current);
    if (dirty.current) await saveDraft();
    onClose(!!draftUid.current);
  };

  const discard = async () => {
    if (!window.confirm(t("compose.confirmDiscard"))) return;
    closed.current = true;
    window.clearTimeout(timer.current);
    if (saving.current) await saving.current;
    if (draftUid.current) {
      try {
        await del(`/api/mail/drafts/${draftUid.current}/?mailbox=${encodeURIComponent(mailbox.id)}`);
      } catch {
        /* чернетку могли вже видалити */
      }
    }
    onClose(false);
  };

  const attachSize = files.reduce((s, f) => s + f.size, 0) + kept.reduce((s, a) => s + a.size, 0);
  const emailRe = /[^@\s<>",]+@[^@\s<>",]+\.[^@\s<>",]+/;

  const submit = async (sendAt?: Date) => {
    setError("");
    const ed = editor.current;
    if (!ed) return;
    if (!form.to.trim() && !form.cc.trim() && !form.bcc.trim()) return setError(t("compose.noRecipient"));
    for (const field of ["to", "cc", "bcc"] as const) {
      const parts = form[field].split(",").map((p) => p.trim()).filter(Boolean);
      const bad = parts.find((p) => !emailRe.test(p));
      if (bad) return setError(t("compose.badAddress", { address: bad }));
    }
    if (confidential && (files.length || kept.length)) return setError(t("compose.confNoAttachments"));
    if (attachSize + ed.imageBytes() > MAX_TOTAL_BYTES) return setError(t("compose.tooBig"));
    if (sendAt && sendAt.getTime() < Date.now() + 60_000) return setError(t("compose.schedPast"));
    if (ed.isEmpty() && !window.confirm(t("compose.confirmEmpty"))) return;
    if (!form.subject.trim() && !window.confirm(t("compose.confirmNoSubject"))) return;

    // Не даємо автозбереженню створити нову чернетку після надсилання
    closed.current = true;
    window.clearTimeout(timer.current);
    if (saving.current) await saving.current;

    const data = new FormData();
    Object.entries(form).forEach(([k, v]) => data.append(k, v));
    data.append("mailbox", mailbox.id);
    data.append("html", ed.html());
    data.append("body", ed.text());
    if (sendAt) data.append("send_at", sendAt.toISOString());
    if (confidential) {
      data.append("confidential", "1");
      data.append("confidential_days", String(confDays));
      if (confPasscode) data.append("confidential_passcode", "1");
    }
    replyFields(data);
    if (init.in_reply_to_uid) data.append("in_reply_to_uid", String(init.in_reply_to_uid));
    if (init.in_reply_to_folder) data.append("in_reply_to_folder", init.in_reply_to_folder);
    if (draftUid.current) data.append("draft_uid", String(draftUid.current));
    kept.forEach((a) => data.append("keep_attachments", String(a.index)));
    files.forEach((f) => data.append("attachments", f));
    setBusy(true);
    try {
      onSent(await api<SendResult>("/api/mail/send/", { method: "POST", body: data }));
    } catch (err) {
      closed.current = false;
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => {
    setForm({ ...form, [k]: e.target.value });
    touch();
  };
  const initialHtml = init.html ?? `<p></p>${init.quoteHtml ? `<p></p>${init.quoteHtml}` : ""}`;

  const addFiles = (list: FileList | null) => {
    if (list) {
      const picked = Array.from(list as FileList);
      setFiles((cur) => [...cur, ...picked].slice(0, 20 - kept.length));
    }
    touch();
  };
  // camera — одразу камера телефону (capture), інакше звичайний вибір файлів
  const attachInput = (camera = false) => (
    <input
      ref={camera ? cameraRef : undefined}
      type="file"
      multiple={!camera}
      hidden
      disabled={confidential}
      accept={camera ? "image/*" : undefined}
      capture={camera ? "environment" : undefined}
      onChange={(e) => {
        addFiles(e.target.files);
        e.target.value = "";
      }}
    />
  );
  const pills = (
    <>
      {kept.map((a) => (
        <span key={`k${a.index}`} className="pill">
          {a.filename} ({formatBytes(a.size)})
          <button
            type="button"
            className="pill-x"
            onClick={() => {
              setKept(kept.filter((x) => x.index !== a.index));
              touch();
            }}
            aria-label={t("common.remove")}
          >
            ×
          </button>
        </span>
      ))}
      {files.map((f, i) => (
        <span key={i} className="pill">
          {f.name} ({formatBytes(f.size)})
          <button
            type="button"
            className="pill-x"
            onClick={() => {
              setFiles(files.filter((_, j) => j !== i));
              touch();
            }}
            aria-label={t("common.remove")}
          >
            ×
          </button>
        </span>
      ))}
    </>
  );
  const signatureSelect = (
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
  );

  // Телефон: повноекранне вікно, як у застосунку Gmail — ✕ ліворуч, вкладення, «⋯» і «Надіслати» вгорі праворуч
  const headerActions = mobile ? (
    <>
      <label
        className={`icon-btn ${confidential ? "disabled" : ""}`}
        title={confidential ? t("compose.confNoAttachments") : t("compose.attach")}
        aria-label={t("compose.attach")}
      >
        <Icon name="paperclip" />
        {attachInput()}
      </label>
      {/* Поза меню «⋯»: меню закривається одразу, а поле має дожити до вибору фото */}
      {attachInput(true)}
      <div className="dropdown" ref={menuRef}>
        <button
          type="button"
          className={`icon-btn ${menuOpen ? "active" : ""}`}
          aria-label={t("mobile.moreActions")}
          title={t("mobile.moreActions")}
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen(!menuOpen)}
        >
          <Icon name="more" />
        </button>
        {menuOpen && (
          <div className="dropdown-menu compose-menu" role="menu">
            <button
              type="button"
              role="menuitem"
              className={`menu-item ${confidential ? "disabled" : ""}`}
              disabled={confidential}
              onClick={() => {
                cameraRef.current?.click();
                setMenuOpen(false);
              }}
            >
              <Icon name="camera" size={18} /> {t("mobile.takePhoto")}
            </button>
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={scheduleOpen}
              className={`menu-item ${scheduleOpen ? "active" : ""}`}
              onClick={() => {
                setScheduleOpen(!scheduleOpen);
                setMenuOpen(false);
              }}
            >
              <Icon name="clock" size={18} /> {t("compose.schedule")}
            </button>
            <button
              type="button"
              role="menuitemcheckbox"
              aria-checked={confidential}
              className={`menu-item ${confidential ? "active" : ""}`}
              onClick={() => {
                setConfidential(!confidential);
                setMenuOpen(false);
              }}
            >
              <Icon name="lock" size={18} /> {t("compose.confidential")}
              {confidential && <Icon name="check" size={16} />}
            </button>
            <label className="menu-item menu-select">
              <Icon name="signature" size={18} />
              {signatureSelect}
            </label>
            <div className="menu-sep" />
            <button
              type="button"
              role="menuitem"
              className="menu-item danger"
              onClick={() => {
                setMenuOpen(false);
                discard();
              }}
            >
              <Icon name="trash" size={18} /> {t("compose.discard")}
            </button>
          </div>
        )}
      </div>
      <button
        type="button"
        className="icon-btn compose-send"
        disabled={busy}
        onClick={() => submit()}
        aria-label={busy ? t("compose.sending") : t("compose.send")}
        title={busy ? t("compose.sending") : t("compose.send")}
      >
        <Icon name="send" />
      </button>
    </>
  ) : undefined;

  return (
    <Modal
      title={init.draftUid ? t("compose.draftTitle") : t("compose.title")}
      onClose={close}
      wide
      fullScreen={mobile}
      actions={headerActions}
      className={mobile ? "compose-full" : undefined}
    >
      <form
        className="stack compose"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <div className="row-between small muted compose-from">
          <span className="truncate">
            {t("compose.from")}: {mailbox.display_name ? `${mailbox.display_name} <${mailbox.address}>` : mailbox.address}
          </span>
          <span className={`draft-state ${saveState === "error" ? "text-danger" : ""}`} aria-live="polite">
            {saveState === "saving" && t("compose.saving")}
            {saveState === "saved" && (savedAt ? t("compose.savedAt", { time: formatDate(savedAt.toISOString()) }) : t("compose.saved"))}
            {saveState === "error" && t("compose.saveFailed")}
          </span>
        </div>
        <div className="row gap">
          <RecipientInput
            className="grow"
            placeholder={t("compose.to")}
            value={form.to}
            onChange={(v) => set("to")({ target: { value: v } })}
            autoFocus={!init.to}
          />
          {!showCc && (
            <button type="button" className="link-btn" onClick={() => setShowCc(true)}>
              {t("mail.cc")}
            </button>
          )}
        </div>
        {showCc && (
          <>
            <RecipientInput placeholder={t("mail.cc")} value={form.cc} onChange={(v) => set("cc")({ target: { value: v } })} />
            <RecipientInput placeholder={t("compose.bcc")} value={form.bcc} onChange={(v) => set("bcc")({ target: { value: v } })} />
          </>
        )}
        <input placeholder={t("mail.subject")} value={form.subject} onChange={set("subject")} maxLength={300} />
        <RichEditor ref={editor} initialHtml={initialHtml} onError={setError} onChange={touch} autoFocus={!!init.to} compactToolbar={mobile} />
        {mobile ? (
          (kept.length > 0 || files.length > 0) && <div className="row gap-sm wrap compose-pills">{pills}</div>
        ) : (
        <div className="row gap wrap">
          <label className={`btn btn-sm ${confidential ? "disabled" : ""}`} title={confidential ? t("compose.confNoAttachments") : undefined}>
            <Icon name="paperclip" size={14} /> {t("compose.attach")}
            {attachInput()}
          </label>
          {pills}
          <span className="grow" />
          <label className="row gap-sm small">
            <Icon name="signature" size={14} />
            {signatureSelect}
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
        )}
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
        {scheduleOpen && (
          <div className="banner banner-info stack small schedule-panel">
            <strong>{t("compose.schedTitle")}</strong>
            <div className="row gap wrap">
              {schedulePresets().map((p) => (
                <button key={p.key} type="button" className="btn btn-sm" disabled={busy} onClick={() => submit(p.date)}>
                  {t(p.key)} · {formatDate(p.date.toISOString())}
                </button>
              ))}
            </div>
            <div className="row gap wrap">
              <input
                type="datetime-local"
                value={customWhen}
                min={toLocalInput(new Date())}
                onChange={(e) => setCustomWhen(e.target.value)}
                aria-label={t("compose.schedPick")}
              />
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={busy || !customWhen}
                onClick={() => submit(new Date(customWhen))}
              >
                <Icon name="clock" size={14} /> {t("compose.schedSet")}
              </button>
            </div>
          </div>
        )}
        {error && <div className="form-error">{error}</div>}
        {!mobile && (
        <div className="modal-actions">
          <button type="button" className="btn btn-danger" onClick={discard} title={t("compose.discard")}>
            <Icon name="trash" size={16} />
          </button>
          <span className="grow" />
          <button type="button" className="btn" onClick={close}>
            {t("common.close")}
          </button>
          <button
            type="button"
            className={`btn ${scheduleOpen ? "btn-active" : ""}`}
            aria-pressed={scheduleOpen}
            onClick={() => setScheduleOpen(!scheduleOpen)}
            title={t("compose.schedule")}
          >
            <Icon name="clock" size={16} /> {t("compose.schedule")}
          </button>
          <button className="btn btn-primary" disabled={busy}>
            <Icon name="send" size={16} /> {busy ? t("compose.sending") : t("compose.send")}
          </button>
        </div>
        )}
      </form>
    </Modal>
  );
}

function ScheduledDialog({ withBox, onClose }: { withBox: (path: string) => string; onClose: () => void }) {
  const t = useT();
  const toast = useToast();
  const [items, setItems] = useState<ScheduledItem[] | null>(null);
  const [error, setError] = useState("");

  const load = () =>
    get<ScheduledItem[]>(withBox("/api/mail/scheduled/"))
      .then(setItems)
      .catch((e) => setError(errorText(e)));
  useEffect(() => {
    load();
  }, []);

  const cancel = async (item: ScheduledItem) => {
    if (!window.confirm(t("sched.confirmCancel"))) return;
    try {
      await post(`/api/mail/scheduled/${item.id}/cancel/`);
      toast(t("sched.cancelled"), "success");
      load();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const STATUS: Record<ScheduledItem["status"], TKey> = {
    pending: "sched.status.pending",
    sending: "sched.status.sending",
    failed: "sched.status.failed",
  };

  return (
    <Modal title={t("mail.scheduled")} onClose={onClose} wide>
      <div className="stack">
        <p className="small muted">{t("sched.hint")}</p>
        {items === null && !error && <div className="empty-small">{t("common.loading")}</div>}
        {items && items.length === 0 && <div className="empty-small">{t("sched.empty")}</div>}
        {items && items.length > 0 && (
          <div className="table-card">
            <table className="table">
              <thead>
                <tr>
                  <th>{t("sched.when")}</th>
                  <th>{t("mail.subject")}</th>
                  <th>{t("mail.to")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((m) => (
                  <tr key={m.id}>
                    <td className="small nowrap">
                      {formatDate(m.send_at)}
                      <div>
                        <span className={`pill ${m.status === "failed" ? "pill-danger" : ""}`}>{t(STATUS[m.status])}</span>
                      </div>
                    </td>
                    <td>
                      <div className="truncate">
                        {m.confidential && <Icon name="lock" size={12} />} {m.subject || t("mail.noSubject")}
                      </div>
                    </td>
                    <td className="small">{m.to}</td>
                    <td>
                      {m.status !== "sending" && (
                        <button className="btn btn-sm btn-danger" onClick={() => cancel(m)}>
                          {t("sched.cancel")}
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
      .then((r) => setItems(Array.isArray(r) ? r : []))
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
        <p className="small muted">{t("mail2.confIntro")}</p>
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
                    <td className="small conf-recipients">{recipientsText(m.recipients)}</td>
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
