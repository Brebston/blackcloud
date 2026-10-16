import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { del, get } from "../api/client";
import { TKey, useT } from "../i18n";
import Icon from "./Icon";

// Поле «Кому / Копія / Прихована» з підказками як у Gmail: після 2 літер поточної адреси
// (частина після останньої коми) показує контакти, тих, кому писали, і користувачів BlackCloud.
// Значення лишається звичайним рядком «Ім'я <адреса>, адреса, …» — як і раніше для сервера.

export interface Suggestion {
  address: string;
  name: string;
  sources: ("contact" | "recent" | "user")[];
  avatar_url?: string | null;
  contact_id?: string;
}

const MIN_CHARS = 2;

function currentToken(value: string): { start: number; text: string } {
  const start = Math.max(value.lastIndexOf(","), value.lastIndexOf(";")) + 1;
  return { start, text: value.slice(start).trim() };
}

function formatRecipient(s: Suggestion): string {
  if (!s.name || s.name.toLowerCase() === s.address) return s.address;
  const safe = /[,;<>"@]/.test(s.name) ? `"${s.name.replace(/["\\]/g, "")}"` : s.name;
  return `${safe} <${s.address}>`;
}

function initials(s: Suggestion): string {
  const base = (s.name || s.address).trim();
  const parts = base.split(/[\s.@_-]+/).filter(Boolean);
  return ((parts[0]?.[0] || "") + (parts[1]?.[0] || "")).toUpperCase() || "?";
}

export default function RecipientInput({
  value,
  onChange,
  placeholder,
  autoFocus,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  className?: string;
}) {
  const t = useT();
  const [items, setItems] = useState<Suggestion[]>([]);
  const [active, setActive] = useState(0);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const seq = useRef(0);
  const token = currentToken(value);

  useEffect(() => {
    const q = token.text;
    if (q.length < MIN_CHARS || /[<>]/.test(q)) {
      setItems([]);
      return;
    }
    const my = ++seq.current;
    const timer = window.setTimeout(() => {
      get<Suggestion[]>(`/api/mail/contacts/suggest/?q=${encodeURIComponent(q)}`)
        .then((r) => {
          if (my !== seq.current) return; // відповідь на старіший запит
          setItems(r);
          setActive(0);
        })
        .catch(() => setItems([]));
    }, 150);
    return () => window.clearTimeout(timer);
  }, [token.text]);

  const pick = (s: Suggestion) => {
    const before = value.slice(0, token.start).replace(/\s*$/, "");
    const next = `${before ? `${before} ` : ""}${formatRecipient(s)}, `;
    onChange(next);
    setItems([]);
    setOpen(false);
    window.requestAnimationFrame(() => {
      const el = input.current;
      if (el) {
        el.focus();
        el.setSelectionRange(next.length, next.length);
      }
    });
  };

  const forget = async (s: Suggestion) => {
    try {
      await del(`/api/mail/contacts/recent/?address=${encodeURIComponent(s.address)}`);
      setItems((list) => list.filter((x) => x.address !== s.address || x.sources.length > 1));
    } catch {
      /* підказка — не критично */
    }
  };

  const shown = open && items.length > 0;

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (!shown) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % items.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + items.length) % items.length);
    } else if (e.key === "Enter" || e.key === "Tab") {
      if (items[active]) {
        e.preventDefault();
        pick(items[active]);
      }
    } else if (e.key === "Escape") {
      // Закрити лише підказки, а не все вікно листа
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    }
  };

  return (
    <div className={`recipient-input ${className || ""}`}>
      <input
        ref={input}
        inputMode="email"
        autoComplete="off"
        spellCheck={false}
        role="combobox"
        aria-expanded={shown}
        aria-autocomplete="list"
        placeholder={placeholder}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => window.setTimeout(() => setOpen(false), 150)}
        onKeyDown={onKeyDown}
      />
      {shown && (
        <ul className="recipient-suggest" role="listbox">
          {items.map((s, i) => (
            <li
              key={s.address}
              role="option"
              aria-selected={i === active}
              className={i === active ? "active" : ""}
              onMouseDown={(e) => {
                e.preventDefault(); // не втрачати фокус до вибору
                pick(s);
              }}
              onMouseEnter={() => setActive(i)}
            >
              {s.avatar_url ? (
                <img className="recipient-av" src={s.avatar_url} alt="" />
              ) : (
                <span className="recipient-av" aria-hidden="true">
                  {initials(s)}
                </span>
              )}
              <span className="recipient-text">
                <span className="truncate">{s.name || s.address}</span>
                {s.name && <span className="small muted truncate">{s.address}</span>}
              </span>
              <span className="recipient-tags">
                {s.sources.map((src) => (
                  <span key={src} className="recipient-tag">
                    {t(`contacts.src.${src}` as TKey)}
                  </span>
                ))}
                {s.sources.includes("recent") && (
                  <button
                    type="button"
                    className="icon-btn recipient-forget"
                    title={t("contacts.forget")}
                    aria-label={t("contacts.forget")}
                    onMouseDown={(e) => {
                      e.preventDefault();
                      e.stopPropagation();
                      forget(s);
                    }}
                  >
                    <Icon name="x" size={12} />
                  </button>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
