// Тема й акцентний колір. Застосовуються до <html> одразу при старті (з localStorage),
// а після входу — з налаштувань профілю на сервері, щоб вибір зберігався між пристроями.

export type Theme = "system" | "dark" | "light";
export type Accent = "violet" | "blue" | "teal" | "green" | "amber" | "orange" | "rose" | "slate";

export const THEMES: Theme[] = ["system", "light", "dark"];

/** Значення `swatch` — лише для кружечка у виборі кольору. */
export const ACCENTS: { id: Accent; swatch: string }[] = [
  { id: "violet", swatch: "#7f77dd" },
  { id: "blue", swatch: "#2f6fe0" },
  { id: "teal", swatch: "#1f978c" },
  { id: "green", swatch: "#2f9e4a" },
  { id: "amber", swatch: "#d9a52e" },
  { id: "orange", swatch: "#e06a25" },
  { id: "rose", swatch: "#c8457a" },
  { id: "slate", swatch: "#5f6b80" },
];

const THEME_KEY = "bc_theme";
const ACCENT_KEY = "bc_accent";

const isTheme = (v: unknown): v is Theme => typeof v === "string" && (THEMES as string[]).includes(v);
const isAccent = (v: unknown): v is Accent => typeof v === "string" && ACCENTS.some((a) => a.id === v);

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* приватний режим або заблоковане сховище: вибір просто не запам'ятається локально */
  }
}

export function storedAppearance(): { theme: Theme; accent: Accent } {
  const theme = read(THEME_KEY);
  const accent = read(ACCENT_KEY);
  return { theme: isTheme(theme) ? theme : "system", accent: isAccent(accent) ? accent : "violet" };
}

/** Застосовує тему/акцент до документа і запам'ятовує їх у браузері. */
export function applyAppearance(theme?: string | null, accent?: string | null) {
  const root = document.documentElement;
  if (isTheme(theme)) {
    root.dataset.theme = theme;
    write(THEME_KEY, theme);
  }
  if (isAccent(accent)) {
    root.dataset.accent = accent;
    write(ACCENT_KEY, accent);
  }
}

/** Фактична схема з урахуванням «як у системі». */
export function resolvedScheme(theme: Theme): "light" | "dark" {
  if (theme !== "system") return theme;
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}
