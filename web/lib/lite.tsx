"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { api, ApiError, errorText, refreshSession, setDeviceSigner } from "./api";
import { dequeue, enqueue, listQueue, loadDevice, sign, type DeviceInfo, type QueuedPhoto } from "./device";
import type { Me } from "./types";

type LiteState = {
  status: "loading" | "ready" | "no-device" | "signed-out";
  me: Me | null;
  device: DeviceInfo | null;
  online: boolean;
  queue: QueuedPhoto[];
  syncing: boolean;
  sync: () => Promise<void>;
  refreshQueue: () => Promise<void>;
};

const LiteContext = createContext<LiteState | null>(null);

export function useLite(): LiteState {
  const ctx = useContext(LiteContext);
  if (!ctx) throw new Error("useLite вне LiteProvider");
  return ctx;
}

export function installSigner(device: DeviceInfo) {
  setDeviceSigner(async () => {
    const ts = String(Date.now());
    return { "X-Device-Timestamp": ts, "X-Device-Signature": await sign(device.keys, `refresh:${ts}`) };
  });
}

async function uploadOne(item: QueuedPhoto): Promise<void> {
  const form = new FormData();
  form.append("file", item.blob, `${item.id}.${item.blob.type === "image/webp" ? "webp" : "jpg"}`);
  form.append("signature", item.signature);
  if (item.challengeId) form.append("challenge_id", item.challengeId);
  if (item.checklistItemId) form.append("checklist_item_id", item.checklistItemId);
  form.append("captured_client_at", item.capturedAt);
  if (item.elapsedMs !== null) form.append("elapsed_ms", String(Math.round(item.elapsedMs)));
  if (item.geo) {
    form.append("geo_lat", item.geo.lat.toFixed(6));
    form.append("geo_lon", item.geo.lon.toFixed(6));
    form.append("geo_accuracy_m", String(Math.round(item.geo.accuracy)));
  }
  // Один и тот же ключ при повторах: если ответ потерялся, второй копии не будет.
  await api(`/stages/${item.stageId}/media`, { method: "POST", form, idempotencyKey: item.id });
}

export function LiteProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<LiteState["status"]>("loading");
  const [me, setMe] = useState<Me | null>(null);
  const [device, setDevice] = useState<DeviceInfo | null>(null);
  const [online, setOnline] = useState(true);
  const [queue, setQueue] = useState<QueuedPhoto[]>([]);
  const [syncing, setSyncing] = useState(false);
  const busy = useRef(false);

  const refreshQueue = useCallback(async () => setQueue(await listQueue()), []);

  const sync = useCallback(async () => {
    if (busy.current || !navigator.onLine) return;
    busy.current = true;
    setSyncing(true);
    try {
      for (const item of await listQueue()) {
        try {
          await uploadOne(item);
          await dequeue(item.id);
        } catch (err) {
          if (err instanceof ApiError && err.problem.status === 0) break; // нет связи — попробуем позже
          if (err instanceof ApiError && err.problem.status === 401) break;
          await enqueue({ ...item, error: errorText(err) });
        }
      }
    } finally {
      busy.current = false;
      setSyncing(false);
      await refreshQueue();
    }
  }, [refreshQueue]);

  useEffect(() => {
    (async () => {
      const saved = await loadDevice().catch(() => null);
      if (!saved) {
        setStatus("no-device");
        return;
      }
      setDevice(saved);
      installSigner(saved);
      await refreshQueue();
      if (!(await refreshSession())) {
        // Без связи показываем приложение: фото можно снимать, они уйдут позже.
        setStatus(navigator.onLine ? "signed-out" : "ready");
        return;
      }
      setMe(await api<Me>("/me").catch(() => null));
      setStatus("ready");
      void sync();
    })();
  }, [refreshQueue, sync]);

  useEffect(() => {
    setOnline(navigator.onLine);
    const on = () => {
      setOnline(true);
      void sync();
    };
    const off = () => setOnline(false);
    window.addEventListener("online", on);
    window.addEventListener("offline", off);
    const timer = setInterval(() => void sync(), 60_000);
    if ("serviceWorker" in navigator) void navigator.serviceWorker.register("/sw.js").catch(() => undefined);
    return () => {
      window.removeEventListener("online", on);
      window.removeEventListener("offline", off);
      clearInterval(timer);
    };
  }, [sync]);

  return (
    <LiteContext.Provider value={{ status, me, device, online, queue, syncing, sync, refreshQueue }}>{children}</LiteContext.Provider>
  );
}
