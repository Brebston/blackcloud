import { useT } from "../i18n";
import { formatBytes } from "../lib/format";

export default function QuotaBar({ used, quota }: { used: number; quota: number }) {
  const t = useT();
  const pct = quota > 0 ? Math.min(100, (used / quota) * 100) : 0;
  const level = pct > 90 ? "danger" : pct > 75 ? "warning" : "ok";
  return (
    <div className="quota">
      <div className="quota-label">
        <span>{t("files.quota.storage")}</span>
        <span>{pct.toFixed(0)}%</span>
      </div>
      <div className="quota-track" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100}>
        <div className={`quota-fill quota-${level}`} style={{ width: `${pct}%` }} />
      </div>
      <div className="quota-text">
        {t("files.quota.usedOf", { used: formatBytes(used), total: formatBytes(quota) })}
      </div>
    </div>
  );
}
