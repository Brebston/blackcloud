// «Поділитися → BlackCloud» (Web Share Target, Android). Service worker (public/sw.js) кладе отримані
// файли й текст в IndexedDB під одноразовим id і перенаправляє на /files?share=<id>; тут їх читаємо.
// Схема бази має збігатися з sw.js. Записи старші за годину видаляються (і тут, і в service worker).

const DB_NAME = "bc-share";
const STORE = "shares";
export const SHARE_MAX_AGE_MS = 60 * 60 * 1000;

export interface SharedFile {
  name: string;
  type: string;
  blob: Blob;
}

export interface SharedEntry {
  id: string;
  created: number;
  title: string;
  text: string;
  url: string;
  files: SharedFile[];
}

export const isShareId = (id: string) => /^[A-Za-z0-9-]{8,64}$/.test(id);

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      if (!req.result.objectStoreNames.contains(STORE)) req.result.createObjectStore(STORE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function withStore<T>(mode: IDBTransactionMode, fn: (store: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const req = fn(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(req.result);
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  } finally {
    db.close();
  }
}

/** Отримані дані або null, якщо їх уже використано чи минуло більше години. */
export async function readShare(id: string): Promise<SharedEntry | null> {
  if (!isShareId(id) || typeof indexedDB === "undefined") return null;
  const entry = (await withStore("readonly", (s) => s.get(id))) as SharedEntry | undefined;
  if (!entry) return null;
  if (Date.now() - entry.created > SHARE_MAX_AGE_MS) {
    await deleteShare(id);
    return null;
  }
  return entry;
}

export async function deleteShare(id: string): Promise<void> {
  if (!isShareId(id) || typeof indexedDB === "undefined") return;
  await withStore("readwrite", (s) => s.delete(id)).catch(() => undefined);
}
