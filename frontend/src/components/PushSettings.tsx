import { useCallback, useEffect, useState } from "react";
import { del, errorText, get, post } from "../api/client";
import { useT } from "../i18n";
import { installPrompt, isIOS, isStandalone, onInstallPromptChange, promptInstall, pushSupported } from "../lib/pwa";
import { useToast } from "./Toast";

// Картка «Сповіщення на цьому пристрої»: підписка Web Push цього браузера, вибір подій,
// тестове сповіщення і (де підтримується) кнопка встановлення застосунку.

interface ServerSub {
  id: string;
  endpoint: string;
  notify_mail: boolean;
  notify_chat: boolean;
  notify_other: boolean;
  show_previews: boolean;
}

type Prefs = Pick<ServerSub, "notify_mail" | "notify_chat" | "notify_other" | "show_previews">;

const DEFAULT_PREFS: Prefs = { notify_mail: true, notify_chat: true, notify_other: true, show_previews: true };

function keyBytes(base64url: string): ArrayBuffer {
  const b64 = base64url.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (base64url.length % 4)) % 4);
  const raw = atob(b64);
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return buf;
}

function sameKey(a: ArrayBuffer | null | undefined, b: ArrayBuffer): boolean {
  if (!a) return false;
  const x = new Uint8Array(a);
  const y = new Uint8Array(b);
  return x.length === y.length && x.every((v, i) => v === y[i]);
}

function subscriptionBody(sub: PushSubscription, prefs: Prefs) {
  const json = sub.toJSON();
  return { endpoint: sub.endpoint, p256dh: json.keys?.p256dh ?? "", auth: json.keys?.auth ?? "", ...prefs };
}

async function registration(create: boolean): Promise<ServiceWorkerRegistration | null> {
  const existing = await navigator.serviceWorker.getRegistration("/");
  if (existing || !create) return existing ?? null;
  await navigator.serviceWorker.register("/sw.js", { scope: "/" });
  return navigator.serviceWorker.ready;
}

export default function PushSettings() {
  const t = useT();
  const toast = useToast();
  const supported = pushSupported();
  const needsInstall = isIOS() && !isStandalone();
  const [serverKey, setServerKey] = useState<string | null>(null);
  const [browserSub, setBrowserSub] = useState<PushSubscription | null>(null);
  const [devices, setDevices] = useState<ServerSub[]>([]);
  const [permission, setPermission] = useState<NotificationPermission>(supported ? Notification.permission : "default");
  const [busy, setBusy] = useState(false);
  const [canInstall, setCanInstall] = useState(() => !!installPrompt());

  useEffect(() => onInstallPromptChange(() => setCanInstall(!!installPrompt())), []);

  const load = useCallback(async () => {
    const [key, list] = await Promise.all([
      get<{ public_key: string; enabled: boolean }>("/api/push/vapid-key/"),
      get<{ count: number; results: ServerSub[] }>("/api/push/subscriptions/"),
    ]);
    setServerKey(key.enabled ? key.public_key : "");
    setDevices(list.results);
    if (supported) {
      const reg = await registration(false);
      setBrowserSub(reg ? await reg.pushManager.getSubscription() : null);
    }
  }, [supported]);

  useEffect(() => {
    load().catch((err) => toast(errorText(err), "error"));
  }, [load, toast]);

  const current = browserSub ? devices.find((d) => d.endpoint === browserSub.endpoint) ?? null : null;
  const prefs: Prefs = current ?? DEFAULT_PREFS;

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (err) {
      toast(errorText(err), "error");
    } finally {
      setBusy(false);
    }
  };

  const enable = () =>
    run(async () => {
      if (!serverKey) return;
      const result = await Notification.requestPermission();
      setPermission(result);
      if (result !== "granted") {
        toast(t("push.permissionDenied"), "error");
        return;
      }
      const reg = await registration(true);
      if (!reg) return;
      const key = keyBytes(serverKey);
      let sub = await reg.pushManager.getSubscription();
      // Ключ сервера змінився (новий VAPID) — стара підписка вже не працюватиме
      if (sub && !sameKey(sub.options.applicationServerKey, key)) {
        await sub.unsubscribe();
        sub = null;
      }
      sub = sub ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }));
      await post("/api/push/subscriptions/", subscriptionBody(sub, prefs));
      toast(t("push.enabled"), "success");
      await load();
    });

  const disable = () =>
    run(async () => {
      const sub = browserSub;
      if (!sub) return;
      await del("/api/push/subscriptions/", { endpoint: sub.endpoint });
      await sub.unsubscribe().catch(() => false);
      toast(t("push.disabled"), "info");
      await load();
    });

  const setPref = (field: keyof Prefs, value: boolean) =>
    run(async () => {
      if (!browserSub) return;
      const saved = await post<ServerSub>("/api/push/subscriptions/", subscriptionBody(browserSub, { ...prefs, [field]: value }));
      setDevices((list) => list.map((d) => (d.id === saved.id ? saved : d)));
    });

  const sendTest = () =>
    run(async () => {
      await post("/api/push/test/");
      toast(t("push.testSent"), "success");
    });

  const install = () => run(async () => void (await promptInstall()));

  const active = !!current && permission === "granted";

  return (
    <div className="card stack">
      <h3>{t("push.title")}</h3>
      <p className="muted">{t("push.intro")}</p>

      {!supported && !needsInstall && <p className="muted">{t("push.unsupported")}</p>}
      {needsInstall && <p>{t("push.iosInstall")}</p>}
      {supported && serverKey === "" && <p className="muted">{t("push.serverDisabled")}</p>}
      {supported && permission === "denied" && <p>{t("push.denied")}</p>}

      {supported && !!serverKey && permission !== "denied" && (
        <>
          <div className="row-between">
            <strong>{active ? t("push.statusOn") : t("push.statusOff")}</strong>
            {active ? (
              <button className="btn" disabled={busy} onClick={disable}>
                {t("push.disable")}
              </button>
            ) : (
              <button className="btn btn-primary" disabled={busy} onClick={enable}>
                {t("push.enable")}
              </button>
            )}
          </div>

          {active && (
            <>
              <span className="muted">{t("push.events")}</span>
              <label className="check">
                <input type="checkbox" checked={prefs.notify_mail} disabled={busy} onChange={(e) => setPref("notify_mail", e.target.checked)} />
                {t("push.eventMail")}
              </label>
              <label className="check">
                <input type="checkbox" checked={prefs.notify_chat} disabled={busy} onChange={(e) => setPref("notify_chat", e.target.checked)} />
                {t("push.eventChat")}
              </label>
              <label className="check">
                <input type="checkbox" checked={prefs.notify_other} disabled={busy} onChange={(e) => setPref("notify_other", e.target.checked)} />
                {t("push.eventOther")}
              </label>
              <label className="check">
                <input type="checkbox" checked={prefs.show_previews} disabled={busy} onChange={(e) => setPref("show_previews", e.target.checked)} />
                {t("push.previews")}
              </label>
              <span className="muted">{t("push.previewsHint")}</span>
              <div>
                <button className="btn" disabled={busy} onClick={sendTest}>
                  {t("push.test")}
                </button>
              </div>
            </>
          )}
        </>
      )}

      {serverKey !== null && (
        <span className="muted">{devices.length ? t("push.devices", { n: devices.length }) : t("push.noDevices")}</span>
      )}

      {canInstall && !isStandalone() && (
        <div className="row-between">
          <span className="muted">{t("push.installHint")}</span>
          <button className="btn" disabled={busy} onClick={install}>
            {t("push.install")}
          </button>
        </div>
      )}
    </div>
  );
}
