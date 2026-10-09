// Мінімальна локалізація без зовнішніх залежностей.
// Українська — основна мова (і запасна для відсутніх ключів), англійська — переклад.
// Вибір зберігається в браузері, а після входу — у налаштуваннях акаунта (Preferences.language).

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useAuth } from "../hooks/useAuth";
import en from "./en";
import uk from "./uk";

export type Lang = "uk" | "en";
export type TKey = keyof typeof uk;
export type TFunc = (key: TKey, vars?: Record<string, string | number>) => string;

const DICTS: Record<Lang, Record<TKey, string>> = { uk, en };
export const LANGS: { id: Lang; label: string; short: string }[] = [
  { id: "uk", label: "Українська", short: "UK" },
  { id: "en", label: "English", short: "EN" },
];

const LANG_KEY = "bc_lang";
// Позначка «мову змінено до входу»: після входу її треба записати в акаунт, а не перезаписати серверною
const PENDING_KEY = "bc_lang_pending";

const isLang = (v: unknown): v is Lang => v === "uk" || v === "en";

function storage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function storedLang(): Lang {
  const v = storage()?.getItem(LANG_KEY);
  if (isLang(v)) return v;
  return navigator.language?.toLowerCase().startsWith("en") ? "en" : "uk";
}

function remember(lang: Lang, pending: boolean) {
  try {
    storage()?.setItem(LANG_KEY, lang);
    if (pending) storage()?.setItem(PENDING_KEY, "1");
    else storage()?.removeItem(PENDING_KEY);
  } catch {
    /* сховище недоступне — мова діятиме до перезавантаження */
  }
}

export function translate(lang: Lang, key: TKey, vars?: Record<string, string | number>): string {
  const template = DICTS[lang][key] ?? uk[key] ?? String(key);
  if (!vars) return template;
  return template.replace(/\{(\w+)\}/g, (m, name) => (name in vars ? String(vars[name]) : m));
}

interface I18nState {
  lang: Lang;
  setLang: (lang: Lang) => Promise<void>;
  t: TFunc;
}

const I18nContext = createContext<I18nState>({
  lang: "uk",
  setLang: async () => {},
  t: (key, vars) => translate("uk", key, vars),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const { user, updatePreferences } = useAuth();
  const [lang, setLangState] = useState<Lang>(storedLang);
  const serverLang = user?.preferences.language;

  // Синхронізація з акаунтом після входу
  useEffect(() => {
    if (!serverLang) return;
    const pending = storage()?.getItem(PENDING_KEY) === "1";
    if (pending && serverLang !== lang) {
      updatePreferences({ language: lang })
        .then(() => remember(lang, false))
        .catch(() => {});
    } else {
      remember(serverLang, false);
      setLangState(serverLang);
    }
  }, [serverLang]);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  const setLang = useCallback(
    async (next: Lang) => {
      setLangState(next);
      remember(next, !user);
      if (user) await updatePreferences({ language: next });
    },
    [user, updatePreferences],
  );

  const t = useCallback<TFunc>((key, vars) => translate(lang, key, vars), [lang]);
  const value = useMemo(() => ({ lang, setLang, t }), [lang, setLang, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export const useI18n = () => useContext(I18nContext);
export const useT = () => useContext(I18nContext).t;

/** Компактний перемикач мови (верхня панель, сторінка входу). */
export function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { lang, setLang, t } = useI18n();
  return (
    <select
      className={`select-sm lang-switch ${className}`}
      value={lang}
      aria-label={t("common.language")}
      title={t("common.language")}
      onChange={(e) => {
        const next = e.target.value;
        if (isLang(next)) setLang(next).catch(() => {});
      }}
    >
      {LANGS.map((l) => (
        <option key={l.id} value={l.id}>
          {l.short} · {l.label}
        </option>
      ))}
    </select>
  );
}
