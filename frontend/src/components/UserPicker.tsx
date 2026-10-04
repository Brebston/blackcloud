import { useEffect, useState } from "react";
import { get } from "../api/client";

interface PublicUser {
  id: string;
  username: string;
  display_name: string;
}

export default function UserPicker({
  onPick,
  placeholder,
}: {
  onPick: (u: PublicUser) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PublicUser[]>([]);

  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    const t = setTimeout(() => {
      get<PublicUser[]>(`/api/users/search/?q=${encodeURIComponent(q.trim())}`)
        .then(setResults)
        .catch(() => setResults([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="user-picker">
      <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder || "Пошук користувачів…"} />
      {results.length > 0 && (
        <div className="picker-results">
          {results.map((u) => (
            <button
              type="button"
              key={u.id}
              onClick={() => {
                onPick(u);
                setQ("");
                setResults([]);
              }}
            >
              <strong>{u.display_name || u.username}</strong> <span className="muted">@{u.username}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
