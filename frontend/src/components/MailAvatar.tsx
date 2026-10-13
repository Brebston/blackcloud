// Аватар відправника, як у Gmail: фото локального користувача BlackCloud (якщо є) або перша літера
// імені на кольорі, що детерміновано залежить від адреси. Кнопка-аватар перемикає вибір листа.
import { useEffect, useState } from "react";
import { ApiError, get } from "../api/client";
import Icon from "./Icon";

/** «Ім'я» <addr>, "Прізвище, Ім'я" <addr>, addr, <addr> → { name, email } (email — у нижньому регістрі). */
export function parseSender(from: string): { name: string; email: string } {
  const value = (from || "").trim();
  const angle = value.match(/<([^<>\s]+@[^<>\s]+)>/);
  let email = angle ? angle[1] : "";
  let name = angle ? value.slice(0, value.indexOf("<")) : value;
  if (!email) {
    const bare = value.match(/[^\s<>"',;]+@[^\s<>"',;]+/);
    if (bare) {
      email = bare[0];
      name = value.replace(bare[0], "");
    }
  }
  name = name.replace(/^[\s"']+|[\s"',;]+$/g, "").replace(/\\"/g, '"').trim();
  return { name, email: email.toLowerCase() };
}

const LETTER = /[\p{L}\p{N}]/u;

/** Перша літера імені (або адреси) — будь-якою абеткою; «?» якщо літер немає. */
export function avatarLetter(from: string): string {
  const { name, email } = parseSender(from);
  for (const source of [name, email]) {
    const ch = Array.from(source).find((c) => LETTER.test(c));
    if (ch) return ch.toLocaleUpperCase();
  }
  return "?";
}

export const AVATAR_COLORS = 12;

/** Номер кольору з фіксованої палітри (FNV-1a від адреси). */
export function avatarColor(key: string): number {
  let h = 0x811c9dc5;
  for (const ch of key.toLowerCase()) {
    h ^= ch.codePointAt(0) || 0;
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h % AVATAR_COLORS;
}

// ── Фото локальних користувачів: один пакетний запит на сторінку списку, кеш на сесію ──

const photoCache = new Map<string, string | null>();
const pending = new Set<string>();
let lookupDisabled = false;
const BATCH = 100;
const EMAIL_RE = /^[^\s@<>",;]+@[^\s@<>",;]+\.[^\s@<>",;]+$/;

/** Завантажує (пакетами по 100) адреси фото для адрес і повертає функцію пошуку. */
export function useSenderPhotos(emails: string[]): (email: string) => string | null {
  const [, setTick] = useState(0);
  const key = Array.from(new Set(emails.map((e) => e.toLowerCase()))).sort().join(",");
  useEffect(() => {
    if (lookupDisabled || !key) return;
    const missing = key.split(",").filter((e) => e && EMAIL_RE.test(e) && e.length <= 254 && !photoCache.has(e) && !pending.has(e));
    if (!missing.length) return;
    let alive = true;
    missing.forEach((e) => pending.add(e));
    const batches: string[][] = [];
    for (let i = 0; i < missing.length; i += BATCH) batches.push(missing.slice(i, i + BATCH));
    Promise.all(
      batches.map(async (batch) => {
        try {
          const found = await get<Record<string, string>>(`/api/users/avatars/?emails=${encodeURIComponent(batch.join(","))}`);
          for (const e of batch) {
            const url = found && typeof found[e] === "string" ? found[e] : null;
            // Лише власні відносні адреси API (img-src 'self')
            photoCache.set(e, url && url.startsWith("/api/") ? url : null);
          }
        } catch (err) {
          // Ендпоінт ще не розгорнуто — до кінця сесії лише літери
          if (err instanceof ApiError && err.status === 404) lookupDisabled = true;
          for (const e of batch) photoCache.set(e, null);
        } finally {
          batch.forEach((e) => pending.delete(e));
        }
      }),
    ).then(() => alive && setTick((n) => n + 1));
    return () => {
      alive = false;
    };
  }, [key]);
  return (email: string) => photoCache.get((email || "").toLowerCase()) || null;
}

export default function MailAvatar({
  from,
  photo,
  selected,
  onToggle,
  label,
  size = 40,
}: {
  /** Значення заголовка From (або To — для надісланих). */
  from: string;
  /** URL фото (якщо є). */
  photo?: string | null;
  selected?: boolean;
  /** Якщо задано — аватар є кнопкою вибору листа. */
  onToggle?: () => void;
  label?: string;
  size?: number;
}) {
  const [broken, setBroken] = useState("");
  const { email } = parseSender(from);
  const showPhoto = !!photo && broken !== photo && !selected;
  const colorClass = `mail-av-c${avatarColor(email || from || "?")}`;
  const content = selected ? (
    <Icon name="check" size={Math.round(size * 0.5)} />
  ) : showPhoto ? (
    <img src={photo!} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setBroken(photo!)} />
  ) : (
    <span aria-hidden="true">{avatarLetter(from)}</span>
  );
  const cls = `mail-av ${selected ? "mail-av-selected" : showPhoto ? "mail-av-photo" : colorClass}`;
  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) };
  if (onToggle) {
    return (
      <button
        type="button"
        className={cls}
        style={style}
        aria-pressed={!!selected}
        aria-label={label}
        title={label}
        onClick={(e: { stopPropagation: () => void }) => {
          e.stopPropagation();
          onToggle();
        }}
      >
        {content}
      </button>
    );
  }
  return (
    <span className={cls} style={style} aria-hidden="true">
      {content}
    </span>
  );
}
