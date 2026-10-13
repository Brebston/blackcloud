/* Service worker BlackCloud: Web Push, мінімальний офлайн-режим (лише оболонка застосунку)
   і «Поділитися → BlackCloud» (Web Share Target, Android).
   API (/api/), WebDAV і адмінка ніколи не кешуються. */
"use strict";

const VERSION = "v2";
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
      .then(() => purgeShares().catch(() => undefined))
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
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.method === "POST" && url.pathname === SHARE_PATH) {
    event.respondWith(handleShare(request));
    return;
  }
  if (request.method !== "GET") return;

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

// ─────────────── Поділитися → BlackCloud (Web Share Target) ───────────────
// Android передає файли/текст POST-запитом на /share-target (manifest.webmanifest → share_target).
// Дані кладемо в IndexedDB під одноразовим id (сторінка /files?share=<id> читає й видаляє їх після
// завантаження, див. src/lib/shareInbox.ts); усе старше за годину видаляється. Нічого не йде на сервер,
// поки користувач не підтвердить збереження у вже відкритій сесії.

const SHARE_PATH = "/share-target";
const SHARE_DB = "bc-share";
const SHARE_STORE = "shares";
const SHARE_MAX_AGE_MS = 60 * 60 * 1000;
const SHARE_MAX_FILES = 100;

function openShareDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(SHARE_DB, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(SHARE_STORE)) req.result.createObjectStore(SHARE_STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function shareTx(mode, fn) {
  const db = await openShareDb();
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(SHARE_STORE, mode);
      const result = fn(tx.objectStore(SHARE_STORE));
      tx.oncomplete = () => resolve(result && "result" in result ? result.result : undefined);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

/** Видаляє невикористані дані старші за годину. */
async function purgeShares(now = Date.now()) {
  await shareTx("readwrite", (store) => {
    const req = store.openCursor();
    req.onsuccess = () => {
      const cursor = req.result;
      if (!cursor) return;
      if (!(now - Number(cursor.value.created) <= SHARE_MAX_AGE_MS)) cursor.delete();
      cursor.continue();
    };
  });
}

const field = (form, name) => {
  const v = form.get(name);
  return typeof v === "string" ? v.slice(0, 100000) : "";
};

async function handleShare(request) {
  let target = "/files";
  try {
    const form = await request.formData();
    const files = form
      .getAll("files")
      .filter((f) => typeof f === "object" && f !== null && typeof f.size === "number" && f.size > 0)
      .slice(0, SHARE_MAX_FILES)
      .map((f) => ({ name: String(f.name || "shared").slice(0, 255), type: String(f.type || ""), blob: f }));
    const entry = {
      id: self.crypto.randomUUID(),
      created: Date.now(),
      title: field(form, "title"),
      text: field(form, "text"),
      url: field(form, "url"),
      files,
    };
    await purgeShares().catch(() => undefined);
    if (files.length || entry.title || entry.text || entry.url) {
      await shareTx("readwrite", (store) => store.put(entry));
      target = `/files?share=${entry.id}`;
    }
  } catch (e) {
    /* непридатні дані — просто відкриваємо «Файли» */
  }
  // 303: браузер відкриє сторінку GET-запитом (без повторного надсилання форми)
  return Response.redirect(new URL(target, self.location.origin).href, 303);
}

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
