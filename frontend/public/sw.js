/* Service worker BlackCloud: Web Push і мінімальний офлайн-режим (лише оболонка застосунку).
   API (/api/), WebDAV і адмінка ніколи не кешуються. */
"use strict";

const VERSION = "v1";
const SHELL_CACHE = `bc-shell-${VERSION}`;
const ASSETS_CACHE = `bc-assets-${VERSION}`;
const MAX_ASSETS = 80;
const NO_FALLBACK = ["/api/", "/dav/", "/django-static/", "/.well-known/"];

self.addEventListener("install", (event) => {
  event.waitUntil(refreshShell().catch(() => undefined).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k.startsWith("bc-") && k !== SHELL_CACHE && k !== ASSETS_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

async function refreshShell() {
  const response = await fetch("/index.html", { cache: "no-store", credentials: "same-origin" });
  if (response.ok) await (await caches.open(SHELL_CACHE)).put("/index.html", response);
}

async function trimAssets() {
  const cache = await caches.open(ASSETS_CACHE);
  const keys = await cache.keys();
  for (const key of keys.slice(0, Math.max(0, keys.length - MAX_ASSETS))) await cache.delete(key);
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Хешовані бандли незмінні: спершу кеш
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.open(ASSETS_CACHE).then(async (cache) => {
        const hit = await cache.match(request);
        if (hit) return hit;
        const response = await fetch(request);
        if (response.ok && response.type === "basic") {
          await cache.put(request, response.clone());
          trimAssets();
        }
        return response;
      }),
    );
    return;
  }

  // Сторінки: спершу мережа; без мережі — збережена оболонка застосунку
  if (request.mode === "navigate" && !NO_FALLBACK.some((p) => url.pathname.startsWith(p))) {
    event.respondWith(
      fetch(request)
        .then((response) => {
          event.waitUntil(refreshShell().catch(() => undefined));
          return response;
        })
        .catch(async () => (await caches.match("/index.html", { cacheName: SHELL_CACHE })) || Response.error()),
    );
  }
});

// ─────────────── Push ───────────────

async function isApplePush() {
  const sub = await self.registration.pushManager.getSubscription();
  return !!sub && new URL(sub.endpoint).hostname.endsWith("push.apple.com");
}

async function handlePush(data) {
  const kind = data.kind || "other";
  if (kind === "chat" || kind === "mail") {
    const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    const active = windows.some((c) => c.focused && c.visibilityState === "visible");
    // Застосунок відкритий і в фокусі — він сам покаже сповіщення. Safari вимагає показувати
    // кожен push (інакше відкликає підписку), тож там — тихе сповіщення.
    if (active && !(await isApplePush())) return;
    if (active) {
      return self.registration.showNotification(data.title || "BlackCloud", notificationOptions(data, true));
    }
  }
  return self.registration.showNotification(data.title || "BlackCloud", notificationOptions(data, false));
}

function notificationOptions(data, silent) {
  return {
    body: data.body || "",
    tag: data.tag || undefined,
    renotify: !!data.tag && !silent,
    silent,
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    lang: self.navigator.language,
    data: { url: typeof data.url === "string" && data.url.startsWith("/") && !data.url.startsWith("//") ? data.url : "/" },
  };
}

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "BlackCloud", body: "" };
  }
  event.waitUntil(handlePush(data));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || "/", self.location.origin).href;
  event.waitUntil(
    (async () => {
      const windows = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
      const client = windows.find((c) => new URL(c.url).origin === self.location.origin);
      if (client) {
        await client.focus();
        if (client.url !== target && "navigate" in client) {
          try {
            await client.navigate(target);
          } catch (e) {
            /* вкладка не під контролем цього service worker — лишаємо як є */
          }
        }
        return;
      }
      await self.clients.openWindow(target);
    })(),
  );
});

// Браузер оновив підписку (зміна ключів push-сервісу) — переносимо її на сервер
self.addEventListener("pushsubscriptionchange", (event) => {
  event.waitUntil(
    (async () => {
      const old = event.oldSubscription;
      const key = old && old.options ? old.options.applicationServerKey : null;
      const sub = event.newSubscription || (key ? await self.registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key }) : null);
      if (!sub) return;
      const csrf = await fetch("/api/auth/csrf/", { credentials: "same-origin" }).then((r) => r.json());
      const json = sub.toJSON();
      const body = { endpoint: json.endpoint, p256dh: json.keys.p256dh, auth: json.keys.auth };
      // replaces — сервер перенесе налаштування подій і видалить стару підписку
      if (old && old.endpoint !== json.endpoint) body.replaces = old.endpoint;
      await fetch("/api/push/subscriptions/", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrf.csrfToken },
        body: JSON.stringify(body),
      });
    })().catch(() => undefined),
  );
});
