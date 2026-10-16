import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { del, errorText, get, patch, post } from "../api/client";
import type { FileItem } from "../api/types";
import FileViewer from "../components/FileViewer";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import { useAuth } from "../hooks/useAuth";
import { TKey, currentLang, useT } from "../i18n";
import { formatBytes } from "../lib/format";
import { thumbnailUrl } from "../lib/viewer";

// Галерея «Фото»: фото й відео з усіх тек стрічкою за датою зйомки (EXIF), групування за
// місяцями й днями, альбоми-посилання, панель «Інформація» з камерою й координатами.

interface PhotoInfoShort {
  kind: "photo" | "video";
  sort_at: string;
  taken_at: string | null;
  width: number | null;
  height: number | null;
  duration: number | null;
  has_location: boolean;
}
export interface PhotoItem extends FileItem {
  photo: PhotoInfoShort;
}
interface PhotoDetail extends FileItem {
  photo: PhotoInfoShort & {
    taken_source: "exif" | "video" | "name" | "upload";
    camera_make: string;
    camera_model: string;
    lens: string;
    location: { lat: number; lon: number; alt?: number } | null;
    albums: { id: string; name: string }[];
  };
}
interface Page {
  results: PhotoItem[];
  next_cursor: string | null;
  total?: number;
  processing?: number;
}
interface Album {
  id: string;
  name: string;
  count: number;
  first_at: string | null;
  last_at: string | null;
  cover: PhotoItem | null;
}

const PAGE = 150;
const locale = () => (currentLang() === "en" ? "en-GB" : "uk-UA");

function localKey(iso: string, len: 7 | 10): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`.slice(0, len);
}

function monthTitle(key: string): string {
  const [y, m] = key.split("-").map(Number);
  const d = new Date(y, m - 1, 1);
  const label = d.toLocaleDateString(locale(), { month: "long", year: "numeric" });
  return label.charAt(0).toUpperCase() + label.slice(1);
}

function dayTitle(key: string): string {
  const [y, m, day] = key.split("-").map(Number);
  const d = new Date(y, m - 1, day);
  const now = new Date();
  return d.toLocaleDateString(locale(), {
    weekday: "short",
    day: "numeric",
    month: "long",
    year: d.getFullYear() === now.getFullYear() ? undefined : "numeric",
  });
}

function duration(sec: number | null): string {
  if (!sec) return "";
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

function osmUrl(lat: number, lon: number): string {
  return `https://www.openstreetmap.org/?mlat=${lat.toFixed(6)}&mlon=${lon.toFixed(6)}#map=16/${lat.toFixed(5)}/${lon.toFixed(5)}`;
}

// ─────────────────────────── Панель «Інформація» ───────────────────────────

function PhotoInfoPanel({ file }: { file: FileItem }) {
  const t = useT();
  const [data, setData] = useState<PhotoDetail | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError("");
    get<PhotoDetail>(`/api/photos/${file.id}/`)
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(errorText(e)));
    return () => {
      cancelled = true;
    };
  }, [file.id]);
  if (error) return <div className="small">{error}</div>;
  if (!data) return <div className="small">{t("photos.loadingInfo")}</div>;
  const p = data.photo;
  const when = p.taken_at || p.sort_at;
  const camera = [p.camera_make, p.camera_model.startsWith(p.camera_make) ? p.camera_model.slice(p.camera_make.length).trim() : p.camera_model]
    .filter(Boolean)
    .join(" ");
  return (
    <dl>
      <dt>{t("photos.taken")}</dt>
      <dd>
        {new Date(when).toLocaleString(locale(), { dateStyle: "full", timeStyle: "short" })}
        <div className="small" style={{ opacity: 0.7 }}>
          {t(`photos.src.${p.taken_source}` as TKey)}
        </div>
      </dd>
      {camera && (
        <>
          <dt>{t("photos.camera")}</dt>
          <dd>{camera}</dd>
        </>
      )}
      {p.lens && (
        <>
          <dt>{t("photos.lens")}</dt>
          <dd>{p.lens}</dd>
        </>
      )}
      {p.width && p.height && (
        <>
          <dt>{t("photos.dimensions")}</dt>
          <dd>{t("photos.megapixels", { w: p.width, h: p.height, mp: ((p.width * p.height) / 1e6).toFixed(1) })}</dd>
        </>
      )}
      {p.kind === "video" && p.duration && (
        <>
          <dt>{t("photos.duration")}</dt>
          <dd>{duration(p.duration)}</dd>
        </>
      )}
      <dt>{t("photos.fileSize")}</dt>
      <dd>
        {data.name} · {formatBytes(data.size)}
      </dd>
      <dt>{t("photos.location")}</dt>
      <dd>
        {p.location ? (
          <>
            {p.location.lat.toFixed(5)}, {p.location.lon.toFixed(5)}
            {typeof p.location.alt === "number" && ` · ${Math.round(p.location.alt)} m`}
            <div>
              <a href={osmUrl(p.location.lat, p.location.lon)} target="_blank" rel="noopener noreferrer">
                {t("photos.openMap")}
              </a>
            </div>
            <div className="small" style={{ opacity: 0.7 }}>
              {t("photos.locationNote")}
            </div>
          </>
        ) : (
          <span style={{ opacity: 0.7 }}>{t("photos.noLocation")}</span>
        )}
      </dd>
      <dt>{t("photos.folder")}</dt>
      <dd>
        <Link to={data.folder ? `/files?folder=${data.folder}` : "/files"}>{t("photos.openFolder")}</Link>
      </dd>
      {p.albums.length > 0 && (
        <>
          <dt>{t("photos.inAlbums")}</dt>
          <dd>
            {p.albums.map((a, i) => (
              <span key={a.id}>
                {i > 0 && ", "}
                <Link to={`/photos/albums/${a.id}`}>{a.name}</Link>
              </span>
            ))}
          </dd>
        </>
      )}
    </dl>
  );
}

// ─────────────────────────── Додати до альбому ───────────────────────────

function AddToAlbumDialog({ ids, onClose, onDone }: { ids: string[]; onClose: () => void; onDone: () => void }) {
  const t = useT();
  const toast = useToast();
  const [albums, setAlbums] = useState<Album[] | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    get<Album[]>("/api/photos/albums/").then(setAlbums).catch((e) => setError(errorText(e)));
  }, []);

  const addTo = async (a: Album) => {
    setBusy(true);
    try {
      const r = await post<{ added: number }>(`/api/photos/albums/${a.id}/items/`, { file_ids: ids });
      toast(t("photos.addedToAlbum", { name: a.name, n: r.added }), "success");
      onDone();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    setBusy(true);
    try {
      const a = await post<Album>("/api/photos/albums/", { name: name.trim(), file_ids: ids });
      toast(t("photos.addedToAlbum", { name: a.name, n: a.count }), "success");
      onDone();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal title={t("photos.chooseAlbum")} onClose={onClose}>
      <div className="stack">
        <form className="row gap-sm" onSubmit={create}>
          <input className="grow" placeholder={t("photos.albumName")} value={name} maxLength={100} onChange={(e) => setName(e.target.value)} autoFocus />
          <button className="btn btn-primary" disabled={busy || !name.trim()}>
            <Icon name="plus" size={16} /> {t("photos.newAlbum")}
          </button>
        </form>
        {error && <div className="form-error">{error}</div>}
        {albums && albums.length === 0 && <div className="empty-small">{t("photos.noAlbums")}</div>}
        <ul className="album-pick">
          {(albums || []).map((a) => (
            <li key={a.id}>
              <button type="button" disabled={busy} onClick={() => addTo(a)}>
                {a.cover?.has_thumbnail ? <img src={thumbnailUrl(a.cover)} alt="" /> : <span className="album-pick-empty"><Icon name="image" size={18} /></span>}
                <span className="truncate">{a.name}</span>
                <span className="small muted">{t("photos.albumCount", { n: a.count })}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </Modal>
  );
}

// ─────────────────────────── Сітка зі стрічкою ───────────────────────────

function PhotoGrid({
  endpoint,
  emptyText,
  header,
  selectionActions,
  reloadKey,
}: {
  endpoint: string;
  emptyText: string;
  header?: ReactNode;
  selectionActions: (ids: string[], items: PhotoItem[], done: () => void) => ReactNode;
  reloadKey?: number;
}) {
  const t = useT();
  const { user } = useAuth();
  const [items, setItems] = useState<PhotoItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [processing, setProcessing] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [kind, setKind] = useState<"" | "photo" | "video">("");
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [viewer, setViewer] = useState<number | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const gen = useRef(0);

  const load = useCallback(
    async (next: string | null, reset: boolean) => {
      const my = reset ? ++gen.current : gen.current;
      setLoading(true);
      try {
        const sep = endpoint.includes("?") ? "&" : "?";
        const q = `limit=${PAGE}${kind ? `&kind=${kind}` : ""}${next ? `&cursor=${encodeURIComponent(next)}` : ""}`;
        const r = await get<Page>(`${endpoint}${sep}${q}`);
        if (my !== gen.current) return;
        setItems((cur) => (reset ? r.results : [...cur, ...r.results]));
        setCursor(r.next_cursor);
        if (typeof r.total === "number") setTotal(r.total);
        if (typeof r.processing === "number") setProcessing(r.processing);
        setError("");
      } catch (e) {
        if (my === gen.current) setError(errorText(e));
      } finally {
        if (my === gen.current) setLoading(false);
      }
    },
    [endpoint, kind],
  );

  useEffect(() => {
    setItems([]);
    setCursor(null);
    setSelected(new Set());
    load(null, true);
  }, [load, reloadKey]);

  // Нескінченна прокрутка
  useEffect(() => {
    const el = sentinel.current;
    if (!el || !cursor) return;
    const io = new IntersectionObserver((entries) => {
      if (entries.some((e) => e.isIntersecting) && !loading) load(cursor, false);
    }, { rootMargin: "800px" });
    io.observe(el);
    return () => io.disconnect();
  }, [cursor, loading, load]);

  // Поки йде обробка EXIF — оновлюємо стрічку раз на хвилину
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => document.visibilityState === "visible" && load(null, true), 60_000);
    return () => window.clearInterval(timer);
  }, [processing, load]);

  const groups = useMemo(() => {
    const months: { key: string; days: { key: string; items: { item: PhotoItem; index: number }[] }[] }[] = [];
    items.forEach((item, index) => {
      const day = localKey(item.photo.sort_at, 10);
      const month = day.slice(0, 7);
      let m = months[months.length - 1];
      if (!m || m.key !== month) months.push((m = { key: month, days: [] }));
      let d = m.days[m.days.length - 1];
      if (!d || d.key !== day) m.days.push((d = { key: day, items: [] }));
      d.items.push({ item, index });
    });
    return months;
  }, [items]);

  const toggle = (id: string) =>
    setSelected((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const doneSelecting = () => {
    setSelected(new Set());
    setSelecting(false);
  };

  return (
    <div className="stack photos-stack">
      <div className="row-between photos-toolbar">
        <div className="row gap-sm">
          <div className="seg" role="group">
            {(["", "photo", "video"] as const).map((k) => (
              <button key={k || "all"} className={`seg-btn ${kind === k ? "active" : ""}`} onClick={() => setKind(k)}>
                {t(k === "photo" ? "photos.onlyPhotos" : k === "video" ? "photos.onlyVideos" : "photos.all")}
              </button>
            ))}
          </div>
          {total !== null && <span className="small muted">{t("photos.count", { n: total })}</span>}
        </div>
        <div className="row gap-sm">
          {header}
          {items.length > 0 && (
            <button className={`btn btn-sm ${selecting ? "btn-primary" : ""}`} onClick={() => (selecting ? doneSelecting() : setSelecting(true))}>
              {selecting ? t("photos.cancelSelect") : t("photos.select")}
            </button>
          )}
        </div>
      </div>
      {processing > 0 && <div className="banner banner-info small">{t("photos.processing", { n: processing })}</div>}
      {error && <div className="banner banner-warning">{error}</div>}
      {!loading && items.length === 0 && !error && <div className="empty-small photos-empty">{emptyText}</div>}

      {groups.map((m) => (
        <section key={m.key} className="photo-month">
          <h3 className="photo-month-title">{monthTitle(m.key)}</h3>
          <div className="photo-days">
          {m.days.map((d) => (
            <div key={d.key} className="photo-day">
              <div className="photo-day-head">
                <span>{dayTitle(d.key)}</span>
                {selecting && (
                  <button
                    className="link-btn small"
                    onClick={() =>
                      setSelected((cur) => {
                        const next = new Set(cur);
                        const all = d.items.every(({ item }) => next.has(item.id));
                        d.items.forEach(({ item }) => (all ? next.delete(item.id) : next.add(item.id)));
                        return next;
                      })
                    }
                  >
                    {t("photos.selectDay")}
                  </button>
                )}
              </div>
              <div className="photo-grid">
                {d.items.map(({ item, index }) => {
                  const isSel = selected.has(item.id);
                  return (
                    <button
                      key={item.id}
                      type="button"
                      className={`photo-tile ${isSel ? "selected" : ""}`}
                      aria-pressed={selecting ? isSel : undefined}
                      aria-label={item.name}
                      title={item.name}
                      onClick={() => (selecting ? toggle(item.id) : setViewer(index))}
                    >
                      {item.has_thumbnail ? (
                        <img src={thumbnailUrl(item)} alt="" loading="lazy" decoding="async" />
                      ) : (
                        <span className="photo-tile-empty">
                          <Icon name={item.photo.kind === "video" ? "video" : "image"} size={28} />
                        </span>
                      )}
                      {item.photo.kind === "video" && (
                        <span className="photo-tile-video">
                          <Icon name="video" size={12} /> {duration(item.photo.duration)}
                        </span>
                      )}
                      {selecting && <span className="photo-tile-check">{isSel && <Icon name="check" size={14} />}</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
          </div>
        </section>
      ))}
      <div ref={sentinel} className="photos-sentinel">
        {loading && <span className="small muted">{t("common.loading")}</span>}
      </div>

      {selecting && selected.size > 0 && (
        <div className="photo-selbar" role="toolbar">
          <span>{t("photos.selected", { n: selected.size })}</span>
          <div className="row gap-sm">
            {selectionActions(
              [...selected],
              items.filter((i) => selected.has(i.id)),
              () => {
                doneSelecting();
                load(null, true);
              },
            )}
          </div>
        </div>
      )}

      {viewer !== null && (
        <FileViewer
          files={items}
          index={viewer}
          officeEnabled={!!user?.features?.office}
          onIndex={(i) => {
            setViewer(i);
            if (i >= items.length - 3 && cursor && !loading) load(cursor, false);
          }}
          onClose={() => setViewer(null)}
          info={(f) => <PhotoInfoPanel key={f.id} file={f} />}
        />
      )}
    </div>
  );
}

// ─────────────────────────── Альбоми ───────────────────────────

function AlbumsList() {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const [albums, setAlbums] = useState<Album[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState("");

  const load = () => get<Album[]>("/api/photos/albums/").then(setAlbums).catch((e) => toast(errorText(e), "error"));
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const create = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    try {
      const a = await post<Album>("/api/photos/albums/", { name: name.trim() });
      setCreating(false);
      setName("");
      navigate(`/photos/albums/${a.id}`);
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  return (
    <div className="stack">
      <div className="row-between">
        <span className="small muted">{t("photos.albumsHint")}</span>
        <button className="btn btn-primary btn-sm" onClick={() => setCreating(true)}>
          <Icon name="plus" size={16} /> {t("photos.newAlbum")}
        </button>
      </div>
      {albums === null && <div className="empty-small">{t("common.loading")}</div>}
      {albums && albums.length === 0 && <div className="empty-small">{t("photos.noAlbums")}</div>}
      <div className="album-grid">
        {(albums || []).map((a) => (
          <Link key={a.id} to={`/photos/albums/${a.id}`} className="album-card">
            <span className="album-cover">
              {a.cover?.has_thumbnail ? <img src={thumbnailUrl(a.cover)} alt="" loading="lazy" /> : <Icon name="image" size={32} />}
            </span>
            <strong className="truncate">{a.name}</strong>
            <span className="small muted">
              {t("photos.albumCount", { n: a.count })}
              {a.first_at && a.last_at && ` · ${new Date(a.first_at).getFullYear()}${new Date(a.first_at).getFullYear() !== new Date(a.last_at).getFullYear() ? `–${new Date(a.last_at).getFullYear()}` : ""}`}
            </span>
          </Link>
        ))}
      </div>
      {creating && (
        <Modal title={t("photos.newAlbum")} onClose={() => setCreating(false)}>
          <form className="stack" onSubmit={create}>
            <label>
              {t("photos.albumName")}
              <input value={name} maxLength={100} autoFocus onChange={(e) => setName(e.target.value)} />
            </label>
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setCreating(false)}>
                {t("common.cancel")}
              </button>
              <button className="btn btn-primary" disabled={!name.trim()}>
                {t("photos.create")}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

function AlbumView({ albumId }: { albumId: string }) {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const [album, setAlbum] = useState<Album | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [name, setName] = useState("");
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback(() => {
    get<Album>(`/api/photos/albums/${albumId}/`)
      .then(setAlbum)
      .catch((e) => {
        toast(errorText(e), "error");
        navigate("/photos?tab=albums", { replace: true });
      });
  }, [albumId, navigate, toast]);
  useEffect(load, [load]);

  const rename = async (e: FormEvent) => {
    e.preventDefault();
    try {
      setAlbum(await patch<Album>(`/api/photos/albums/${albumId}/`, { name: name.trim() }));
      setRenaming(false);
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  const remove = async () => {
    if (!album || !window.confirm(t("photos.deleteAlbumConfirm", { name: album.name }))) return;
    try {
      await del(`/api/photos/albums/${albumId}/`);
      toast(t("photos.albumDeleted"), "success");
      navigate("/photos?tab=albums");
    } catch (err) {
      toast(errorText(err), "error");
    }
  };

  return (
    <div className="stack">
      <div className="row-between album-head">
        <div className="row gap-sm">
          <Link to="/photos?tab=albums" className="btn btn-sm">
            <Icon name="chevronLeft" size={16} /> {t("photos.back")}
          </Link>
          <h3 className="truncate">{album?.name || "…"}</h3>
        </div>
        <div className="row gap-sm">
          <button
            className="btn btn-sm"
            onClick={() => {
              setName(album?.name || "");
              setRenaming(true);
            }}
          >
            <Icon name="edit" size={14} /> {t("photos.rename")}
          </button>
          <button className="btn btn-sm btn-danger-outline" onClick={remove}>
            <Icon name="trash" size={14} /> {t("photos.deleteAlbum")}
          </button>
        </div>
      </div>
      <PhotoGrid
        endpoint={`/api/photos/albums/${albumId}/items/`}
        emptyText={t("photos.emptyAlbum")}
        reloadKey={reloadKey}
        selectionActions={(ids, _items, done) => (
          <>
            {ids.length === 1 && (
              <button
                className="btn btn-sm"
                onClick={async () => {
                  try {
                    setAlbum(await patch<Album>(`/api/photos/albums/${albumId}/`, { cover: ids[0] }));
                    toast(t("photos.coverSet"), "success");
                    done();
                  } catch (err) {
                    toast(errorText(err), "error");
                  }
                }}
              >
                {t("photos.makeCover")}
              </button>
            )}
            <button
              className="btn btn-sm btn-danger-outline"
              onClick={async () => {
                try {
                  const r = await del<{ removed: number; album: Album }>(`/api/photos/albums/${albumId}/items/`, { file_ids: ids });
                  setAlbum(r.album);
                  toast(t("photos.removedFromAlbum", { n: r.removed }), "success");
                  done();
                  setReloadKey((k) => k + 1);
                } catch (err) {
                  toast(errorText(err), "error");
                }
              }}
            >
              {t("photos.removeFromAlbum")}
            </button>
          </>
        )}
      />
      {renaming && (
        <Modal title={t("photos.rename")} onClose={() => setRenaming(false)}>
          <form className="stack" onSubmit={rename}>
            <input value={name} maxLength={100} autoFocus onChange={(e) => setName(e.target.value)} />
            <div className="modal-actions">
              <button type="button" className="btn" onClick={() => setRenaming(false)}>
                {t("common.cancel")}
              </button>
              <button className="btn btn-primary" disabled={!name.trim()}>
                {t("common.save")}
              </button>
            </div>
          </form>
        </Modal>
      )}
    </div>
  );
}

// ─────────────────────────── Сторінка ───────────────────────────

export default function PhotosPage() {
  const t = useT();
  const { albumId } = useParams();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const tabParam = params.get("tab");
  const tab = albumId || tabParam === "albums" ? "albums" : "timeline";
  const [adding, setAdding] = useState<{ ids: string[]; done: () => void } | null>(null);

  return (
    <div className="page photos-page">
      <div className="page-head">
        <h2>{t("photos.title")}</h2>
        <div className="tabs photos-tabs">
          <button className={`tab ${tab === "timeline" ? "active" : ""}`} onClick={() => navigate("/photos")}>
            <Icon name="image" size={16} /> {t("photos.timeline")}
          </button>
          <button className={`tab ${tab === "albums" ? "active" : ""}`} onClick={() => navigate("/photos?tab=albums")}>
            <Icon name="grid" size={16} /> {t("photos.albums")}
          </button>
        </div>
      </div>
      {tab === "timeline" && (
        <PhotoGrid
          endpoint="/api/photos/"
          emptyText={t("photos.empty")}
          selectionActions={(ids, _items, done) => (
            <button className="btn btn-sm btn-primary" onClick={() => setAdding({ ids, done })}>
              <Icon name="plus" size={14} /> {t("photos.addToAlbum")}
            </button>
          )}
        />
      )}
      {tab === "albums" && (albumId ? <AlbumView albumId={albumId} /> : <AlbumsList />)}
      {adding && (
        <AddToAlbumDialog
          ids={adding.ids}
          onClose={() => setAdding(null)}
          onDone={() => {
            adding.done();
            setAdding(null);
          }}
        />
      )}
    </div>
  );
}
