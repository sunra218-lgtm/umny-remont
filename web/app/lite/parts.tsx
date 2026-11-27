"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useLite } from "@/lib/lite";

export function LiteHeader() {
  const { online, queue, syncing, device } = useLite();
  const waiting = queue.length;
  return (
    <header className="topbar">
      <Link href="/lite" className="brand">
        Бригада
      </Link>
      <div className="lite-status" aria-live="polite">
        {!online && <span className="badge warn">Нет связи</span>}
        {waiting > 0 && (
          <span className="badge">
            {syncing ? "Отправляем" : "В очереди"}: {waiting}
          </span>
        )}
        {device?.fullName && <span className="muted small">{device.fullName}</span>}
      </div>
    </header>
  );
}

/** Показывает содержимое, только если телефон привязан. */
export function LiteGate({ children }: { children: ReactNode }) {
  const { status } = useLite();
  if (status === "loading") return <div className="skeleton" aria-busy="true" aria-label="Загрузка" />;
  if (status === "no-device")
    return (
      <section className="card">
        <h1>Телефон не привязан</h1>
        <p>Попросите руководителя прислать ссылку для входа и откройте её на этом телефоне.</p>
      </section>
    );
  if (status === "signed-out")
    return (
      <section className="card">
        <h1>Вход завершён</h1>
        <p>Руководитель завершил сеанс на этом телефоне или он истёк. Попросите прислать новую ссылку для входа.</p>
      </section>
    );
  return <>{children}</>;
}

export function cacheGet<T>(key: string): T | null {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export function cacheSet(key: string, value: unknown) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // Память браузера недоступна — работаем без запоминания.
  }
}
