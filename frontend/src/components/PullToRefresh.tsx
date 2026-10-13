// «Потягніть, щоб оновити» для сенсорних екранів. Працює лише з touch-подіями (мишу ігнорує)
// і лише коли область прокрутки (.content) уже на самому верху; звичайну прокрутку не зачіпає:
// напрям фіксується після ~10px, і жест униз приймається тільки якщо він переважно вертикальний.
import { ReactNode, useEffect, useRef, useState } from "react";
import { useT } from "../i18n";
import Icon from "./Icon";

const THRESHOLD = 70; // px (видима відстань) — достатньо, щоб оновити
const MAX_PULL = 110;
const RESISTANCE = 0.55;
const LOCK = 10;

export default function PullToRefresh({
  onRefresh,
  enabled = true,
  children,
}: {
  onRefresh: () => Promise<unknown> | void;
  enabled?: boolean;
  children: ReactNode;
}) {
  const t = useT();
  const wrap = useRef<HTMLDivElement>(null);
  const [pull, setPull] = useState(0);
  const [dragging, setDragging] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const refreshingRef = useRef(false);
  const onRefreshRef = useRef(onRefresh);
  onRefreshRef.current = onRefresh;

  useEffect(() => {
    const el = wrap.current;
    if (!el || !enabled) return;
    const scroller = (): HTMLElement | null =>
      (el.closest(".content") as HTMLElement | null) || (document.scrollingElement as HTMLElement | null);
    let tracking = false;
    let lock: "" | "pull" = "";
    let startX = 0;
    let startY = 0;
    let dist = 0;

    const reset = () => {
      tracking = false;
      lock = "";
      setDragging(false);
    };
    const onStart = (e: TouchEvent) => {
      tracking = false;
      if (refreshingRef.current || e.touches.length !== 1) return;
      if ((scroller()?.scrollTop ?? 0) > 0) return;
      tracking = true;
      lock = "";
      dist = 0;
      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;
    };
    const onMove = (e: TouchEvent) => {
      if (!tracking) return;
      const dx = e.touches[0].clientX - startX;
      const dy = e.touches[0].clientY - startY;
      if (!lock) {
        if (Math.abs(dx) < LOCK && Math.abs(dy) < LOCK) return;
        // Лише жест переважно вниз; горизонтальний (свайп рядка) або вгору (прокрутка) — не наш
        if (dy <= 0 || Math.abs(dy) <= Math.abs(dx)) {
          tracking = false;
          return;
        }
        lock = "pull";
        setDragging(true);
      }
      if ((scroller()?.scrollTop ?? 0) > 0) {
        reset();
        setPull(0);
        return;
      }
      if (e.cancelable) e.preventDefault(); // без «пружини» iOS і рідного оновлення сторінки
      dist = Math.max(0, Math.min(MAX_PULL, (dy - LOCK) * RESISTANCE));
      setPull(dist);
    };
    const onEnd = () => {
      if (!tracking) return;
      const was = lock;
      reset();
      if (was === "pull" && dist >= THRESHOLD) {
        refreshingRef.current = true;
        setRefreshing(true);
        setPull(THRESHOLD * 0.8);
        Promise.resolve()
          .then(() => onRefreshRef.current())
          .catch(() => undefined)
          .finally(() => {
            refreshingRef.current = false;
            setRefreshing(false);
            setPull(0);
          });
      } else {
        setPull(0);
      }
    };
    el.addEventListener("touchstart", onStart, { passive: true });
    el.addEventListener("touchmove", onMove, { passive: false });
    el.addEventListener("touchend", onEnd);
    el.addEventListener("touchcancel", onEnd);
    return () => {
      el.removeEventListener("touchstart", onStart);
      el.removeEventListener("touchmove", onMove);
      el.removeEventListener("touchend", onEnd);
      el.removeEventListener("touchcancel", onEnd);
    };
  }, [enabled]);

  const ready = pull >= THRESHOLD;
  return (
    <div className="ptr-wrap" ref={wrap}>
      <div
        className={`ptr ${dragging ? "" : "ptr-anim"} ${refreshing ? "ptr-busy" : ""} ${ready ? "ptr-ready" : ""}`}
        style={{ height: pull }}
        role="status"
        aria-live="polite"
      >
        {(pull > 0 || refreshing) && (
          <span className="ptr-icon" style={refreshing ? undefined : { transform: `rotate(${Math.round((pull / THRESHOLD) * 270)}deg)` }}>
            <Icon name="refresh" size={20} />
          </span>
        )}
        <span className="sr-only">{refreshing ? t("common.loading") : ready ? t("mobile.ptrRelease") : ""}</span>
      </div>
      {children}
    </div>
  );
}
