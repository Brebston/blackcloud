// Рядок списку зі свайпом (лише сенсорний екран): вліво/вправо відкриває кольорову підкладку з іконкою,
// після порогу виконує дію, інакше пружинить назад. Напрям фіксується після ~10px: вертикальний жест —
// звичайна прокрутка, рядок не рухається. Клік (відкрити лист) і вкладені кнопки працюють як раніше.
import { ReactNode, useEffect, useRef, useState } from "react";
import Icon from "./Icon";

export interface SwipeAction {
  icon: string;
  label: string;
  /** Клас кольору підкладки: swipe-danger, swipe-archive… */
  tone: string;
  run: () => void;
}

const LOCK = 10;

export default function SwipeRow({
  className,
  left,
  right,
  children,
}: {
  className?: string;
  /** Свайп ліворуч (рядок їде вліво, підкладка праворуч). */
  left?: SwipeAction;
  /** Свайп праворуч. */
  right?: SwipeAction;
  children: ReactNode;
}) {
  const [dx, setDx] = useState(0);
  const [dragging, setDragging] = useState(false);
  const g = useRef({ x: 0, y: 0, lock: "" as "" | "h" | "v", active: false, width: 1, moved: false });
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);

  const enabled = !!(left || right);
  const threshold = () => Math.min(110, g.current.width * 0.35);

  const onTouchStart = (e: { touches: ArrayLike<{ clientX: number; clientY: number }>; currentTarget: HTMLElement }) => {
    if (!enabled || e.touches.length !== 1) return;
    const s = g.current;
    s.x = e.touches[0].clientX;
    s.y = e.touches[0].clientY;
    s.lock = "";
    s.active = true;
    s.moved = false;
    s.width = e.currentTarget.offsetWidth || 1;
  };
  const onTouchMove = (e: { touches: ArrayLike<{ clientX: number; clientY: number }> }) => {
    const s = g.current;
    if (!s.active) return;
    let ddx = e.touches[0].clientX - s.x;
    const ddy = e.touches[0].clientY - s.y;
    if (!s.lock) {
      if (Math.abs(ddx) < LOCK && Math.abs(ddy) < LOCK) return;
      s.lock = Math.abs(ddx) > Math.abs(ddy) ? "h" : "v";
      if (s.lock === "v") {
        s.active = false;
        return;
      }
      s.moved = true;
      setDragging(true);
    }
    ddx -= Math.sign(ddx) * LOCK;
    // Напрям без дії — лише легкий опір
    if ((ddx < 0 && !left) || (ddx > 0 && !right)) ddx = ddx / 6;
    setDx(ddx);
  };
  const onTouchEnd = () => {
    const s = g.current;
    if (!s.active) return;
    s.active = false;
    setDragging(false);
    if (s.lock !== "h") return;
    const action = dx < 0 ? left : right;
    if (action && Math.abs(dx) >= threshold()) {
      setDx(dx < 0 ? -s.width : s.width);
      timer.current = window.setTimeout(() => {
        action.run();
        // Якщо рядок не прибрали зі списку (помилка) — повертаємо на місце
        timer.current = window.setTimeout(() => setDx(0), 400);
      }, 180);
    } else {
      setDx(0);
    }
  };

  const action = dx < 0 ? left : dx > 0 ? right : undefined;
  const armed = !!action && Math.abs(dx) >= threshold();
  return (
    <div className="swipe">
      {action && (
        <div className={`swipe-bg ${action.tone} ${dx < 0 ? "swipe-bg-end" : ""} ${armed ? "armed" : ""}`} aria-hidden="true">
          <Icon name={action.icon} size={20} />
          <span>{action.label}</span>
        </div>
      )}
      <div
        className={`swipe-fg ${className || ""} ${dragging ? "" : "swipe-anim"}`}
        style={dx ? { transform: `translateX(${dx}px)` } : undefined}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        onTouchCancel={onTouchEnd}
        onClickCapture={(e: { stopPropagation: () => void; preventDefault: () => void }) => {
          // Після свайпу не відкриваємо лист випадковим «кліком»
          if (g.current.moved) {
            g.current.moved = false;
            e.stopPropagation();
            e.preventDefault();
          }
        }}
      >
        {children}
      </div>
    </div>
  );
}
