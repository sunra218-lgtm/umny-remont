// Телефон бригады: ключ устройства и очередь неотправленных фото в IndexedDB.
// Закрытый ключ создаётся неэкспортируемым: его нельзя вынуть из браузера и перенести на другой телефон.

const DB_NAME = "umny-lite";
const KV = "kv";
const QUEUE = "queue";

function openDb(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(KV)) db.createObjectStore(KV);
      if (!db.objectStoreNames.contains(QUEUE)) db.createObjectStore(QUEUE, { keyPath: "id" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

async function tx<T>(store: string, mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest<T>): Promise<T> {
  const db = await openDb();
  return new Promise((resolve, reject) => {
    const t = db.transaction(store, mode);
    const req = fn(t.objectStore(store));
    t.oncomplete = () => resolve(req.result);
    t.onerror = () => reject(t.error);
    t.onabort = () => reject(t.error);
  });
}

export type DeviceInfo = { keys: CryptoKeyPair; publicKey: string; fullName: string | null; objectId: string | null };

export async function loadDevice(): Promise<DeviceInfo | null> {
  return ((await tx(KV, "readonly", (s) => s.get("device"))) as DeviceInfo | undefined) ?? null;
}

export async function saveDevice(info: DeviceInfo): Promise<void> {
  await tx(KV, "readwrite", (s) => s.put(info, "device"));
}

export async function forgetDevice(): Promise<void> {
  await tx(KV, "readwrite", (s) => s.delete("device"));
}

function b64(buf: ArrayBuffer): string {
  let s = "";
  for (const byte of new Uint8Array(buf)) s += String.fromCharCode(byte);
  return btoa(s);
}

export async function createKeys(): Promise<{ keys: CryptoKeyPair; publicKey: string }> {
  const keys = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, false, ["sign", "verify"]);
  const spki = await crypto.subtle.exportKey("spki", keys.publicKey);
  return { keys, publicKey: b64(spki) };
}

/** Подпись в формате r||s (так её отдаёт WebCrypto), base64. */
export async function sign(keys: CryptoKeyPair, message: string): Promise<string> {
  const sig = await crypto.subtle.sign({ name: "ECDSA", hash: "SHA-256" }, keys.privateKey, new TextEncoder().encode(message));
  return b64(sig);
}

export async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const hash = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("");
}

// ---- Очередь фото ----

export type QueuedPhoto = {
  id: string;
  stageId: string;
  stageTitle: string;
  address: string;
  blob: Blob;
  signature: string;
  challengeId: string | null;
  capturedAt: string;
  elapsedMs: number | null;
  geo: { lat: number; lon: number; accuracy: number } | null;
  error: string | null;
};

export async function enqueue(item: QueuedPhoto): Promise<void> {
  await tx(QUEUE, "readwrite", (s) => s.put(item));
}

export async function listQueue(): Promise<QueuedPhoto[]> {
  const items = (await tx(QUEUE, "readonly", (s) => s.getAll())) as QueuedPhoto[];
  return items.sort((a, b) => a.capturedAt.localeCompare(b.capturedAt));
}

export async function dequeue(id: string): Promise<void> {
  await tx(QUEUE, "readwrite", (s) => s.delete(id));
}
