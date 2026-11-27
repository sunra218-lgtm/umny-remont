"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { api, errorText, setAccessToken } from "@/lib/api";
import { createKeys, saveDevice } from "@/lib/device";
import { installSigner } from "@/lib/lite";

type Exchange = { access_token: string; full_name: string | null; object_id: string | null };

/** Вход бригады по ссылке из СМС: телефон создаёт свой ключ, и дальше вход не нужен. */
export default function LinkLoginPage() {
  const { token } = useParams<{ token: string }>();
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        if (!window.isSecureContext || !crypto.subtle) {
          throw new Error("insecure");
        }
        const { keys, publicKey } = await createKeys();
        const out = await api<Exchange>(`/auth/links/${encodeURIComponent(token)}/exchange`, {
          method: "POST",
          body: { public_key: publicKey },
        });
        const device = { keys, publicKey, fullName: out.full_name, objectId: out.object_id };
        await saveDevice(device);
        installSigner(device);
        setAccessToken(out.access_token);
        router.replace("/lite");
      } catch (err) {
        setError(
          err instanceof Error && err.message === "insecure"
            ? "Откройте ссылку в обычном браузере телефона (Chrome или Safari)."
            : errorText(err),
        );
      }
    })();
  }, [token, router]);

  return (
    <main className="container lite">
      <h1>Умный ремонт для бригады</h1>
      {error ? (
        <>
          <p className="error" role="alert">
            {error}
          </p>
          <p className="muted">Ссылка работает один раз и 30 минут. Попросите руководителя прислать новую.</p>
        </>
      ) : (
        <p className="muted" aria-busy="true">
          Привязываем этот телефон…
        </p>
      )}
    </main>
  );
}
