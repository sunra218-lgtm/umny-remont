"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, OBJECT_STATUS_LABELS, personName } from "@/lib/format";
import type { Page, RepairObject } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";

export default function ObjectsPage() {
  const { me, loading } = useRequireAuth();
  const [objects, setObjects] = useState<RepairObject[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [address, setAddress] = useState("");
  const [area, setArea] = useState("");
  const [customerPhone, setCustomerPhone] = useState("");
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setObjects((await api<Page<RepairObject>>("/objects?limit=200")).items);
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  const isStaff = me.memberships.length > 0;

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const obj = await api<RepairObject>("/objects", {
        method: "POST",
        body: {
          address_text: address,
          area_m2: area ? area.replace(",", ".") : null,
          customer_phone: me?.is_platform_admin && customerPhone ? customerPhone : null,
        },
      });
      location.assign(`/objects/${obj.id}`);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <Shell>
      <div className="row between">
        <h1>{isStaff ? "Объекты" : "Мои квартиры"}</h1>
        {!creating && (me.is_platform_admin || !isStaff) && (
          <button className="primary small" onClick={() => setCreating(true)}>
            Добавить квартиру
          </button>
        )}
      </div>
      {creating && (
        <section className="card">
          <form onSubmit={create} className="grid2">
            <div style={{ gridColumn: "1 / -1" }}>
              <label htmlFor="addr">Адрес квартиры</label>
              <input id="addr" value={address} onChange={(e) => setAddress(e.target.value)} placeholder="Москва, ул. Примерная, д. 1, кв. 10" autoFocus />
            </div>
            <div>
              <label htmlFor="area">Площадь, м² (необязательно)</label>
              <input id="area" inputMode="decimal" value={area} onChange={(e) => setArea(e.target.value)} placeholder="54,3" />
            </div>
            {me.is_platform_admin && (
              <div>
                <label htmlFor="cphone">Телефон заказчика</label>
                <input id="cphone" type="tel" value={customerPhone} onChange={(e) => setCustomerPhone(e.target.value)} placeholder="Если создаёте за заказчика" />
              </div>
            )}
            <div className="row" style={{ gridColumn: "1 / -1" }}>
              <button type="submit" className="primary" disabled={address.trim().length < 5}>
                Сохранить квартиру
              </button>
              <button type="button" className="secondary" onClick={() => setCreating(false)}>
                Отменить
              </button>
            </div>
          </form>
          <ErrorNote text={error} />
        </section>
      )}
      {!creating && <ErrorNote text={error} />}
      {objects === null ? (
        <Loading />
      ) : objects.length === 0 ? (
        <section className="card empty">
          {isStaff ? (
            <p>Пока нет объектов, на которые вас назначили.</p>
          ) : (
            <>
              <p>Добавьте квартиру, чтобы заказать замер и получить смету.</p>
              {!creating && (
                <button className="primary" onClick={() => setCreating(true)}>
                  Добавить квартиру
                </button>
              )}
            </>
          )}
        </section>
      ) : (
        <ul className="cards">
          {objects.map((o) => (
            <li key={o.id}>
              <Link href={`/objects/${o.id}`} className="card linkcard">
                <div className="row between">
                  <b>{o.address_text}</b>
                  <span className={`status status-${o.status}`}>{OBJECT_STATUS_LABELS[o.status]}</span>
                </div>
                <div className="muted">
                  {o.area_m2 ? `${o.area_m2.replace(".", ",")} м² · ` : ""}
                  {isStaff && o.customer ? `Заказчик: ${personName(o.customer)} · ` : ""}
                  {o.contractor_name ?? "Подрядчик не назначен"} · добавлен {formatDate(o.created_at)}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}
