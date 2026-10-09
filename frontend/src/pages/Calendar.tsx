import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { api, del, errorText, get, patch, post } from "../api/client";
import type { CalendarT, EventT } from "../api/types";
import Icon from "../components/Icon";
import Modal from "../components/Modal";
import { useToast } from "../components/Toast";
import UserPicker from "../components/UserPicker";
import { useAuth } from "../hooks/useAuth";
import { type Lang, type TKey, useI18n, useT } from "../i18n";

const REPEAT: { value: string; label: TKey }[] = [
  { value: "", label: "calendar.repeat.none" },
  { value: "FREQ=DAILY", label: "calendar.repeat.daily" },
  { value: "FREQ=WEEKLY", label: "calendar.repeat.weekly" },
  { value: "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR", label: "calendar.repeat.weekdays" },
  { value: "FREQ=MONTHLY", label: "calendar.repeat.monthly" },
  { value: "FREQ=YEARLY", label: "calendar.repeat.yearly" },
];
const REMINDERS: { value: string; label: TKey }[] = [
  { value: "", label: "calendar.reminder.none" },
  { value: "0", label: "calendar.reminder.atStart" },
  { value: "10", label: "calendar.reminder.10m" },
  { value: "30", label: "calendar.reminder.30m" },
  { value: "60", label: "calendar.reminder.1h" },
  { value: "1440", label: "calendar.reminder.1d" },
];

const localeOf = (lang: Lang) => (lang === "en" ? "en-GB" : "uk-UA");

/** Короткі назви днів тижня («Пн», «Mon») у порядку відображення сітки. */
function weekdayNames(locale: string, mondayFirst: boolean): string[] {
  const fmt = new Intl.DateTimeFormat(locale, { weekday: "short" });
  // 2024-01-07 — неділя
  return Array.from({ length: 7 }, (_, i) => {
    const name = fmt.format(new Date(2024, 0, 7 + i + (mondayFirst ? 1 : 0)));
    return name.charAt(0).toUpperCase() + name.slice(1);
  });
}

function startOfGrid(month: Date, mondayFirst: boolean): Date {
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const shift = mondayFirst ? (first.getDay() + 6) % 7 : first.getDay();
  return new Date(first.getFullYear(), first.getMonth(), 1 - shift);
}

const toLocalInput = (d: Date) => {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const sameDay = (a: Date, b: Date) => a.toDateString() === b.toDateString();

interface Draft {
  id?: string;
  calendar: string;
  title: string;
  description: string;
  location: string;
  start: string;
  end: string;
  all_day: boolean;
  rrule: string;
  reminder: string;
}

export default function CalendarPage() {
  const { user } = useAuth();
  const toast = useToast();
  const { lang, t } = useI18n();
  const locale = localeOf(lang);
  const mondayFirst = user?.preferences.week_starts_monday ?? true;
  const [month, setMonth] = useState(() => new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  const [calendars, setCalendars] = useState<CalendarT[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [events, setEvents] = useState<EventT[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [manage, setManage] = useState<CalendarT | null>(null);
  const [newCal, setNewCal] = useState(false);

  const gridStart = useMemo(() => startOfGrid(month, mondayFirst), [month, mondayFirst]);
  const days = useMemo(
    () => Array.from({ length: 42 }, (_, i) => new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + i)),
    [gridStart],
  );

  const loadCalendars = useCallback(() => get<CalendarT[]>("/api/calendars/").then(setCalendars), []);
  const loadEvents = useCallback(async () => {
    const end = new Date(gridStart.getFullYear(), gridStart.getMonth(), gridStart.getDate() + 42);
    try {
      setEvents(
        await get<EventT[]>(
          `/api/calendars/events/?start=${encodeURIComponent(gridStart.toISOString())}&end=${encodeURIComponent(end.toISOString())}`,
        ),
      );
    } catch (e) {
      toast(errorText(e), "error");
    }
  }, [gridStart, toast]);

  useEffect(() => {
    loadCalendars();
  }, [loadCalendars]);
  useEffect(() => {
    loadEvents();
  }, [loadEvents]);

  const editable = calendars.filter((c) => c.can_edit);

  const openNew = (day: Date) => {
    if (!editable.length) return;
    const start = new Date(day.getFullYear(), day.getMonth(), day.getDate(), 9, 0);
    const end = new Date(start.getTime() + 60 * 60 * 1000);
    setDraft({
      calendar: editable[0].id,
      title: "",
      description: "",
      location: "",
      start: toLocalInput(start),
      end: toLocalInput(end),
      all_day: false,
      rrule: "",
      reminder: "",
    });
  };

  const openEdit = (ev: EventT) => {
    setDraft({
      id: ev.id,
      calendar: ev.calendar,
      title: ev.title,
      description: ev.description,
      location: ev.location,
      start: toLocalInput(new Date(ev.start)),
      end: toLocalInput(new Date(ev.end)),
      all_day: ev.all_day,
      rrule: ev.rrule,
      reminder: ev.reminder_minutes === null ? "" : String(ev.reminder_minutes),
    });
  };

  const visible = events.filter((e) => !hidden.has(e.calendar));

  return (
    <div className="page calendar-page">
      <div className="page-head">
        <div className="row gap">
          <button className="icon-btn" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() - 1, 1))} aria-label={t("calendar.prevMonth")}>
            <Icon name="chevronLeft" />
          </button>
          <h2 className="month-title">{month.toLocaleDateString(locale, { month: "long", year: "numeric" })}</h2>
          <button className="icon-btn" onClick={() => setMonth(new Date(month.getFullYear(), month.getMonth() + 1, 1))} aria-label={t("calendar.nextMonth")}>
            <Icon name="chevronRight" />
          </button>
          <button className="btn btn-sm" onClick={() => setMonth(new Date(new Date().getFullYear(), new Date().getMonth(), 1))}>
            {t("calendar.today")}
          </button>
        </div>
        <button className="btn btn-primary" onClick={() => openNew(new Date())} disabled={!editable.length}>
          <Icon name="plus" size={16} /> {t("calendar.newEvent")}
        </button>
      </div>

      <div className="calendar-layout">
        <aside className="card cal-sidebar">
          <div className="row-between">
            <strong>{t("calendar.calendars")}</strong>
            <button className="icon-btn" onClick={() => setNewCal(true)} aria-label={t("calendar.newCalendar")}>
              <Icon name="plus" size={16} />
            </button>
          </div>
          {calendars.map((c) => (
            <div key={c.id} className="cal-item">
              <label className="check">
                <input
                  type="checkbox"
                  checked={!hidden.has(c.id)}
                  onChange={() => {
                    const next = new Set(hidden);
                    if (next.has(c.id)) next.delete(c.id);
                    else next.add(c.id);
                    setHidden(next);
                  }}
                />
                <span className="swatch" style={{ background: c.color }} />
                <span className="truncate">{c.name}</span>
              </label>
              {!c.is_owner && <span className="muted small">@{c.owner}</span>}
              <button className="icon-btn" onClick={() => setManage(c)} aria-label={t("calendar.settings")}>
                <Icon name="settings" size={14} />
              </button>
            </div>
          ))}
        </aside>

        <div className="card month-grid">
          {weekdayNames(locale, mondayFirst).map((d) => (
            <div key={d} className="weekday">
              {d}
            </div>
          ))}
          {days.map((day) => {
            const dayEvents = visible.filter((ev) => {
              const s = new Date(ev.occurrence_start || ev.start);
              const e = new Date(ev.occurrence_end || ev.end);
              const dayStart = new Date(day.getFullYear(), day.getMonth(), day.getDate());
              const dayEnd = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1);
              return s < dayEnd && e > dayStart;
            });
            return (
              <div
                key={day.toISOString()}
                className={`day ${day.getMonth() !== month.getMonth() ? "other" : ""} ${sameDay(day, new Date()) ? "today" : ""}`}
                onDoubleClick={() => openNew(day)}
              >
                <div className="day-num">{day.getDate()}</div>
                {dayEvents.slice(0, 4).map((ev) => (
                  <button
                    key={ev.id + (ev.occurrence_start || "")}
                    className="event-chip"
                    style={{ borderLeftColor: ev.color }}
                    onClick={() => openEdit(ev)}
                    title={ev.title}
                  >
                    {!ev.all_day && (
                      <span className="event-time">
                        {new Date(ev.occurrence_start || ev.start).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" })}
                      </span>
                    )}
                    <span className="truncate">{ev.title}</span>
                  </button>
                ))}
                {dayEvents.length > 4 && <div className="more">{t("calendar.more", { n: dayEvents.length - 4 })}</div>}
              </div>
            );
          })}
        </div>
      </div>

      {draft && (
        <EventDialog
          draft={draft}
          calendars={editable}
          onClose={() => setDraft(null)}
          onSaved={() => {
            setDraft(null);
            loadEvents();
          }}
        />
      )}
      {newCal && (
        <NewCalendarDialog
          onClose={() => setNewCal(false)}
          onCreated={() => {
            setNewCal(false);
            loadCalendars();
          }}
        />
      )}
      {manage && (
        <ManageCalendarDialog
          calendar={manage}
          onClose={() => setManage(null)}
          onChanged={() => {
            loadCalendars();
            loadEvents();
          }}
        />
      )}
    </div>
  );
}

function EventDialog({
  draft,
  calendars,
  onClose,
  onSaved,
}: {
  draft: Draft;
  calendars: CalendarT[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const t = useT();
  const [d, setD] = useState<Draft>(draft);
  const [error, setError] = useState("");
  const readOnly = draft.id !== undefined && !calendars.some((c) => c.id === draft.calendar);
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => {
    setD({ ...d, [k]: v });
    setError("");
  };

  const save = async (e: FormEvent) => {
    e.preventDefault();
    if (!d.title.trim()) return setError(t("calendar.errTitle"));
    const start = new Date(d.start);
    let end = new Date(d.end);
    if (Number.isNaN(start.getTime()) || Number.isNaN(end.getTime())) return setError(t("calendar.errDate"));
    if (d.all_day) {
      start.setHours(0, 0, 0, 0);
      end = new Date(end.getFullYear(), end.getMonth(), end.getDate(), 23, 59, 59);
    }
    if (end < start) return setError(t("calendar.errEndBeforeStart"));
    const body = {
      calendar: d.calendar,
      title: d.title.trim(),
      description: d.description,
      location: d.location,
      start: start.toISOString(),
      end: end.toISOString(),
      all_day: d.all_day,
      rrule: d.rrule,
      reminder_minutes: d.reminder === "" ? null : Number(d.reminder),
    };
    try {
      if (d.id) await patch(`/api/calendars/events/${d.id}/`, body);
      else await post("/api/calendars/events/", body);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  const remove = async () => {
    if (!d.id || !window.confirm(t("calendar.deleteEventConfirm"))) return;
    try {
      await del(`/api/calendars/events/${d.id}/`);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  };

  return (
    <Modal title={d.id ? t("calendar.event") : t("calendar.newEventTitle")} onClose={onClose}>
      <form className="stack" onSubmit={save}>
        <input autoFocus placeholder={t("calendar.title")} value={d.title} onChange={(e) => set("title", e.target.value)} disabled={readOnly} maxLength={200} />
        <label className="check">
          <input type="checkbox" checked={d.all_day} onChange={(e) => set("all_day", e.target.checked)} disabled={readOnly} />
          {t("calendar.allDay")}
        </label>
        <div className="grid-2">
          <label>
            {t("calendar.start")}
            <input type="datetime-local" value={d.start} onChange={(e) => set("start", e.target.value)} disabled={readOnly} />
          </label>
          <label>
            {t("calendar.end")}
            <input type="datetime-local" value={d.end} onChange={(e) => set("end", e.target.value)} disabled={readOnly} />
          </label>
        </div>
        <div className="grid-2">
          <label>
            {t("calendar.repeat")}
            <select value={d.rrule} onChange={(e) => set("rrule", e.target.value)} disabled={readOnly}>
              {REPEAT.map((r) => (
                <option key={r.value} value={r.value}>
                  {t(r.label)}
                </option>
              ))}
              {d.rrule && !REPEAT.some((r) => r.value === d.rrule) && <option value={d.rrule}>{d.rrule}</option>}
            </select>
          </label>
          <label>
            {t("calendar.reminder")}
            <select value={d.reminder} onChange={(e) => set("reminder", e.target.value)} disabled={readOnly}>
              {REMINDERS.map((r) => (
                <option key={r.value} value={r.value}>
                  {t(r.label)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {!readOnly && (
          <label>
            {t("calendar.calendar")}
            <select value={d.calendar} onChange={(e) => set("calendar", e.target.value)}>
              {calendars.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <input placeholder={t("calendar.location")} value={d.location} onChange={(e) => set("location", e.target.value)} disabled={readOnly} maxLength={300} />
        <textarea placeholder={t("calendar.description")} rows={3} value={d.description} onChange={(e) => set("description", e.target.value)} disabled={readOnly} />
        {error && <div className="form-error">{error}</div>}
        {!readOnly && (
          <div className="modal-actions">
            {d.id && (
              <button type="button" className="btn btn-danger" onClick={remove}>
                {t("common.delete")}
              </button>
            )}
            <div className="spacer" />
            <button type="button" className="btn" onClick={onClose}>
              {t("common.cancel")}
            </button>
            <button className="btn btn-primary">{t("common.save")}</button>
          </div>
        )}
      </form>
    </Modal>
  );
}

function NewCalendarDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const t = useT();
  const [name, setName] = useState("");
  const [color, setColor] = useState("#1D9E75");
  const [error, setError] = useState("");
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return setError(t("calendar.errName"));
    try {
      await post("/api/calendars/", { name: name.trim(), color });
      onCreated();
    } catch (err) {
      setError(errorText(err));
    }
  };
  return (
    <Modal title={t("calendar.newCalendar")} onClose={onClose}>
      <form className="stack" onSubmit={submit}>
        <input autoFocus placeholder={t("calendar.title")} value={name} onChange={(e) => setName(e.target.value)} maxLength={100} />
        <label className="row gap">
          {t("calendar.color")} <input type="color" value={color} onChange={(e) => setColor(e.target.value)} />
        </label>
        {error && <div className="form-error">{error}</div>}
        <div className="modal-actions">
          <button className="btn btn-primary">{t("common.create")}</button>
        </div>
      </form>
    </Modal>
  );
}

function ManageCalendarDialog({
  calendar,
  onClose,
  onChanged,
}: {
  calendar: CalendarT;
  onClose: () => void;
  onChanged: () => void;
}) {
  const toast = useToast();
  const t = useT();
  const [feedUrl, setFeedUrl] = useState("");
  const [canEdit, setCanEdit] = useState(false);
  const [error, setError] = useState("");

  const importIcs = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    try {
      const r = await api<{ imported: number }>(`/api/calendars/${calendar.id}/import/`, { method: "POST", body: form });
      toast(t("calendar.imported", { n: r.imported }), "success");
      onChanged();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <Modal title={calendar.name} onClose={onClose}>
      <div className="stack">
        <div className="row gap wrap">
          <a className="btn btn-sm" href={`/api/calendars/${calendar.id}/export/`}>
            <Icon name="download" size={14} /> {t("calendar.exportIcs")}
          </a>
          {calendar.can_edit && (
            <label className="btn btn-sm">
              <Icon name="upload" size={14} /> {t("calendar.importIcs")}
              <input type="file" accept=".ics,text/calendar" hidden onChange={(e) => e.target.files?.[0] && importIcs(e.target.files[0])} />
            </label>
          )}
        </div>

        {calendar.is_owner && (
          <>
            <h4>{t("calendar.sharing")}</h4>
            <label className="check">
              <input type="checkbox" checked={canEdit} onChange={(e) => setCanEdit(e.target.checked)} />
              {t("calendar.allowEdit")}
            </label>
            <UserPicker
              onPick={async (u) => {
                try {
                  await post(`/api/calendars/${calendar.id}/share/`, { username: u.username, can_edit: canEdit });
                  toast(t("calendar.shared", { name: u.username }), "success");
                  onChanged();
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            />
            <ul className="plain-list">
              {calendar.shared_with.map((s) => (
                <li key={s.username} className="row-between">
                  <span>
                    {s.username} <span className="muted small">{s.can_edit ? t("calendar.canEdit") : t("calendar.canView")}</span>
                  </span>
                  <button
                    className="link-btn danger"
                    onClick={() => post(`/api/calendars/${calendar.id}/unshare/`, { username: s.username }).then(onChanged)}
                  >
                    {t("calendar.revoke")}
                  </button>
                </li>
              ))}
            </ul>

            <h4>{t("calendar.feed")}</h4>
            <p className="small muted">{t("calendar.feedHint")}</p>
            <div className="row gap wrap">
              <button
                className="btn btn-sm"
                onClick={async () => setFeedUrl((await post<{ url: string }>(`/api/calendars/${calendar.id}/feed/`)).url)}
              >
                {calendar.has_feed ? t("calendar.feedReissue") : t("calendar.feedCreate")}
              </button>
              {calendar.has_feed && (
                <button
                  className="btn btn-sm"
                  onClick={async () => {
                    await post(`/api/calendars/${calendar.id}/feed/`, { disable: true });
                    setFeedUrl("");
                    onChanged();
                  }}
                >
                  {t("calendar.feedDisable")}
                </button>
              )}
            </div>
            {feedUrl && (
              <div className="secret-box">
                <code className="break">{feedUrl}</code>
              </div>
            )}
            <button
              className="btn btn-danger btn-sm"
              onClick={async () => {
                if (!window.confirm(t("calendar.deleteCalendarConfirm", { name: calendar.name }))) return;
                try {
                  await del(`/api/calendars/${calendar.id}/`);
                  onChanged();
                  onClose();
                } catch (e) {
                  setError(errorText(e));
                }
              }}
            >
              {t("calendar.deleteCalendar")}
            </button>
          </>
        )}
        {!calendar.is_owner && (
          <button className="btn btn-sm" onClick={() => post(`/api/calendars/${calendar.id}/unshare/`).then(() => { onChanged(); onClose(); })}>
            {t("calendar.unsubscribe")}
          </button>
        )}
        {error && <div className="form-error">{error}</div>}
      </div>
    </Modal>
  );
}
