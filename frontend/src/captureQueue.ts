import { ApiError, SignedOutError, api } from "./api";
import type { RemoteMode } from "./types";

/** A recording kept on this device until the relay confirms it (ADR 0006, 0009).
 *  `uid` is minted once, here, so every retry is the same capture: the relay overwrites
 *  and the PC collapses it into one session. */
export interface QueuedCapture {
  uid: string;
  mode: RemoteMode;
  topic: string | null;
  blob: Blob;
  filename: string;
  createdAt: string;
  attempts: number;
  lastError: string | null;
  nextTryAt: number;
  /** The server refused it outright (too big, bad mode). Retrying cannot help. */
  rejected: boolean;
}

const DB_NAME = "ect-captures";
const STORE = "captures";
const MAX_DELAY_MS = 30 * 60 * 1000;

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "uid" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function run<T>(mode: IDBTransactionMode, action: (store: IDBObjectStore) => IDBRequest<T>) {
  const db = await openDb();
  try {
    return await new Promise<T>((resolve, reject) => {
      const request = action(db.transaction(STORE, mode).objectStore(STORE));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } finally {
    db.close();
  }
}

export const listQueued = () => run("readonly", (s) => s.getAll() as IDBRequest<QueuedCapture[]>);
const save = (item: QueuedCapture) => run("readwrite", (s) => s.put(item));
export const discardQueued = (uid: string) => run("readwrite", (s) => s.delete(uid));

export async function enqueue(capture: {
  uid: string;
  mode: RemoteMode;
  topic: string | null;
  blob: Blob;
  filename: string;
}): Promise<QueuedCapture> {
  const item: QueuedCapture = {
    ...capture,
    createdAt: new Date().toISOString(),
    attempts: 0,
    lastError: null,
    nextTryAt: 0,
    rejected: false,
  };
  await save(item);
  return item;
}

/** Try to send one stored capture. It is deleted only after the relay says it has it. */
export async function sendQueued(item: QueuedCapture) {
  try {
    const result = await api.uploadToInbox(item);
    await discardQueued(item.uid);
    return result;
  } catch (err) {
    const attempts = item.attempts + 1;
    const message = err instanceof Error ? err.message : "Upload failed.";
    // 400/413 are the relay saying "never": a retry would only repeat it.
    const rejected = err instanceof ApiError && (err.status === 400 || err.status === 413);
    const delay = Math.min(MAX_DELAY_MS, 15_000 * 2 ** (attempts - 1));
    await save({ ...item, attempts, lastError: message, rejected, nextTryAt: Date.now() + delay });
    throw err;
  }
}

let draining = false;

/** Send everything that is due. `force` ignores the backoff delay (the browser's `online`
 *  event). Stops at a signed-out error: the rest would fail the
 *  same way, and the banner is already asking the user to sign in. */
export async function drainQueue(force = false): Promise<void> {
  if (draining) return;
  draining = true;
  try {
    for (const item of await listQueued()) {
      if (item.rejected) continue;
      if (!force && item.nextTryAt > Date.now()) continue;
      try {
        await sendQueued(item);
      } catch (err) {
        if (err instanceof SignedOutError) return;
      }
    }
  } finally {
    draining = false;
  }
}
