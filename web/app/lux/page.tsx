"use client";

import Link from "next/link";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { OBJECT_STATUS_LABELS, parseRub, rub } from "@/lib/format";

import { ErrorNote, Loading, Shell } from "../components";
import {
  OrderTimeline,
  personLabel,
  type FurnitureOrder,
  type LogisticsEstimate,
  type LuxInfo,
  type LuxPerson,
} from "../lux";

type LuxObject = { id: string; address: string; status: string; manager: LuxPerson | null };

// Что менеджер делает дальше по каждому статусу.
const NEXT: Record<string, { status: string; label: string }[]> = {
  confirmed: [{ status: "paid", label: "Оплата получена" }],
  paid: [{ status: "ordered", label: "Заказано у фабрики" }],
  ordered: [{ status: "shipping", label: "Отгружено, в пути" }],
  shipping: [{ status: "delivered", label: "Доставлено на объект" }],
  delivered: [{ status: "installed", label: "Собрано и расставлено" }],
};

/** Рабочее место менеджера «Люкс»: заказы мебели, объекты, каталог. */
export default function LuxPage() {
  const { me, loading } = useRequireAuth();
  const [info, setInfo] = useState<LuxInfo | null>(null);
  const [orders, setOrders] = useState<FurnitureOrder[] | null>(null);
  const [objects, setObjects] = useState<LuxObject[]>([]);
  const [mine, setMine] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const q = new URLSearchParams();
      if (mine) q.set("mine", "true");
      if (done) q.set("done", "true");
      const [i, o, objs] = await Promise.all([
        api<LuxInfo>("/lux/info"),
        api<FurnitureOrder[]>(`/lux/orders?${q}`),
        api<LuxObject[]>("/lux/objects"),
      ]);
      setInfo(i);
      setOrders(o);
      setObjects(objs);
    } catch (err) {
      setError(errorText(err));
    }
  }, [mine, done]);
  useEffect(() => {
    if (me) void load();
  }, [me, load]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (!info || !orders) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  return (
    <Shell wide>
      <h1>Люкс ремонт</h1>
      <ErrorNote text={error} />
      <div className="split">
        <div>
          <section className="card">
            <h2>Заказы мебели</h2>
            <div className="chips" role="group" aria-label="Фильтр">
              <button type="button" className="chip" aria-pressed={mine} onClick={() => setMine(!mine)}>
                Только мои
              </button>
              <button type="button" className="chip" aria-pressed={done} onClick={() => setDone(!done)}>
                Завершённые
              </button>
            </div>
            {orders.length === 0 && <p className="muted">Заказов нет.</p>}
          </section>
          {orders.map((o) => (
            <ManagerOrder key={o.id} order={o} info={info} onChanged={load} />
          ))}
        </div>
        <div>
          <section className="card">
            <h2>Объекты «Люкс»</h2>
            {objects.length === 0 ? (
              <p className="muted">Пока нет.</p>
            ) : (
              <ul className="list">
                {objects.map((o) => (
                  <li key={o.id}>
                    <Link href={`/objects/${o.id}`}>{o.address}</Link>
                    <br />
                    <span className="muted small">
                      {OBJECT_STATUS_LABELS[o.status] ?? o.status} ·{" "}
                      {o.manager ? personLabel(o.manager) : <span className="badge warn">нет менеджера</span>}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <AddItem info={info} />
        </div>
      </div>
    </Shell>
  );
}

function ManagerOrder({ order, info, onChanged }: { order: FurnitureOrder; info: LuxInfo; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function act(fn: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card">
      <h2>
        №{order.number} <span className={`badge${order.status === "requested" ? " warn" : ""}`}>{info.statuses[order.status]}</span>
      </h2>
      <p className="small">
        <Link href={`/objects/${order.object_id}/furniture`}>{order.address}</Link>
        {order.manager && <span className="muted"> · менеджер {personLabel(order.manager)}</span>}
      </p>
      <ErrorNote text={error} />
      <ul className="list">
        {order.lines.map((l) => (
          <li key={l.id}>
            {l.title} × {l.qty}
            {l.room && <span className="muted small"> · {l.room}</span>}
            {l.wish && <div className="small pre">{l.wish}</div>}
            {l.link && (
              <a className="small" href={l.link} target="_blank" rel="noreferrer nofollow">
                {l.link}
              </a>
            )}
          </li>
        ))}
      </ul>
      {order.total_kop !== null && <p>Предложение: <b>{rub(order.total_kop)}</b></p>}
      {(order.status === "requested" || order.status === "quoted") && (
        <QuoteForm order={order} busy={busy} onSubmit={(body) => act(() => api(`/furniture-orders/${order.id}/quote`, { method: "PUT", body }))} />
      )}
      {["paid", "ordered", "shipping", "delivered"].includes(order.status) && (
        <ShipForm order={order} busy={busy} onSubmit={(body) => act(() => api(`/furniture-orders/${order.id}/logistics`, { method: "POST", body }))} />
      )}
      <p className="row">
        {(NEXT[order.status] ?? []).map((n) => (
          <button
            key={n.status}
            type="button"
            disabled={busy}
            onClick={() => void act(() => api(`/furniture-orders/${order.id}/status`, { method: "POST", body: { status: n.status } }))}
          >
            {n.label}
          </button>
        ))}
        {["requested", "quoted", "confirmed"].includes(order.status) && (
          <button
            type="button"
            className="link"
            disabled={busy}
            onClick={() => void act(() => api(`/furniture-orders/${order.id}/status`, { method: "POST", body: { status: "cancelled" } }))}
          >
            Отменить
          </button>
        )}
      </p>
      <OrderTimeline order={order} />
    </section>
  );
}

function rubText(kop: number | null): string {
  return kop === null ? "" : String(Math.round(kop / 100));
}

function QuoteForm({
  order,
  busy,
  onSubmit,
}: {
  order: FurnitureOrder;
  busy: boolean;
  onSubmit: (body: unknown) => Promise<void>;
}) {
  const inDays = (n: number) => new Date(Date.now() + n * 86_400_000).toISOString().slice(0, 10);
  const [prices, setPrices] = useState<Record<string, string>>(
    Object.fromEntries(order.lines.map((l) => [l.id, rubText(l.price_kop ?? l.estimate_kop)])),
  );
  const [titles, setTitles] = useState<Record<string, string>>({});
  const [delivery, setDelivery] = useState(rubText(order.delivery_kop));
  const [customs, setCustoms] = useState(rubText(order.customs_kop));
  const [service, setService] = useState(rubText(order.service_kop));
  const [eta, setEta] = useState(order.eta ?? inDays(75));
  const [valid, setValid] = useState(order.quote_valid_until ?? inDays(7));
  const [note, setNote] = useState(order.manager_note ?? "");
  const [est, setEst] = useState<LogisticsEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function prefill() {
    const e = await api<LogisticsEstimate>(`/furniture-orders/${order.id}/estimate`);
    setEst(e);
    setDelivery(rubText(e.delivery_kop));
    setCustoms(rubText(e.customs_kop));
    setService(rubText(e.insurance_kop + e.assembly_kop));
    setEta(inDays(e.days + 3));
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    const lines = order.lines.map((l) => ({ id: l.id, price_kop: parseRub(prices[l.id] ?? ""), title: titles[l.id]?.trim() || null }));
    const d = parseRub(delivery || "0");
    const c = parseRub(customs || "0");
    const s = parseRub(service || "0");
    if (lines.some((l) => !l.price_kop) || d === null || c === null || s === null) {
      setError("Укажите цены числами");
      return;
    }
    setError(null);
    void onSubmit({ lines, delivery_kop: d, customs_kop: c, service_kop: s, eta, valid_until: valid, note: note.trim() || null });
  }

  return (
    <form className="boxed" onSubmit={submit}>
      <h3>Предложение заказчику</h3>
      <ErrorNote text={error} />
      {order.lines.map((l) => (
        <div key={l.id} className="row">
          <label className="grow">
            {l.item_id ? l.title : "Что нашли (название)"}
            {!l.item_id && <input value={titles[l.id] ?? ""} maxLength={300} onChange={(e) => setTitles({ ...titles, [l.id]: e.target.value })} />}
          </label>
          <label>
            Цена за шт., ₽
            <input inputMode="decimal" value={prices[l.id] ?? ""} onChange={(e) => setPrices({ ...prices, [l.id]: e.target.value })} />
          </label>
        </div>
      ))}
      <div className="row">
        <label>
          Доставка, ₽
          <input inputMode="decimal" value={delivery} onChange={(e) => setDelivery(e.target.value)} />
        </label>
        <label>
          Таможня и налоги, ₽
          <input inputMode="decimal" value={customs} onChange={(e) => setCustoms(e.target.value)} />
        </label>
        <label>
          Сборка, страховка, сопровождение, ₽
          <input inputMode="decimal" value={service} onChange={(e) => setService(e.target.value)} />
        </label>
      </div>
      <div className="row">
        <label>
          Доставка к
          <input type="date" value={eta} onChange={(e) => setEta(e.target.value)} />
        </label>
        <label>
          Цена действует до
          <input type="date" value={valid} onChange={(e) => setValid(e.target.value)} />
        </label>
      </div>
      <label htmlFor={`note-${order.id}`}>Комментарий заказчику</label>
      <textarea id={`note-${order.id}`} rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} />
      {est && (
        <p className="muted small">
          Расчёт по {est.source === "partner" ? `данным перевозчика${est.carrier ? ` (${est.carrier})` : ""}` : "тарифам платформы"}: в пути с
          производством ≈ {est.days} дн.
        </p>
      )}
      <p className="row">
        <button type="button" className="secondary" disabled={busy} onClick={() => void prefill()}>
          Посчитать доставку и таможню
        </button>
        <button type="submit" disabled={busy}>
          Отправить предложение
        </button>
      </p>
    </form>
  );
}

function ShipForm({ order, busy, onSubmit }: { order: FurnitureOrder; busy: boolean; onSubmit: (body: unknown) => Promise<void> }) {
  const [carrier, setCarrier] = useState(order.carrier ?? "");
  const [tracking, setTracking] = useState(order.tracking ?? "");
  const [text, setText] = useState("");
  const [eta, setEta] = useState(order.eta ?? "");
  function submit(e: FormEvent) {
    e.preventDefault();
    void onSubmit({ carrier, tracking, text: text.trim() || null, eta: eta || null }).then(() => setText(""));
  }
  return (
    <form className="boxed" onSubmit={submit}>
      <h3>Доставка</h3>
      <div className="row">
        <label>
          Перевозчик
          <input value={carrier} maxLength={100} onChange={(e) => setCarrier(e.target.value)} />
        </label>
        <label>
          Номер отслеживания
          <input value={tracking} maxLength={100} onChange={(e) => setTracking(e.target.value)} />
        </label>
        <label>
          Срок
          <input type="date" value={eta} onChange={(e) => setEta(e.target.value)} />
        </label>
      </div>
      <label htmlFor={`ship-${order.id}`}>Отметка для заказчика</label>
      <input id={`ship-${order.id}`} placeholder="Например: прошёл таможню в Бресте" value={text} maxLength={1000} onChange={(e) => setText(e.target.value)} />
      <button type="submit" disabled={busy}>
        Сохранить
      </button>
    </form>
  );
}

function AddItem({ info }: { info: LuxInfo }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ title: "", brand: "", designer: "", category: "sofa", country: "IT", price: "", currency: "EUR", description: "", dimensions: "", image_url: "", lead_time_days: "60" });
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const price = parseRub(f.price);
    if (!price) {
      setError("Укажите цену у фабрики числом");
      return;
    }
    try {
      await api("/furniture", {
        method: "POST",
        body: {
          title: f.title,
          brand: f.brand || null,
          designer: f.designer || null,
          category: f.category,
          country: f.country,
          price: f.currency === "JPY" ? Math.round(price / 100) : price,
          currency: f.currency,
          description: f.description || null,
          dimensions: f.dimensions || null,
          image_url: f.image_url || null,
          lead_time_days: Number(f.lead_time_days) || 60,
        },
      });
      setSaved(f.title);
      setF({ ...f, title: "", price: "", description: "", dimensions: "", image_url: "" });
    } catch (err) {
      setError(errorText(err));
    }
  }
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <section className="card">
      <h2>Каталог мебели</h2>
      {!open ? (
        <button type="button" className="secondary" onClick={() => setOpen(true)}>
          Добавить позицию
        </button>
      ) : (
        <form onSubmit={submit}>
          <ErrorNote text={error} />
          {saved && <p className="small">Добавлено: {saved}</p>}
          <label htmlFor="ai-title">Название</label>
          <input id="ai-title" required minLength={2} value={f.title} onChange={set("title")} />
          <div className="row">
            <label>
              Бренд
              <input value={f.brand} onChange={set("brand")} />
            </label>
            <label>
              Дизайнер
              <input value={f.designer} onChange={set("designer")} />
            </label>
          </div>
          <div className="row">
            <label>
              Категория
              <select value={f.category} onChange={set("category")}>
                {Object.entries(info.categories).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Страна
              <select value={f.country} onChange={set("country")}>
                {Object.entries(info.countries).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="row">
            <label>
              Цена у фабрики
              <input inputMode="decimal" required value={f.price} onChange={set("price")} />
            </label>
            <label>
              Валюта
              <select value={f.currency} onChange={set("currency")}>
                {["EUR", "USD", "GBP", "DKK", "SEK", "CNY", "JPY", "TRY", "AED", "RUB"].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            <label>
              Изготовление, дн.
              <input type="number" min={1} max={730} value={f.lead_time_days} onChange={set("lead_time_days")} />
            </label>
          </div>
          <label htmlFor="ai-dim">Размеры</label>
          <input id="ai-dim" value={f.dimensions} onChange={set("dimensions")} />
          <label htmlFor="ai-desc">Описание</label>
          <textarea id="ai-desc" rows={2} value={f.description} onChange={set("description")} />
          <label htmlFor="ai-img">Ссылка на фото (https)</label>
          <input id="ai-img" type="url" value={f.image_url} onChange={set("image_url")} />
          <button type="submit">Добавить в каталог</button>
        </form>
      )}
    </section>
  );
}
