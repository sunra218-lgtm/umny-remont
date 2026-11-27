import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { LiteProvider } from "@/lib/lite";

import { LiteHeader } from "./parts";

export const metadata: Metadata = {
  title: "Умный ремонт — бригада",
  manifest: "/manifest.webmanifest",
  appleWebApp: { capable: true, title: "Бригада" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#1f6f50" };

export default function LiteLayout({ children }: { children: ReactNode }) {
  return (
    <LiteProvider>
      <LiteHeader />
      <main className="container lite">{children}</main>
    </LiteProvider>
  );
}
