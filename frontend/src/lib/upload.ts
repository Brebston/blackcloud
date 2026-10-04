import { api, post } from "../api/client";

export interface UploadProgress {
  id: string;
  name: string;
  loaded: number;
  total: number;
  state: "uploading" | "done" | "error" | "cancelled";
  error?: string;
}

interface StartResponse {
  id: string;
  name: string;
  chunk_size: number;
  chunks_total: number;
  next_index: number;
}

/**
 * Чанкове завантаження: файл ріжеться на шматки по chunk_size і надсилається
 * послідовно. Кожен чанк шифрується на сервері окремо. Невдалий чанк
 * повторюється до 5 разів з експоненційною затримкою.
 */
export async function uploadFile(
  file: File,
  folder: string | null,
  onProgress: (p: UploadProgress) => void,
  signal?: AbortSignal,
): Promise<void> {
  const start = await post<StartResponse>("/api/files/uploads/", { name: file.name, size: file.size, folder });
  const progress: UploadProgress = { id: start.id, name: start.name, loaded: 0, total: file.size, state: "uploading" };
  onProgress({ ...progress });

  try {
    for (let index = start.next_index; index < start.chunks_total; index++) {
      if (signal?.aborted) throw new DOMException("cancelled", "AbortError");
      const blob = file.slice(index * start.chunk_size, (index + 1) * start.chunk_size);
      let attempt = 0;
      for (;;) {
        try {
          await api(`/api/files/uploads/${start.id}/chunks/${index}/`, {
            method: "PUT",
            body: blob,
            headers: { "Content-Type": "application/octet-stream" },
            signal,
          });
          break;
        } catch (e) {
          if (signal?.aborted || ++attempt > 5) throw e;
          await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
        }
      }
      progress.loaded = Math.min(file.size, (index + 1) * start.chunk_size);
      onProgress({ ...progress });
    }
    await post(`/api/files/uploads/${start.id}/complete/`);
    progress.state = "done";
    progress.loaded = file.size;
    onProgress({ ...progress });
  } catch (e) {
    const aborted = (e as Error)?.name === "AbortError";
    progress.state = aborted ? "cancelled" : "error";
    progress.error = (e as Error)?.message;
    onProgress({ ...progress });
    // Звільняємо зарезервоване місце
    api(`/api/files/uploads/${start.id}/`, { method: "DELETE" }).catch(() => {});
    if (!aborted) throw e;
  }
}
