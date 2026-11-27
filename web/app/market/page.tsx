"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { formatDate, parseRub, rub, STAGE_TYPE_LABELS } from "@/lib/format";
import type { MarketTask } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../components";

/** Заявки заказчиков для подрядчиков: без точного адреса, с откликом по цене и срокам. */
export default function MarketPage() {
  const { me, loading } = useRequireAuth();
  const [items, setItems] = useState<MarketTask[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const org = me?.memberships.find((m) => m.org_type === "contractor" && m.role === "contractor_admin");

  const load = useCallback(async () => {
    try {
      setItems(await api<MarketTask[]>("/market/tasks"));
    } catch (err) {
      setError(errorText(err));
    }
  }, []);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  return (
    <Shell>
      <h1>Заявки заказчиков</h1>
      <p className="muted">
        Заказчики сами выбирают подрядчика по откликам. Точный адрес вы увидите, когда вас выберут.
        {org && (
          <>
            {" "}
            Заказчики смотрят ваш <Link href={`/contractors/${org.org_id}`}>профиль и портфолио</Link>.
          </>
        )}
      </p>
      <ErrorNote text={error} />
      {items === null ? (
        error ? null : <Loading />
      ) : items.length === 0 ? (
        <section className="card empty">
          <p>Открытых заявок нет. Когда заказчик опубликует заявку с вашими видами работ, придёт СМС.</p>
        </section>
      ) : (
        <ul className="cards">
          {items.map((t) => (
            <li key={t.id} className="card">
              <TaskCard task={t} onChanged={load} />
            </li>
          ))}
        </ul>
      )}
    </Shell>
  );
}

function TaskCard({ task, onChanged }: { task: MarketTask; onChanged: () => Promise<void> }) {
  const mine = task.my_bid && task.my_bid.status !== "withdrawn" ? task.my_bid : null;
  const [open, setOpen] = useState(false);
  const [price, setPrice] = useState(mine ? String(mine.price_kop / 100) : "");
  const [days, setDays] = useState(mine ? String(mine.days) : "");
  const [start, setStart] = useState(mine?.start_date ?? "");
  const [message, setMessage] = useState(mine?.message ?? "");
  const [error, setError] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const kop = parseRub(price);
    if (!kop) {
      setError("Укажите цену работ в рублях");
      return;
    }
    try {
      await api(`/market-tasks/${task.id}/bids`, {
        method: "POST",
        body: { price_kop: kop, days: Number(days), start_date: start || null, message: message || null },
      });
      setOpen(false);
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function withdraw() {
    if (!mine) return;
    try {
      await api(`/market-bids/${mine.id}/withdraw`, { method: "POST" });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <>
      <b>{task.title}</b>
      <div className="muted small">
        {task.area_hint}
        {task.area_m2 && ` · ${task.area_m2} м²`} · опубликована {formatDate(task.created_at)} · откликов {task.bids_count}
      </div>
      {task.work_types.length > 0 && <div className="small">{task.work_types.map((w) => STAGE_TYPE_LABELS[w] ?? w).join(", ")}</div>}
      {task.budget_kop && <div className="small">Бюджет заказчика: {rub(task.budget_kop)}</div>}
      {task.desired_start && <div className="small">Начать: {new Date(task.desired_start).toLocaleDateString("ru-RU")}</div>}
      {task.description && <p className="pre small">{task.description}</p>}
      {mine && !open && (
        <p className="ok">
          Ваш отклик: {rub(mine.price_kop)}, {mine.days} дн.
        </p>
      )}
      <ErrorNote text={error} />
      {open ? (
        <form onSubmit={submit} className="boxed">
          <label htmlFor={`bp-${task.id}`}>Цена работ, ₽</label>
          <input id={`bp-${task.id}`} inputMode="numeric" value={price} onChange={(e) => setPrice(e.target.value)} />
          <label htmlFor={`bd-${task.id}`}>Срок, рабочих дней</label>
          <input id={`bd-${task.id}`} inputMode="numeric" value={days} onChange={(e) => setDays(e.target.value.replace(/\D/g, ""))} />
          <label htmlFor={`bs-${task.id}`}>Можем начать (необязательно)</label>
          <input id={`bs-${task.id}`} type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          <label htmlFor={`bm-${task.id}`}>Сообщение заказчику (необязательно)</label>
          <textarea id={`bm-${task.id}`} rows={3} maxLength={2000} value={message} onChange={(e) => setMessage(e.target.value)} />
          <div className="row">
            <button type="submit" className="primary" disabled={!price || !days}>
              {mine ? "Обновить отклик" : "Откликнуться"}
            </button>
            <button type="button" className="link" onClick={() => setOpen(false)}>
              Отмена
            </button>
          </div>
        </form>
      ) : (
        <div className="row">
          <button type="button" className={mine ? "secondary" : "primary"} onClick={() => setOpen(true)}>
            {mine ? "Изменить отклик" : "Откликнуться"}
          </button>
          {mine && (
            <button type="button" className="link" onClick={() => void withdraw()}>
              Отозвать
            </button>
          )}
        </div>
      )}
    </>
  );
}
