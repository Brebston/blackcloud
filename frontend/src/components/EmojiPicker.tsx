import { useEffect, useMemo, useRef, useState } from "react";
import { EMOJI_CATEGORIES, recentEmoji, rememberEmoji, searchEmoji } from "../lib/emoji";

/** Панель вибору емоджі: недавні, категорії, пошук українською або англійською. */
export default function EmojiPicker({
  onPick,
  onClose,
  className = "",
}: {
  onPick: (emoji: string) => void;
  onClose: () => void;
  className?: string;
}) {
  const [query, setQuery] = useState("");
  const [recent] = useState(recentEmoji);
  const [active, setActive] = useState(recent.length ? "recent" : EMOJI_CATEGORIES[0].id);
  const ref = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);

  // Закриття кліком поза панеллю або Esc
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const results = useMemo(() => searchEmoji(query), [query]);

  const pick = (e: string) => {
    rememberEmoji(e);
    onPick(e);
  };

  const jump = (id: string) => {
    setActive(id);
    setQuery("");
    setTimeout(() => bodyRef.current?.querySelector(`[data-cat="${id}"]`)?.scrollIntoView({ block: "start" }), 0);
  };

  const sections = [
    ...(recent.length ? [{ id: "recent", label: "Недавні", icon: "🕘", items: recent.map((e) => ({ e, k: "" })) }] : []),
    ...EMOJI_CATEGORIES,
  ];

  return (
    <div className={`emoji-picker ${className}`} ref={ref} role="dialog" aria-label="Вибір емоджі">
      <input
        className="emoji-search"
        placeholder="Пошук: серце, сміх, like…"
        value={query}
        autoFocus
        onChange={(e) => setQuery(e.target.value)}
      />
      <div className="emoji-body" ref={bodyRef}>
        {query ? (
          results.length ? (
            <div className="emoji-grid">
              {results.map((e) => (
                <button key={e} type="button" className="emoji-btn" onClick={() => pick(e)} title={e}>
                  {e}
                </button>
              ))}
            </div>
          ) : (
            <div className="muted small emoji-empty">Нічого не знайдено</div>
          )
        ) : (
          sections.map((cat) => (
            <div key={cat.id} data-cat={cat.id}>
              <div className="emoji-cat-title">{cat.label}</div>
              <div className="emoji-grid">
                {cat.items.map(({ e }) => (
                  <button key={cat.id + e} type="button" className="emoji-btn" onClick={() => pick(e)}>
                    {e}
                  </button>
                ))}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="emoji-tabs">
        {sections.map((cat) => (
          <button
            key={cat.id}
            type="button"
            className={`emoji-tab ${active === cat.id && !query ? "active" : ""}`}
            onClick={() => jump(cat.id)}
            title={cat.label}
            aria-label={cat.label}
          >
            {cat.icon}
          </button>
        ))}
      </div>
    </div>
  );
}
