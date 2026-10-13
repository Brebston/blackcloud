// Лічильники для нижньої панелі вкладок (мобільна версія). Сторінки публікують дані,
// які вже мають (напр., кількість непрочитаних у «Вхідних»), — без додаткових запитів.
import { useEffect, useState } from "react";

type Badges = { mail: number };

let state: Badges = { mail: 0 };
const listeners = new Set<() => void>();

export function setBadge(key: keyof Badges, value: number) {
  if (state[key] === value) return;
  state = { ...state, [key]: value };
  listeners.forEach((fn) => fn());
}

export function useBadges(): Badges {
  const [value, setValue] = useState(state);
  useEffect(() => {
    const fn = () => setValue(state);
    listeners.add(fn);
    fn();
    return () => {
      listeners.delete(fn);
    };
  }, []);
  return value;
}
