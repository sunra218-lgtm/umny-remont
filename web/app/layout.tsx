import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/auth";

import "./globals.css";

export const metadata: Metadata = {
  title: "Умный ремонт",
  description: "Ремонт квартиры под контролем: смета, фотоотчёты, оплата по этапам",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ru">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
