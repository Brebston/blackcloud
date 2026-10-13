import { useEffect, useState } from "react";

// Та сама межа, що й у styles.css (@media (max-width: 820px))
export const MOBILE_QUERY = "(max-width: 820px)";

function matches(): boolean {
  try {
    return window.matchMedia(MOBILE_QUERY).matches;
  } catch {
    return false;
  }
}

/** true — вузький (мобільний) екран; оновлюється при повороті / зміні розміру вікна. */
export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState(matches);
  useEffect(() => {
    const mq = window.matchMedia(MOBILE_QUERY);
    const onChange = () => setMobile(mq.matches);
    onChange();
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  return mobile;
}

/**
 * Висота видимої області (без екранної клавіатури) у CSS-змінних --vvh і --vv-top.
 * iOS (і Chrome на Android за замовчуванням) не зменшує layout viewport, коли відкрита клавіатура,
 * тож вікна-шторки орієнтуються на visualViewport, щоб поля, панель інструментів і кнопки лишались видимими.
 */
let vvUsers = 0;
export function useVisualViewportVars(enabled = true) {
  useEffect(() => {
    const vv = window.visualViewport;
    if (!enabled || !vv) return;
    const root = document.documentElement;
    const update = () => {
      root.style.setProperty("--vvh", `${Math.round(vv.height)}px`);
      root.style.setProperty("--vv-top", `${Math.round(vv.offsetTop)}px`);
    };
    vvUsers++;
    update();
    vv.addEventListener("resize", update);
    vv.addEventListener("scroll", update);
    return () => {
      vv.removeEventListener("resize", update);
      vv.removeEventListener("scroll", update);
      if (--vvUsers === 0) {
        root.style.removeProperty("--vvh");
        root.style.removeProperty("--vv-top");
      }
    };
  }, [enabled]);
}
