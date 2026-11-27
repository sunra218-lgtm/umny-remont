"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { useAuth } from "@/lib/auth";

export function Header() {
  const { me, signOut } = useAuth();
  return (
    <header className="topbar">
      <Link href="/" className="brand">
        Умный ремонт
      </Link>
      {me && (
        <nav className="topnav">
          <Link href="/objects">Объекты</Link>
          {(me.is_platform_admin || me.memberships.some((m) => m.role === "inspector")) && <Link href="/inspections">Выезды</Link>}
          <Link href="/cabinet">Кабинет</Link>
          {me.is_platform_admin && <Link href="/admin">Админка</Link>}
          <button type="button" className="link" onClick={() => void signOut().then(() => location.assign("/login"))}>
            Выйти
          </button>
        </nav>
      )}
    </header>
  );
}

export function Shell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  return (
    <>
      <Header />
      <main className={wide ? "container wide" : "container"}>{children}</main>
    </>
  );
}

export function ErrorNote({ text }: { text: string | null }) {
  if (!text) return null;
  return (
    <p className="error" role="alert">
      {text}
    </p>
  );
}

export function Loading() {
  return <div className="skeleton" aria-busy="true" aria-label="Загрузка" />;
}
