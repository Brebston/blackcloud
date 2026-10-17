import { FormEvent, useState } from "react";
import { del, errorText, post } from "../api/client";
import type { FileTag } from "../api/types";
import { TKey, useT } from "../i18n";
import Icon from "./Icon";
import Modal from "./Modal";

// Особисті кольорові теги (як мітки у Finder): позначити файл чи теку, створити новий тег.

export const TAG_COLORS = ["red", "orange", "yellow", "green", "teal", "blue", "violet", "pink", "gray"] as const;

export function TagDot({ tag, size = 10 }: { tag: FileTag; size?: number }) {
  return <span className={`tag-dot tag-${tag.color}`} style={{ width: size, height: size }} title={tag.name} aria-label={tag.name} />;
}

export default function TagsDialog({
  target,
  tags,
  onClose,
  onChanged,
}: {
  /** null — лише керування тегами (без позначення конкретного елемента) */
  target: { type: "file" | "folder"; id: string; name: string; tags: string[] } | null;
  tags: FileTag[];
  onClose: () => void;
  onChanged: () => void;
}) {
  const t = useT();
  const [assigned, setAssigned] = useState<Set<string>>(new Set(target?.tags || []));
  const [name, setName] = useState("");
  const [color, setColor] = useState<string>("blue");
  const [error, setError] = useState("");

  const toggle = async (tag: FileTag) => {
    if (!target) return;
    const on = !assigned.has(tag.id);
    setError("");
    try {
      await post("/api/files/tags/assign/", { tag: tag.id, [`${target.type}s`]: [target.id], assign: on });
      setAssigned((cur) => {
        const next = new Set(cur);
        if (on) next.add(tag.id);
        else next.delete(tag.id);
        return next;
      });
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setError("");
    try {
      const tag = await post<FileTag>("/api/files/tags/", { name: name.trim(), color });
      setName("");
      if (target) {
        await post("/api/files/tags/assign/", { tag: tag.id, [`${target.type}s`]: [target.id], assign: true });
        setAssigned((cur) => new Set(cur).add(tag.id));
      }
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const remove = async (tag: FileTag) => {
    if (!window.confirm(t("org.deleteTag", { name: tag.name }))) return;
    try {
      await del(`/api/files/tags/${tag.id}/`);
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={target ? t("org.tagsTitle", { name: target.name }) : t("org.manageTags")} onClose={onClose}>
      <div className="stack">
        {tags.length === 0 && <div className="empty-small">{t("org.noTags")}</div>}
        <ul className="tag-list">
          {tags.map((tag) => (
            <li key={tag.id}>
              {target ? (
                <label className="check">
                  <input type="checkbox" checked={assigned.has(tag.id)} onChange={() => toggle(tag)} />
                  <TagDot tag={tag} /> {tag.name}
                </label>
              ) : (
                <span className="row gap-sm">
                  <TagDot tag={tag} /> {tag.name}
                  {typeof tag.count === "number" && <span className="small muted">{tag.count}</span>}
                </span>
              )}
              <button className="icon-btn" onClick={() => remove(tag)} aria-label={t("common.delete")} title={t("common.delete")}>
                <Icon name="trash" size={14} />
              </button>
            </li>
          ))}
        </ul>
        <form className="stack tag-new" onSubmit={create}>
          <strong className="small">{t("org.newTag")}</strong>
          <div className="row gap-sm">
            <input className="grow" placeholder={t("org.tagName")} maxLength={40} value={name} onChange={(e) => setName(e.target.value)} />
            <button className="btn btn-primary" disabled={!name.trim()}>
              <Icon name="plus" size={14} /> {t("org.addTag")}
            </button>
          </div>
          <div className="tag-colors" role="radiogroup">
            {TAG_COLORS.map((c) => (
              <button
                key={c}
                type="button"
                role="radio"
                aria-checked={color === c}
                aria-label={t(`org.color.${c}` as TKey)}
                title={t(`org.color.${c}` as TKey)}
                className={`tag-swatch tag-${c} ${color === c ? "active" : ""}`}
                onClick={() => setColor(c)}
              />
            ))}
          </div>
        </form>
        <p className="small muted">{t("org.tagsHint")}</p>
        {error && <div className="form-error">{error}</div>}
      </div>
    </Modal>
  );
}
