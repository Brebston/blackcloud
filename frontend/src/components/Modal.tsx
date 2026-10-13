import { ReactNode, useEffect } from "react";
import { useVisualViewportVars } from "../hooks/useIsMobile";
import { useT } from "../i18n";
import Icon from "./Icon";

// На вузькому екрані (styles.css, ≤ 820px) будь-яке вікно стає «нижньою шторкою» з прокруткою всередині.
// fullScreen — на весь екран (напр., редактор листа на телефоні): ✕ ліворуч, дії (actions) праворуч у заголовку.
export default function Modal({
  title,
  onClose,
  children,
  wide,
  fullScreen,
  actions,
  className,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  wide?: boolean;
  fullScreen?: boolean;
  actions?: ReactNode;
  className?: string;
}) {
  const t = useT();
  // Шторка/повноекранне вікно тримається над екранною клавіатурою (див. styles.css, --vvh)
  useVisualViewportVars();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const close = (
    <button type="button" className="icon-btn modal-close" onClick={onClose} aria-label={t("misc.close")}>
      <Icon name="x" />
    </button>
  );

  return (
    <div
      className={`modal-backdrop ${fullScreen ? "modal-backdrop-full" : ""}`}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={`modal ${wide ? "modal-wide" : ""} ${fullScreen ? "modal-full" : ""} ${className || ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="modal-head">
          {fullScreen && close}
          <h3>{title}</h3>
          {actions && <div className="modal-head-actions">{actions}</div>}
          {!fullScreen && close}
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
