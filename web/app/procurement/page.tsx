"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, REQUEST_STATUS_LABELS } from "@/lib/format";
import type { MaterialRequest, PurchaseOrder } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";
import { OrderCard } from "../orders";

/** Закупки: менеджер платформы видит заявки прорабов и все заказы, поставщик — свои заказы. */
export default function ProcurementPage() {
  const { me, loading } = useRequireAuth();
  const [orders, setOrders] = useState<PurchaseOrder[] | null>(null);
  const [requests, setRequests] = useState<MaterialRequest[]>([]);
  const [all, setAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const staff = Boolean(me && (me.is_platform_admin || me.memberships.some((m) => m.role === "procurement_manager")));

  const load = useCallback(async () => {
    setError(null);
    try {
      setOrders(await api<PurchaseOrder[]>(`/purchase-orders?active_only=${!all}`));
      if (staff) setRequests(await api<MaterialRequest[]>("/material-requests"));
    } catch (err) {
      setError(errorText(err));
      setOrders([]);
    }
  }, [all, staff]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  const replace = (o: PurchaseOrder) => setOrders((xs) => (xs ?? []).map((x) => (x.id === o.id ? o : x)));
  return (
    <Shell wide>
      <h1>Закупки</h1>
      <ErrorNote text={error} />
      <div className="split">
        <div>
          <section className="card">
            <div className="row between">
              <h2>{staff ? "Заказы материалов" : "Заказы для отгрузки"}</h2>
              <label className="check">
                <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} />
                Показать завершённые
              </label>
            </div>
            {orders === null ? (
              <Loading />
            ) : orders.length === 0 ? (
              <p className="muted">{staff ? "Активных заказов нет. Корзину собирают на странице объекта." : "Новых заказов нет."}</p>
            ) : (
              orders.map((o) => (
                <div key={o.id}>
                  {staff && (
                    <p className="small">
                      <Link href={`/objects/${o.object_id}#materials`}>{o.address}</Link>
                    </p>
                  )}
                  <OrderCard order={o} onChanged={replace} showAddress={!staff} />
                </div>
              ))
            )}
          </section>
        </div>
        {staff && (
          <div>
            <section className="card">
              <h2>Заявки прорабов</h2>
              {requests.length === 0 ? (
                <p className="muted">Новых заявок нет.</p>
              ) : (
                <ul className="list">
                  {requests.map((r) => (
                    <li key={r.id}>
                      <div>
                        <b>{r.text ?? "Голосовая заявка"}</b>
                        <div className="muted small">
                          {r.address} · {r.author_name}, {formatDate(r.created_at)} · {REQUEST_STATUS_LABELS[r.status]}
                        </div>
                        {r.audio_url && <audio controls preload="none" src={r.audio_url} />}
                      </div>
                      <Link className="button secondary" href={`/objects/${r.object_id}#materials`}>
                        Собрать корзину
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        )}
      </div>
    </Shell>
  );
}
