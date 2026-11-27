"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { PhoneCameraStatus } from "@/lib/types";

const KEY = "camera:token";
const MAX_SIDE = 1280;
const MAX_BYTES = 1_800_000;
const IDLE_CHECK_MS = 60_000;

function readToken(): string | null {
  const m = /[#&]t=([A-Za-z0-9_-]+)/.exec(window.location.hash);
  try {
    if (m) {
      localStorage.setItem(KEY, m[1]);
      // Убираем ключ из адресной строки, чтобы его не было видно на экране.
      history.replaceState(null, "", window.location.pathname);
      return m[1];
    }
    return localStorage.getItem(KEY);
  } catch {
    return m ? m[1] : null;
  }
}

type WakeLockLike = { release: () => Promise<void> };

/** Старый телефон как камера на объекте: сам делает кадр раз в N минут в рабочие часы,
 * пока заказчик не выключит камеру. Вход не нужен — ключ в ссылке из карточки объекта. */
export default function PhoneCameraPage() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const timer = useRef<number | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<PhoneCameraStatus | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [cameraReady, setCameraReady] = useState(false);
  const [dim, setDim] = useState(false);
  const [lastShot, setLastShot] = useState<Date | null>(null);

  useEffect(() => setToken(readToken() ?? ""), []);

  const call = useCallback(
    async (path: string, init: RequestInit = {}): Promise<PhoneCameraStatus | null> => {
      if (!token) return null;
      const headers: Record<string, string> = { "X-Camera-Token": token, Accept: "application/json" };
      if (init.method === "POST") headers["Idempotency-Key"] = crypto.randomUUID();
      try {
        const resp = await fetch(`/api/v1${path}`, { ...init, headers });
        if (resp.ok) {
          setProblem(null);
          return (await resp.json()) as PhoneCameraStatus;
        }
        const body = (await resp.json().catch(() => ({}))) as { title?: string; detail?: string };
        if (resp.status === 401) {
          setProblem("Ссылка камеры больше не действует. Попросите новую ссылку в карточке объекта.");
          return null;
        }
        // Камеру выключили, нерабочие часы, рано — это не ошибка, состояние обновится ниже.
        if (resp.status !== 409 && resp.status !== 429) setProblem(body.title ?? "Не удалось отправить кадр");
        return null;
      } catch {
        setProblem("Нет связи с сервером. Камера продолжит, когда связь появится.");
        return null;
      }
    },
    [token],
  );

  // Камера телефона.
  useEffect(() => {
    if (!token) return;
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
          setCameraReady(true);
        }
      } catch {
        setProblem("Нет доступа к камере. Разрешите доступ к камере для этого сайта в настройках браузера.");
      }
    })();
    return () => stream?.getTracks().forEach((t) => t.stop());
  }, [token]);

  // Экран не гаснет, пока страница открыта.
  useEffect(() => {
    let lock: WakeLockLike | null = null;
    const nav = navigator as Navigator & { wakeLock?: { request: (t: "screen") => Promise<WakeLockLike> } };
    const take = async () => {
      try {
        if (nav.wakeLock && document.visibilityState === "visible") lock = await nav.wakeLock.request("screen");
      } catch {
        /* не поддерживается: экран может погаснуть, подсказка внизу страницы */
      }
    };
    void take();
    const onVisible = () => void take();
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      void lock?.release();
    };
  }, []);

  const shoot = useCallback(async (): Promise<PhoneCameraStatus | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    const blob = await frameToBlob(video);
    const form = new FormData();
    form.append("photo", blob, blob.type === "image/webp" ? "frame.webp" : "frame.jpg");
    const st = await call("/camera/frames", { method: "POST", body: form });
    if (st) setLastShot(new Date());
    return st;
  }, [call]);

  // Цикл: узнать состояние → если пора, снять → ждать до следующего кадра.
  useEffect(() => {
    if (!token || !cameraReady) return;
    let stopped = false;
    const tick = async () => {
      let st = await call("/camera/status");
      if (st && (st.state === "live" || st.state === "no_signal") && st.next_in_s === 0) {
        st = (await shoot()) ?? (await call("/camera/status"));
      }
      if (stopped) return;
      if (st) setStatus(st);
      const wait =
        st && (st.state === "live" || st.state === "no_signal")
          ? Math.max(5_000, Math.min(st.next_in_s * 1000 + 1_000, IDLE_CHECK_MS * 5))
          : IDLE_CHECK_MS;
      timer.current = window.setTimeout(() => void tick(), wait);
    };
    void tick();
    return () => {
      stopped = true;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [token, cameraReady, call, shoot]);

  if (token === "") {
    return (
      <main className="container">
        <h1>Камера объекта</h1>
        <p>Откройте на этом телефоне ссылку камеры из карточки объекта.</p>
      </main>
    );
  }

  const active = status && (status.state === "live" || status.state === "no_signal");
  return (
    <main className="container phone-camera">
      <h1>{status ? `Камера: ${status.title}` : "Камера объекта"}</h1>
      <div className="camera">
        <video ref={videoRef} playsInline muted aria-label="Что видит камера" />
        <span className="camera-code">{active ? "● Снимает" : "Пауза"}</span>
      </div>
      {problem && <p className="error">{problem}</p>}
      {status && (
        <div className="boxed">
          <p>
            <b>{status.message}</b>
          </p>
          <p className="muted small">
            Кадр раз в {status.interval_min} мин, с {status.hour_from}:00 до {status.hour_to}:00 по Москве. Сегодня кадров:{" "}
            {status.frames_today}.
            {lastShot && ` Последний: ${lastShot.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Moscow" })}.`}
          </p>
        </div>
      )}
      <p className="muted small">
        Поставьте телефон на зарядку и не закрывайте эту страницу. Звук не записывается. Включает и выключает камеру
        заказчик в своём кабинете.
      </p>
      <button type="button" className="secondary" onClick={() => setDim(true)}>
        Затемнить экран
      </button>
      {dim && (
        <button type="button" className="dim-overlay" onClick={() => setDim(false)} aria-label="Показать экран">
          Камера работает. Коснитесь, чтобы показать экран.
        </button>
      )}
    </main>
  );
}

async function frameToBlob(video: HTMLVideoElement): Promise<Blob> {
  const scale = Math.min(1, MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("Камера недоступна");
  ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
  for (const q of [0.8, 0.65, 0.5]) {
    const webp = await toBlob(canvas, "image/webp", q);
    const blob = webp && webp.type === "image/webp" ? webp : await toBlob(canvas, "image/jpeg", q);
    if (blob && blob.size <= MAX_BYTES) return blob;
  }
  const last = await toBlob(canvas, "image/jpeg", 0.4);
  if (!last) throw new Error("Не удалось сохранить кадр");
  return last;
}

function toBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}
