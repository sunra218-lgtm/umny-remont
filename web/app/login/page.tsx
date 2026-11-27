"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError, errorText } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { formatPhone } from "@/lib/format";

import { ErrorNote, Header } from "../components";

type OtpSent = { phone: string; expires_in: number; resend_in: number; debug_code: string | null };
type Tokens = { access_token: string; is_new_user: boolean };

export default function LoginPage() {
  const router = useRouter();
  const { me, loading, signIn } = useAuth();
  const [step, setStep] = useState<"phone" | "code">("phone");
  const [phone, setPhone] = useState("");
  const [sent, setSent] = useState<OtpSent | null>(null);
  const [code, setCode] = useState("");
  const [consent, setConsent] = useState(false);
  const [marketing, setMarketing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [resendIn, setResendIn] = useState(0);

  useEffect(() => {
    if (!loading && me) router.replace(me.is_platform_admin ? "/admin" : "/cabinet");
  }, [loading, me, router]);

  useEffect(() => {
    if (resendIn <= 0) return;
    const t = setTimeout(() => setResendIn((s) => s - 1), 1000);
    return () => clearTimeout(t);
  }, [resendIn]);

  async function requestCode(e?: FormEvent) {
    e?.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const data = await api<OtpSent>("/auth/otp", { method: "POST", body: { phone } });
      setSent(data);
      setStep("code");
      setCode("");
      setResendIn(data.resend_in);
    } catch (err) {
      if (err instanceof ApiError && typeof err.problem.retry_after === "number") {
        setResendIn(err.problem.retry_after);
        if (sent) setStep("code");
      }
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function verify(e: FormEvent) {
    e.preventDefault();
    if (!sent) return;
    setError(null);
    setBusy(true);
    try {
      const data = await api<Tokens>("/auth/otp/verify", {
        method: "POST",
        body: { phone: sent.phone, code, accept_pd_processing: consent, accept_marketing: marketing },
      });
      await signIn(data.access_token);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <Header />
      <main className="container narrow">
        <section className="card">
          <h1>Вход</h1>
          {step === "phone" ? (
            <form onSubmit={requestCode} noValidate>
              <p className="muted">Пароль не нужен: пришлём код в СМС.</p>
              <label htmlFor="phone">Номер телефона</label>
              <input
                id="phone"
                type="tel"
                inputMode="tel"
                autoComplete="tel"
                placeholder="+7 900 000-00-00"
                value={phone}
                onChange={(e) => {
                  setPhone(e.target.value);
                  setError(null);
                }}
                autoFocus
              />
              <ErrorNote text={error} />
              <button type="submit" className="primary" disabled={busy || phone.replace(/\D/g, "").length < 10}>
                {busy ? "Отправляем…" : "Получить код"}
              </button>
            </form>
          ) : (
            <form onSubmit={verify} noValidate>
              <p className="muted">
                Код отправлен на {sent ? formatPhone(sent.phone) : ""}.{" "}
                <button type="button" className="link" onClick={() => setStep("phone")}>
                  Изменить номер
                </button>
              </p>
              {sent?.debug_code && (
                <p className="devnote">
                  Режим разработки: настоящие СМС не отправляются. Ваш код — <b>{sent.debug_code}</b>
                </p>
              )}
              <label htmlFor="code">Код из СМС</label>
              <input
                id="code"
                inputMode="numeric"
                autoComplete="one-time-code"
                maxLength={6}
                placeholder="6 цифр"
                value={code}
                onChange={(e) => {
                  setCode(e.target.value.replace(/\D/g, ""));
                  setError(null);
                }}
                autoFocus
              />
              <label className="check">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                <span>
                  Соглашаюсь на <a href="/legal/personal-data">обработку персональных данных</a>
                </span>
              </label>
              <label className="check">
                <input type="checkbox" checked={marketing} onChange={(e) => setMarketing(e.target.checked)} />
                <span>Хочу получать новости и предложения (необязательно)</span>
              </label>
              <ErrorNote text={error} />
              <button type="submit" className="primary" disabled={busy || code.length !== 6}>
                {busy ? "Проверяем…" : "Войти"}
              </button>
              <button type="button" className="secondary" disabled={busy || resendIn > 0} onClick={() => void requestCode()}>
                {resendIn > 0 ? `Отправить код снова через ${resendIn} с` : "Отправить код снова"}
              </button>
            </form>
          )}
        </section>
      </main>
    </>
  );
}
