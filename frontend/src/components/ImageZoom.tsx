import { PointerEvent as RPointerEvent, useEffect, useRef, useState } from "react";
import Icon from "./Icon";

const MIN = 1;
const MAX = 10;

interface View {
  scale: number;
  x: number;
  y: number;
  rotate: number;
}

const INITIAL: View = { scale: 1, x: 0, y: 0, rotate: 0 };

/** Перегляд зображення з масштабуванням: колесо/тачпад, pinch на телефоні, перетягування, подвійний клік. */
export default function ImageZoom({ src, alt }: { src: string; alt: string }) {
  const [view, setView] = useState<View>(INITIAL);
  const box = useRef<HTMLDivElement>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const last = useRef<{ x: number; y: number; dist: number } | null>(null);

  useEffect(() => setView(INITIAL), [src]);

  // Масштабування з фіксованою точкою під курсором
  const zoomAt = (factor: number, cx: number, cy: number) => {
    setView((v) => {
      const scale = Math.min(MAX, Math.max(MIN, v.scale * factor));
      if (scale === MIN) return { ...v, scale: 1, x: 0, y: 0 };
      const rect = box.current?.getBoundingClientRect();
      if (!rect) return { ...v, scale };
      const px = cx - rect.left - rect.width / 2;
      const py = cy - rect.top - rect.height / 2;
      const k = scale / v.scale;
      return { ...v, scale, x: px - (px - v.x) * k, y: py - (py - v.y) * k };
    });
  };

  const zoomCenter = (factor: number) => {
    const rect = box.current?.getBoundingClientRect();
    if (rect) zoomAt(factor, rect.left + rect.width / 2, rect.top + rect.height / 2);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "+" || e.key === "=") zoomCenter(1.25);
      if (e.key === "-") zoomCenter(0.8);
      if (e.key === "0") setView(INITIAL);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  // Непасивний слухач: інакше pinch на тачпаді (ctrl+wheel) масштабує всю сторінку браузера
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const factor = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0015));
      zoomAt(factor, e.clientX, e.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  });

  const onPointerDown = (e: RPointerEvent) => {
    (e.target as Element).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    last.current = null;
  };

  const onPointerMove = (e: RPointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    if (pts.length === 2) {
      const [a, b] = pts;
      const dist = Math.hypot(a.x - b.x, a.y - b.y);
      const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
      if (last.current?.dist) zoomAt(dist / last.current.dist, mid.x, mid.y);
      last.current = { ...mid, dist };
    } else if (pts.length === 1) {
      const p = pts[0];
      if (last.current && view.scale > 1) {
        const dx = p.x - last.current.x;
        const dy = p.y - last.current.y;
        setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
      }
      last.current = { x: p.x, y: p.y, dist: 0 };
    }
  };

  const onPointerUp = (e: RPointerEvent) => {
    pointers.current.delete(e.pointerId);
    last.current = null;
  };

  return (
    <div className="zoom">
      <div
        ref={box}
        className={`zoom-stage ${view.scale > 1 ? "zoomed" : ""}`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onDoubleClick={(e) => (view.scale > 1 ? setView(INITIAL) : zoomAt(2.5, e.clientX, e.clientY))}
      >
        <img
          src={src}
          alt={alt}
          draggable={false}
          style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.scale}) rotate(${view.rotate}deg)` }}
        />
      </div>
      <div className="zoom-controls">
        <button className="icon-btn" onClick={() => zoomCenter(0.8)} aria-label="Зменшити" title="Зменшити (−)">
          −
        </button>
        <button className="zoom-level" onClick={() => setView(INITIAL)} title="Вписати (0)">
          {Math.round(view.scale * 100)}%
        </button>
        <button className="icon-btn" onClick={() => zoomCenter(1.25)} aria-label="Збільшити" title="Збільшити (+)">
          +
        </button>
        <button className="icon-btn" onClick={() => setView((v) => ({ ...v, rotate: (v.rotate + 90) % 360 }))} aria-label="Повернути" title="Повернути">
          <Icon name="refresh" size={16} />
        </button>
      </div>
    </div>
  );
}
