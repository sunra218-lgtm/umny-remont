"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";

import { api, errorText } from "@/lib/api";
import { enqueue, sha256Hex, sign } from "@/lib/device";
import { useLite } from "@/lib/lite";

import type { StageChecklist } from "@/lib/types";

import { cacheGet, cacheSet, LiteGate } from "../parts";

type Challenge = { challenge_id: string; code: string; ttl_s: number; receivedAt: number };
type Geo = { lat: number; lon: number; accuracy: number };

const MAX_SIDE = 1600;
const MAX_BYTES = 800 * 1024;
const SPARE_CODES = 3;

export default function CapturePage() {
  return (
    <LiteGate>
      <Suspense>
        <Capture />
      </Suspense>
    </LiteGate>
  );
}

function Capture() {
  const params = useSearchParams();
  const stageId = params.get("stage") ?? "";
  const stageTitle = params.get("title") ?? "Этап";
  const address = params.get("address") ?? "";
  const { device, sync, refreshQueue, online } = useLite();

  const videoRef = useRef<HTMLVideoElement>(null);
  const codes = useRef<Challenge[]>([]);
  const geo = useRef<Geo | null>(null);
  const [nextCode, setNextCode] = useState<string | null>(null);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [busy, setBusy] = useState(false);
  const [shots, setShots] = useState(0);
  const [geoState, setGeoState] = useState<"wait" | "ok" | "off">("wait");
  const [checklist, setChecklist] = useState<StageChecklist | null>(() => cacheGet(`lite:checklist:${stageId}`));
  const [itemId, setItemId] = useState<string | null>(null);

  useEffect(() => {
    if (!stageId) return;
    api<StageChecklist>(`/stages/${stageId}/checklist`)
      .then((c) => {
        setChecklist(c);
        cacheSet(`lite:checklist:${stageId}`, c);
      })
      .catch(() => undefined);
  }, [stageId]);

  useEffect(() => {
    // Сразу предлагаем первый несделанный обязательный пункт.
    if (checklist && itemId === null && checklist.items.length) setItemId(checklist.missing[0] ?? checklist.items[0].id);
  }, [checklist, itemId]);

  const item = checklist?.items.find((i) => i.id === itemId) ?? null;

  const fresh = (c: Challenge) => performance.now() - c.receivedAt < (c.ttl_s - 30) * 1000;

  const refill = useCallback(async () => {
    codes.current = codes.current.filter(fresh);
    try {
      while (codes.current.length < SPARE_CODES && navigator.onLine) {
        const c = await api<Omit<Challenge, "receivedAt">>(`/stages/${stageId}/capture-challenges`, { method: "POST" });
        codes.current.push({ ...c, receivedAt: performance.now() });
      }
    } catch (err) {
      if (codes.current.length === 0 && navigator.onLine) setError(errorText(err));
    }
    setNextCode(codes.current[0]?.code ?? null);
  }, [stageId]);

  useEffect(() => {
    void refill();
  }, [refill, online]);

  useEffect(() => {
    let stream: MediaStream | null = null;
    (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1440 } },
          audio: false,
        });
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          await videoRef.current.play();
          setReady(true);
        }
      } catch {
        setCameraError("Нет доступа к камере. Разрешите доступ к камере для этого сайта в настройках браузера.");
      }
    })();
    let watch: number | null = null;
    if ("geolocation" in navigator) {
      watch = navigator.geolocation.watchPosition(
        (p) => {
          geo.current = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy: p.coords.accuracy };
          setGeoState("ok");
        },
        () => setGeoState("off"),
        { enableHighAccuracy: true, maximumAge: 30_000, timeout: 20_000 },
      );
    } else setGeoState("off");
    return () => {
      stream?.getTracks().forEach((t) => t.stop());
      if (watch !== null) navigator.geolocation.clearWatch(watch);
    };
  }, []);

  async function shoot() {
    const video = videoRef.current;
    if (!video || !device || busy) return;
    setBusy(true);
    setError(null);
    try {
      codes.current = codes.current.filter(fresh);
      const challenge = codes.current.shift() ?? null;
      const now = new Date();
      const blob = await frameToBlob(video, [
        challenge ? `Код ${challenge.code}` : "Без кода (нет связи)",
        now.toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }),
        `${address} · ${stageTitle}${item ? ` · ${item.title}` : ""}`,
      ]);
      const sha = await sha256Hex(await blob.arrayBuffer());
      const signature = await sign(device.keys, `${sha}:${challenge?.code ?? ""}`);
      await enqueue({
        id: crypto.randomUUID(),
        stageId,
        stageTitle,
        address,
        blob,
        signature,
        challengeId: challenge?.challenge_id ?? null,
        capturedAt: now.toISOString(),
        elapsedMs: challenge ? performance.now() - challenge.receivedAt : null,
        geo: geo.current,
        checklistItemId: item?.id ?? null,
        error: null,
      });
      setShots((n) => n + 1);
      if (checklist && item) {
        // Отмечаем снятый пункт и переходим к следующему несделанному.
        const updated = {
          ...checklist,
          items: checklist.items.map((i) => (i.id === item.id ? { ...i, photos: i.photos + 1 } : i)),
          missing: checklist.missing.filter((id) => id !== item.id),
        };
        setChecklist(updated);
        cacheSet(`lite:checklist:${stageId}`, updated);
        if (updated.missing.length) setItemId(updated.missing[0]);
      }
      await refreshQueue();
      void sync();
      void refill();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!stageId) return <p className="error">Этап не выбран. Вернитесь к списку.</p>;
  return (
    <>
      <p>
        <Link href="/lite">← К этапам</Link>
      </p>
      <h1>{stageTitle}</h1>
      <p className="muted">{address}</p>
      {checklist && checklist.items.length > 0 && (
        <>
          <ul className="chips" aria-label="Что снимать">
            {checklist.items.map((i) => (
              <li key={i.id}>
                <button type="button" className="chip" aria-pressed={i.id === itemId} onClick={() => setItemId(i.id)}>
                  {i.photos > 0 && <span className="done">✓ </span>}
                  {i.title}
                </button>
              </li>
            ))}
            <li>
              <button type="button" className="chip" aria-pressed={itemId === ""} onClick={() => setItemId("")}>
                Другое
              </button>
            </li>
          </ul>
          {item?.required_angle && <p className="muted small">Как снять: {item.required_angle}</p>}
        </>
      )}
      {cameraError ? (
        <p className="error" role="alert">
          {cameraError}
        </p>
      ) : (
        <div className="camera">
          <video ref={videoRef} playsInline muted aria-label="Камера" />
          {nextCode && <div className="camera-code">Код {nextCode}</div>}
        </div>
      )}
      <div className="capture-bar">
        <button type="button" className="primary shutter" disabled={!ready || busy} onClick={() => void shoot()}>
          {busy ? "Сохраняем…" : "Сфотографировать"}
        </button>
        <p className="muted small">
          {shots > 0 ? `Снято: ${shots}. ` : ""}
          {geoState === "off" ? "Геолокация выключена — включите её, чтобы отчёт приняли без вопросов." : geoState === "wait" ? "Определяем место…" : "Место определено."}
          {!nextCode && " Нет связи: фото сохранится и отправится позже."}
        </p>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </>
  );
}

/** Кадр с камеры → WebP (или JPEG) не больше 800 КБ, с подписью внизу кадра. */
async function frameToBlob(video: HTMLVideoElement, lines: string[]): Promise<Blob> {
  const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
  const w = Math.round(video.videoWidth * scale);
  const h = Math.round(video.videoHeight * scale);
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Камера недоступна");
  ctx.drawImage(video, 0, 0, w, h);
  const size = Math.max(16, Math.round(w / 40));
  const pad = Math.round(size * 0.6);
  const band = lines.length * size * 1.3 + pad * 2;
  ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
  ctx.fillRect(0, h - band, w, band);
  ctx.fillStyle = "#fff";
  ctx.font = `600 ${size}px sans-serif`;
  ctx.textBaseline = "top";
  lines.forEach((line, i) => ctx.fillText(line, pad, h - band + pad + i * size * 1.3, w - pad * 2));

  for (const quality of [0.85, 0.75, 0.6, 0.45]) {
    const webp = await toBlob(canvas, "image/webp", quality);
    // Safari старых версий не умеет WebP и молча отдаёт PNG — тогда берём JPEG.
    const blob = webp && webp.type === "image/webp" ? webp : await toBlob(canvas, "image/jpeg", quality);
    if (blob && blob.size <= MAX_BYTES) return blob;
  }
  const last = await toBlob(canvas, "image/jpeg", 0.35);
  if (!last) throw new Error("Не удалось сохранить фото");
  return last;
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}
