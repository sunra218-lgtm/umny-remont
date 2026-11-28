"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { useRequireAuth } from "@/lib/auth";
import { rub } from "@/lib/format";
import type { RepairObject } from "@/lib/types";

import { ErrorNote, Loading, Shell } from "../../../components";
import { roughRub, type DesignInfo } from "../../../design";
import {
  dateOnly,
  OrderTimeline,
  personLabel,
  REGION_LABELS,
  supplierPrice,
  type FurnitureItem,
  type FurnitureOrder,
  type LogisticsEstimate,
  type LuxInfo,
} from "../../../lux";

/** Дизайнерская мебель для объекта «Люкс»: каталог, «найдите мне», подборка, заказы. */
export default function FurniturePage() {
  const { id } = useParams<{ id: string }>();
  const { me, loading } = useRequireAuth();
  const [obj, setObj] = useState<RepairObject | null>(null);
  const [info, setInfo] = useState<LuxInfo | null>(null);
  const [styles, setStyles] = useState<Record<string, string>>({});
  const [orders, setOrders] = useState<FurnitureOrder[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadOrders = useCallback(async () => {
    setOrders(await api<FurnitureOrder[]>(`/objects/${id}/furniture-orders`));
  }, [id]);

  useEffect(() => {
    if (!me) return;
    (async () => {
      try {
        const [o, i, d] = await Promise.all([
          api<RepairObject>(`/objects/${id}`),
          api<LuxInfo>("/lux/info"),
          api<DesignInfo>("/design/info"),
          loadOrders(),
        ]);
        setObj(o);
        setInfo(i);
        setStyles(Object.fromEntries(d.styles.map((s) => [s.key, s.title])));
      } catch (err) {
        setError(errorText(err));
      }
    })();
  }, [me, id, loadOrders]);

  if (loading || !me) return <Shell><Loading /></Shell>;
  if (!obj || !info || !orders) return <Shell>{error ? <ErrorNote text={error} /> : <Loading />}</Shell>;

  const draft = orders.find((o) => o.status === "draft") ?? null;
  const others = orders.filter((o) => o.status !== "draft");
  const canAdd = (obj.access.is_customer || obj.access.is_admin) && obj.package === "lux";

  async function add(body: Record<string, unknown>) {
    setError(null);
    try {
      await api(`/objects/${id}/furniture-orders/lines`, { method: "POST", body });
      await loadOrders();
      return true;
    } catch (err) {
      setError(errorText(err));
      return false;
    }
  }

  return (
    <Shell wide>
      <p>
        <Link href={`/objects/${id}`}>← К объекту</Link>
      </p>
      <h1>Мебель и предметы интерьера</h1>
      <p className="muted">{obj.address_text}</p>
      <ErrorNote text={error} />
      {obj.package !== "lux" && (
        <p className="card">
          Заказ мебели — часть пакета «Люкс». Включить его можно на <Link href={`/objects/${id}#lux`}>странице объекта</Link>.
        </p>
      )}
      <div className="split">
        <div>
          <Catalog info={info} styles={styles} canAdd={canAdd} onAdd={(itemId, room) => add({ item_id: itemId, room: room || null })} />
          {canAdd && <WishForm onAdd={add} />}
        </div>
        <div>
          <Draft order={draft} info={info} onChanged={loadOrders} />
          {others.map((o) => (
            <OrderCard key={o.id} order={o} info={info} onChanged={loadOrders} />
          ))}
        </div>
      </div>
    </Shell>
  );
}

function Catalog({
  info,
  styles,
  canAdd,
  onAdd,
}: {
  info: LuxInfo;
  styles: Record<string, string>;
  canAdd: boolean;
  onAdd: (itemId: string, room: string) => Promise<boolean>;
}) {
  const [q, setQ] = useState("");
  const [category, setCategory] = useState("");
  const [country, setCountry] = useState("");
  const [style, setStyle] = useState("");
  const [items, setItems] = useState<FurnitureItem[] | null>(null);
  const [partner, setPartner] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);

  const search = useCallback(
    async (fromPartners = false) => {
      setError(null);
      try {
        const p = new URLSearchParams();
        if (q.trim()) p.set("q", q.trim());
        if (category) p.set("category", category);
        if (fromPartners) {
          setItems(await api<FurnitureItem[]>(`/furniture/partners?${p}`));
        } else {
          if (country) p.set("country", country);
          if (style) p.set("style", style);
          setItems(await api<FurnitureItem[]>(`/furniture?${p}`));
        }
        setPartner(fromPartners);
      } catch (err) {
        setError(errorText(err));
      }
    },
    [q, category, country, style],
  );
  useEffect(() => {
    void search();
    // Фильтры-списки применяются сразу, текст — по кнопке.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [category, country, style]);

  function submit(e: FormEvent) {
    e.preventDefault();
    void search();
  }

  return (
    <section className="card">
      <h2>Каталог</h2>
      <form className="filters" onSubmit={submit}>
        <label className="sr-only" htmlFor="fq">
          Поиск
        </label>
        <input id="fq" placeholder="Например: кресло, травертин, орех" value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Категория" value={category} onChange={(e) => setCategory(e.target.value)}>
          <option value="">Все категории</option>
          {Object.entries(info.categories).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select aria-label="Страна" value={country} onChange={(e) => setCountry(e.target.value)}>
          <option value="">Все страны</option>
          {Object.entries(info.countries).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <select aria-label="Стиль" value={style} onChange={(e) => setStyle(e.target.value)}>
          <option value="">Любой стиль</option>
          {Object.entries(styles).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
        <button type="submit">Найти</button>
        {info.partners && q.trim().length > 1 && (
          <button type="button" className="secondary" onClick={() => void search(true)}>
            Искать у фабрик-партнёров
          </button>
        )}
      </form>
      <ErrorNote text={error} />
      {partner && <p className="muted small">Найдено у фабрик-партнёров. Точную цену и срок подтвердит менеджер.</p>}
      {items === null ? (
        <Loading />
      ) : items.length === 0 ? (
        <p className="muted">
          Ничего не нашли. {canAdd ? "Опишите, что ищете, в форме ниже — менеджер найдёт у фабрик." : ""}
        </p>
      ) : (
        <ul className="furniture-grid">
          {items.map((i) => (
            <li key={i.id} className="boxed furniture-card">
              {i.image_url ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={i.image_url} alt="" loading="lazy" />
              ) : (
                <div className={`furniture-ph cat-${i.category}`} aria-hidden="true">
                  {info.categories[i.category]}
                </div>
              )}
              <b>{i.title}</b>
              <span className="muted small">
                {[i.brand, i.designer].filter(Boolean).join(" · ")}
                {(i.brand || i.designer) && " · "}
                {info.countries[i.country] ?? i.country}
                {i.dimensions && ` · ${i.dimensions}`}
              </span>
              {i.description && <span className="small">{i.description}</span>}
              <span>
                <b>≈ {rub(i.price_rub_kop)}</b> <span className="muted small">({supplierPrice(i)} у фабрики)</span>
              </span>
              <span className="muted small">Изготовление ≈ {i.lead_time_days} дн.</span>
              {canAdd && (
                <button
                  type="button"
                  className="secondary"
                  onClick={() =>
                    void onAdd(i.id, "").then((ok) => {
                      if (ok) setAdded(i.id);
                    })
                  }
                >
                  {added === i.id ? "Добавлено ✓" : "В подборку"}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function WishForm({ onAdd }: { onAdd: (body: Record<string, unknown>) => Promise<boolean> }) {
  const [wish, setWish] = useState("");
  const [link, setLink] = useState("");
  const [room, setRoom] = useState("");
  const [qty, setQty] = useState(1);
  async function submit(e: FormEvent) {
    e.preventDefault();
    const ok = await onAdd({ wish: wish.trim() || null, link: link.trim() || null, room: room.trim() || null, qty });
    if (ok) {
      setWish("");
      setLink("");
      setRoom("");
      setQty(1);
    }
  }
  return (
    <section className="card">
      <h2>Найти по описанию или ссылке</h2>
      <p className="muted small">
        Увидели вещь в журнале, в салоне или в интернете? Опишите её или дайте ссылку — менеджер найдёт у фабрики или
        подберёт аналог и посчитает доставку.
      </p>
      <form onSubmit={submit}>
        <label htmlFor="w-wish">Что ищете</label>
        <textarea id="w-wish" rows={3} maxLength={2000} value={wish} onChange={(e) => setWish(e.target.value)} />
        <label htmlFor="w-link">Ссылка</label>
        <input id="w-link" type="url" inputMode="url" placeholder="https://…" value={link} onChange={(e) => setLink(e.target.value)} />
        <div className="row">
          <label className="grow">
            Комната
            <input value={room} maxLength={60} onChange={(e) => setRoom(e.target.value)} />
          </label>
          <label>
            Количество
            <input type="number" min={1} max={100} value={qty} onChange={(e) => setQty(Number(e.target.value) || 1)} />
          </label>
        </div>
        <button type="submit" disabled={!wish.trim() && !link.trim()}>
          Добавить в подборку
        </button>
      </form>
    </section>
  );
}

function EstimateBox({ order }: { order: FurnitureOrder }) {
  const [est, setEst] = useState<LogisticsEstimate | null>(null);
  const key = order.lines.map((l) => `${l.id}:${l.qty}`).join(",");
  useEffect(() => {
    void api<LogisticsEstimate>(`/furniture-orders/${order.id}/estimate`)
      .then(setEst)
      .catch(() => setEst(null));
  }, [order.id, key]);
  if (!est || !est.goods_kop) return null;
  return (
    <div className="boxed">
      <p>
        Ориентир с доставкой и таможней: <b>{roughRub(est.total_kop)}</b>, привезём примерно за {est.days} дн.
      </p>
      <ul className="muted small">
        <li>Мебель: {rub(est.goods_kop)}</li>
        {est.legs.map((l) => (
          <li key={l.region}>
            Доставка {REGION_LABELS[l.region] ?? l.region}: {rub(l.delivery_kop)}
            {l.customs_kop > 0 && `, таможня ${rub(l.customs_kop)}`}
          </li>
        ))}
        <li>Страховка груза: {rub(est.insurance_kop)}</li>
        <li>Сборка и расстановка VIP-мастерами: {rub(est.assembly_kop)}</li>
      </ul>
      {est.unpriced > 0 && <p className="muted small">Позиции «найти по описанию» ({est.unpriced}) оценит менеджер.</p>}
    </div>
  );
}

function Draft({ order, info, onChanged }: { order: FurnitureOrder | null; info: LuxInfo; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!order) {
    return (
      <section className="card">
        <h2>Подборка</h2>
        <p className="muted">Добавляйте понравившиеся позиции из каталога — менеджер соберёт предложение с доставкой.</p>
      </section>
    );
  }
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
      <h2>Подборка №{order.number}</h2>
      <ErrorNote text={error} />
      <ul className="list">
        {order.lines.map((l) => (
          <li key={l.id} className="row between">
            <span className="grow">
              {l.title}
              {l.wish && <span className="muted small"> — {l.wish}</span>}
              {l.link && (
                <>
                  {" "}
                  <a className="small" href={l.link} target="_blank" rel="noreferrer nofollow">
                    ссылка
                  </a>
                </>
              )}
              <br />
              <span className="muted small">
                {l.country && `${info.countries[l.country] ?? l.country} · `}
                {l.estimate_kop ? `≈ ${rub(l.estimate_kop)} за шт.` : "цену назовёт менеджер"}
              </span>
            </span>
            {order.can_edit && (
              <span className="row">
                <input
                  aria-label={`Количество: ${l.title}`}
                  className="qty-input"
                  type="number"
                  min={1}
                  max={100}
                  value={l.qty}
                  disabled={busy}
                  onChange={(e) =>
                    void act(() => api(`/furniture-order-lines/${l.id}`, { method: "PUT", body: { qty: Number(e.target.value) || 1 } }))
                  }
                />
                <button
                  type="button"
                  className="link"
                  disabled={busy}
                  onClick={() => void act(() => api(`/furniture-order-lines/${l.id}`, { method: "PUT", body: { qty: 0 } }))}
                >
                  Убрать
                </button>
              </span>
            )}
          </li>
        ))}
      </ul>
      <EstimateBox order={order} />
      {order.can_edit && order.lines.length > 0 && (
        <p>
          <button
            type="button"
            disabled={busy}
            onClick={() => void act(() => api(`/furniture-orders/${order.id}/status`, { method: "POST", body: { status: "requested" } }))}
          >
            Отправить менеджеру
          </button>
        </p>
      )}
    </section>
  );
}

function OrderCard({ order, info, onChanged }: { order: FurnitureOrder; info: LuxInfo; onChanged: () => Promise<void> }) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function move(status: string) {
    setBusy(true);
    setError(null);
    try {
      await api(`/furniture-orders/${order.id}/status`, { method: "POST", body: { status } });
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const canDecide = !order.can_manage && (order.status === "quoted" || order.status === "requested");
  return (
    <section className="card">
      <h2>
        Заказ мебели №{order.number} <span className={`badge${order.status === "quoted" ? " warn" : ""}`}>{info.statuses[order.status]}</span>
      </h2>
      <ErrorNote text={error} />
      {order.manager && <p className="small">Менеджер: {personLabel(order.manager)}</p>}
      <ul className="list">
        {order.lines.map((l) => (
          <li key={l.id} className="row between">
            <span>
              {l.title} × {l.qty}
              {l.wish && <span className="muted small"> — {l.wish}</span>}
            </span>
            {l.price_kop !== null && <b>{rub(l.price_kop * l.qty)}</b>}
          </li>
        ))}
      </ul>
      {order.total_kop !== null && (
        <div className="boxed">
          <p className="estimate-range">{rub(order.total_kop)}</p>
          <ul className="muted small">
            <li>Мебель: {rub(order.goods_kop ?? 0)}</li>
            <li>Доставка: {rub(order.delivery_kop ?? 0)}</li>
            <li>Таможня и налоги: {rub(order.customs_kop ?? 0)}</li>
            {!!order.service_kop && <li>Сборка, страховка и сопровождение: {rub(order.service_kop)}</li>}
          </ul>
          {order.eta && <p>Доставка на объект к {dateOnly(order.eta)}</p>}
          {order.status === "quoted" && order.quote_valid_until && (
            <p className="muted small">Цена действует до {dateOnly(order.quote_valid_until)}.</p>
          )}
          {order.manager_note && <p className="pre">{order.manager_note}</p>}
        </div>
      )}
      {order.tracking && (
        <p className="small">
          Перевозчик: {order.carrier ?? "—"}, номер {order.tracking}
        </p>
      )}
      <OrderTimeline order={order} />
      {canDecide && (
        <p className="row">
          {order.status === "quoted" && (
            <button type="button" disabled={busy} onClick={() => void move("confirmed")}>
              Подтвердить заказ
            </button>
          )}
          {order.status === "quoted" && (
            <button type="button" className="secondary" disabled={busy} onClick={() => void move("requested")}>
              Попросить изменить
            </button>
          )}
          <button type="button" className="link" disabled={busy} onClick={() => void move("cancelled")}>
            Отменить заказ
          </button>
        </p>
      )}
      {order.status === "confirmed" && !order.can_manage && (
        <p className="muted small">Менеджер пришлёт счёт на оплату. После оплаты заказ уходит на фабрику.</p>
      )}
    </section>
  );
}
