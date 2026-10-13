// Аватар: фото профілю (якщо є) або перша літера імені на кольоровому тлі.
// Колір визначається хешем адреси/імені — стабільний для людини; усі кольори палітри
// мають контраст з білим текстом не нижче 4.5:1 (WCAG AA).
import { useEffect, useState } from "react";

const PALETTE = [
  "#6d28d9", // violet-700
  "#1d4ed8", // blue-700
  "#0369a1", // sky-700
  "#0f766e", // teal-700
  "#15803d", // green-700
  "#854d0e", // yellow-800
  "#c2410c", // orange-700
  "#be123c", // rose-700
  "#a21caf", // fuchsia-700
  "#475569", // slate-600
];

function hash(text: string): number {
  // FNV-1a: детермінований і однаковий у всіх браузерах
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function avatarColor(key: string): string {
  return PALETTE[hash(key.trim().toLowerCase()) % PALETTE.length];
}

export function avatarLetter(name: string, email?: string | null): string {
  const source = (name || email || "?").trim();
  const ch = Array.from(source)[0] || "?";
  return ch.toUpperCase();
}

export default function Avatar({
  name,
  email,
  url,
  size = 32,
  className = "",
  label,
}: {
  name: string;
  email?: string | null;
  url?: string | null;
  size?: number;
  className?: string;
  /** Підпис для читачів екрана; без нього аватар декоративний (поруч зазвичай є ім'я). */
  label?: string;
}) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  const a11y = label ? { role: "img", "aria-label": label, title: label } : { "aria-hidden": true };
  const dims = { width: size, height: size, fontSize: Math.max(11, Math.round(size * 0.42)) };

  if (url && !failed) {
    return (
      <span className={`avatar bc-avatar has-photo ${className}`} style={dims} {...a11y}>
        <img src={url} alt="" loading="lazy" decoding="async" draggable={false} onError={() => setFailed(true)} />
      </span>
    );
  }
  return (
    <span className={`avatar bc-avatar ${className}`} style={{ ...dims, background: avatarColor(email || name), color: "#fff" }} {...a11y}>
      {avatarLetter(name, email)}
    </span>
  );
}
