"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { PAYMENT_STATUS_LABELS, rub } from "@/lib/format";
import type { Payment } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../components";

function backUrl(p: Payment): string {
  return p.purchase_order_id ? `/objects/${p.object_id}#materials` : `/stages/${p.stage_id}`;
}

/** Тестовая страница оплаты: так выглядит переход в банк, пока настоящий банк не подключён. */
export default function FakePayPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { me, loading } = useRequireAuth();
  const [payment, setPayment] = useState<Payment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (me) api<Payment>(`/payments/${id}`).then(setPayment).catch((err) => setError(errorText(err)));
  }, [me, id]);

  async function pay(success: boolean) {
    setBusy(true);
    setError(null);
    try {
      const p = await api<Payment>(`/fake-bank/payments/${id}/pay`, { method: "POST", body: { success } });
      router.replace(backUrl(p));
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  if (loading || !me) return <Shell><Loading /></Shell>;
  return (
    <Shell>
      <h1>Оплата резерва</h1>
      <p className="devnote">Тестовый режим: банк ещё не подключён, деньги не списываются.</p>
      {!payment ? (
        error ? <ErrorNote text={error} /> : <Loading />
      ) : (
        <section className="card">
          <p className="muted">{payment.address}</p>
          <h2>
            {payment.purchase_order_id ? "Материалы для ремонта" : `Этап «${payment.stage_title}»`}: {rub(payment.amount_kop)}
          </h2>
          {payment.status === "pending_funding" ? (
            <div className="row">
              <button className="primary" disabled={busy} onClick={() => void pay(true)}>
                Оплатить {rub(payment.amount_kop)}
              </button>
              <button className="secondary" disabled={busy} onClick={() => void pay(false)}>
                Отменить оплату
              </button>
            </div>
          ) : (
            <p>
              {PAYMENT_STATUS_LABELS[payment.status]}.{" "}
              <a href={backUrl(payment)}>{payment.purchase_order_id ? "Вернуться к объекту" : "Вернуться к этапу"}</a>
            </p>
          )}
          <ErrorNote text={error} />
        </section>
      )}
    </Shell>
  );
}
