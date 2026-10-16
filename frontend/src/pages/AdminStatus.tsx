import { useCallback, useEffect, useState } from "react";
import { errorText, get } from "../api/client";
import Icon from "../components/Icon";
import { TKey, useT } from "../i18n";
import { formatBytes, formatDate } from "../lib/format";

// Адмінка → «Стан сервера»: диск, пам'ять, процесор, черги, резервні копії, сертифікати, служби.
// Дані з GET /api/admin/status/; помилка в одному блоці приходить як {ok:false, reason}.

type Failed = { ok: false; reason?: string };
type Block<T> = (T & { ok: boolean }) | Failed;

interface Disk { percent: number; total: number; used: number; free: number; threshold: number }
interface Memory { percent: number; total: number; used: number; available: number; swap_total: number; swap_used: number }
interface Load { load1: number; load5: number; load15: number; cpus: number; uptime_s: number | null }
interface Antivirus {
  enabled: boolean;
  reachable: boolean;
  pending: number;
  pending_versions: number;
  oldest_minutes: number | null;
  infected: number;
  unscanned: number;
}
interface Thumbs { enabled: boolean; pending?: number }
interface Queues { skipped?: boolean; lengths?: Record<string, number> }
interface Backup {
  configured: boolean;
  last_success_at?: string | null;
  last_status?: string | null;
  running?: boolean;
  stale?: boolean;
  next_backup?: string | null;
  repo_size_bytes?: number | null;
}
interface Cert { ok: boolean; skipped?: boolean; days_left?: number; not_after?: string; reason?: string }
interface Service { ok: boolean; reason?: string; age_seconds?: number }

interface StatusData {
  ok: boolean;
  problems: string[];
  disk: Block<Disk>;
  memory: Block<Memory>;
  load: Block<Load>;
  antivirus: Block<Antivirus>;
  thumbnails: Block<Thumbs>;
  celery_queues: Block<Queues>;
  backup: Block<Backup>;
  certs: { web: Cert; mail: Cert; checked_at?: string };
  services: Record<string, Service>;
  thresholds: { disk_percent: number; memory_percent: number; cert_days: number };
  generated_at: string;
}

const REFRESH_MS = 30_000;

function has<T>(b: Block<T>): b is T & { ok: boolean } {
  return !("reason" in b && b.ok === false && Object.keys(b).length <= 2);
}

function Meter({ percent, warn, danger }: { percent: number; warn: number; danger: number }) {
  const cls = percent >= danger ? "quota-danger" : percent >= warn ? "quota-warning" : "quota-ok";
  return (
    <div className="quota-track status-meter" role="meter" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}>
      <div className={`quota-fill ${cls}`} style={{ width: `${Math.min(100, percent)}%` }} />
    </div>
  );
}

function StatePill({ ok, okText, badText }: { ok: boolean; okText: string; badText: string }) {
  return <span className={`pill ${ok ? "pill-success" : "pill-danger"}`}>{ok ? okText : badText}</span>;
}

export function ServerStatusTab({ onOpenBackups }: { onOpenBackups: () => void }) {
  const t = useT();
  const [data, setData] = useState<StatusData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (refresh = false) => {
    setBusy(true);
    try {
      setData(await get<StatusData>(`/api/admin/status/${refresh ? "?refresh=1" : ""}`));
      setError("");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load();
    }, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [load]);

  if (error && !data) return <div className="banner banner-warning">{error}</div>;
  if (!data) return <div className="empty-small">{t("common.loading")}</div>;

  const noData = <span className="muted">{t("status.noData")}</span>;
  const { disk, memory, load: cpu, antivirus: av, thumbnails: th, celery_queues: queues, backup: bk, certs, services } = data;

  const certRow = (label: string, c: Cert) => (
    <>
      <dt>{label}</dt>
      <dd>
        {c.skipped ? (
          <span className="muted">{t("status.certSkipped")}</span>
        ) : typeof c.days_left === "number" ? (
          <span className={c.days_left < data.thresholds.cert_days ? "text-danger" : c.days_left < 30 ? "text-warning" : ""}>
            {t("status.certDays", { n: c.days_left, date: formatDate(c.not_after, false) })}
          </span>
        ) : (
          <span className="text-danger">{t("status.fail")}</span>
        )}
      </dd>
    </>
  );

  return (
    <div className="stack">
      <div className="row-between status-head">
        <div className="row gap-sm">
          {data.ok ? (
            <span className="pill pill-success">{t("status.allOk")}</span>
          ) : (
            <span className="pill pill-danger">{t("status.problems", { n: data.problems.length })}</span>
          )}
          <span className="small muted">{t("status.updated", { when: formatDate(data.generated_at) })}</span>
        </div>
        <button className="icon-btn" onClick={() => load(true)} disabled={busy} aria-label={t("common.refresh")} title={t("common.refresh")}>
          <Icon name="refresh" />
        </button>
      </div>
      {error && <div className="banner banner-warning">{error}</div>}
      {!data.ok && (
        <div className="banner banner-warning">
          <ul className="status-problems">
            {data.problems.map((p) => (
              <li key={p}>{t(`status.problem.${p}` as TKey)}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="settings-grid">
        <div className="card stack">
          <h3>{t("status.disk")}</h3>
          {has(disk) ? (
            <>
              <div className="stat-value">{disk.percent}%</div>
              <Meter percent={disk.percent} warn={disk.threshold - 10} danger={disk.threshold} />
              <div className="small muted">
                {t("status.diskUsed", { used: formatBytes(disk.used), total: formatBytes(disk.total), free: formatBytes(disk.free) })}
              </div>
              <p className="small muted">{t("status.diskHint", { n: disk.threshold })}</p>
            </>
          ) : (
            noData
          )}
        </div>

        <div className="card stack">
          <h3>{t("status.memory")}</h3>
          {has(memory) ? (
            <>
              <div className="stat-value">{memory.percent}%</div>
              <Meter percent={memory.percent} warn={data.thresholds.memory_percent - 10} danger={data.thresholds.memory_percent} />
              <div className="small muted">
                {t("status.memoryUsed", {
                  used: formatBytes(memory.used),
                  total: formatBytes(memory.total),
                  available: formatBytes(memory.available),
                })}
              </div>
              {memory.swap_total > 0 && (
                <div className="small muted">{t("status.swap", { used: formatBytes(memory.swap_used), total: formatBytes(memory.swap_total) })}</div>
              )}
            </>
          ) : (
            noData
          )}
        </div>

        <div className="card stack">
          <h3>{t("status.load")}</h3>
          {has(cpu) ? (
            <dl className="info-list status-list">
              <dt>{t("status.loadAvg")}</dt>
              <dd className={cpu.load5 >= cpu.cpus * 2 ? "text-danger" : ""}>
                {cpu.load1} / {cpu.load5} / {cpu.load15}
              </dd>
              <dt>{t("status.cpus")}</dt>
              <dd>{cpu.cpus}</dd>
              {typeof cpu.uptime_s === "number" && (
                <>
                  <dt>{t("status.uptime")}</dt>
                  <dd>{t("status.uptimeValue", { d: Math.floor(cpu.uptime_s / 86400), h: Math.floor((cpu.uptime_s % 86400) / 3600) })}</dd>
                </>
              )}
            </dl>
          ) : (
            noData
          )}
        </div>

        <div className="card stack">
          <h3>{t("status.antivirus")}</h3>
          {has(av) ? (
            <>
              <div>
                {!av.enabled ? (
                  <span className="pill">{t("status.avOff")}</span>
                ) : (
                  <StatePill ok={av.reachable} okText={t("status.avOk")} badText={t("status.avDown")} />
                )}
              </div>
              <dl className="info-list status-list">
                <dt>{t("status.avPending")}</dt>
                <dd>
                  {av.pending} {av.pending_versions > 0 && <span className="muted">{t("status.avPendingVersions", { n: av.pending_versions })}</span>}
                </dd>
                {av.oldest_minutes !== null && (
                  <>
                    <dt>{t("status.avOldest")}</dt>
                    <dd className={av.ok ? "" : "text-danger"}>{t("status.minutes", { n: av.oldest_minutes })}</dd>
                  </>
                )}
                <dt>{t("status.avInfected")}</dt>
                <dd className={av.infected ? "text-danger" : ""}>{av.infected}</dd>
                <dt>{t("status.avUnscanned")}</dt>
                <dd>{av.unscanned}</dd>
              </dl>
            </>
          ) : (
            noData
          )}
        </div>

        <div className="card stack">
          <h3>{t("status.thumbs")}</h3>
          <dl className="info-list status-list">
            <dt>{t("status.thumbsPending")}</dt>
            <dd>{has(th) ? (th.enabled ? th.pending : <span className="muted">{t("status.thumbsOff")}</span>) : noData}</dd>
            {has(queues) &&
              queues.lengths &&
              Object.entries(queues.lengths).map(([name, n]) => (
                <QueueRow key={name} name={name} n={n} />
              ))}
            {!has(queues) && (
              <>
                <dt>Celery</dt>
                <dd className="text-danger">{t("status.fail")}</dd>
              </>
            )}
          </dl>
          <p className="small muted">{t("status.thumbsHint")}</p>
        </div>

        <div className="card stack">
          <div className="row-between">
            <h3>{t("status.backup")}</h3>
            <button className="btn btn-sm" onClick={onOpenBackups}>
              {t("status.backupDetails")}
            </button>
          </div>
          {has(bk) ? (
            !bk.configured ? (
              <span className="pill">{t("status.backupNotConfigured")}</span>
            ) : (
              <>
                <div>
                  {bk.running ? (
                    <span className="pill pill-warning">{t("status.backupRunning")}</span>
                  ) : (
                    <StatePill ok={bk.ok} okText={t("status.backupOk")} badText={t("status.backupBad")} />
                  )}
                </div>
                <dl className="info-list status-list">
                  <dt>{t("backup.lastSuccess")}</dt>
                  <dd>{bk.last_success_at ? formatDate(bk.last_success_at) : t("backup.never")}</dd>
                  <dt>{t("backup.next")}</dt>
                  <dd>{bk.next_backup ? formatDate(bk.next_backup) : "—"}</dd>
                  <dt>{t("backup.size")}</dt>
                  <dd>{typeof bk.repo_size_bytes === "number" ? formatBytes(bk.repo_size_bytes) : "—"}</dd>
                </dl>
              </>
            )
          ) : (
            noData
          )}
        </div>

        <div className="card stack">
          <h3>{t("status.certs")}</h3>
          <dl className="info-list status-list">
            {certRow(t("status.certWeb"), certs.web)}
            {certRow(t("status.certMail"), certs.mail)}
          </dl>
        </div>

        <div className="card stack">
          <h3>{t("status.services")}</h3>
          <dl className="info-list status-list">
            {Object.entries(services).map(([name, s]) => (
              <ServiceRow key={name} name={name} s={s} />
            ))}
          </dl>
        </div>
      </div>
      <p className="small muted">{t("status.autoRefresh")}</p>
    </div>
  );
}

function QueueRow({ name, n }: { name: string; n: number }) {
  const t = useT();
  return (
    <>
      <dt>{t("status.queue", { name })}</dt>
      <dd className={n > 500 ? "text-warning" : ""}>{n}</dd>
    </>
  );
}

function ServiceRow({ name, s }: { name: string; s: Service }) {
  const t = useT();
  const label = (["db", "cache", "celery"] as const).includes(name as "db") ? t(`status.svc.${name}` as TKey) : name;
  return (
    <>
      <dt>{label}</dt>
      <dd>
        <span className={`pill ${s.ok ? "pill-success" : "pill-danger"}`}>{s.ok ? t("status.ok") : t("status.fail")}</span>
      </dd>
    </>
  );
}
