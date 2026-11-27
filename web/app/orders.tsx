"use client";

import { useState, type FormEvent } from "react";

import { api, errorText } from "@/lib/api";
import { DELIVERY_STATUS_LABELS, formatDate, ORDER_STATUS_LABELS, parseRub, qty, rub } from "@/lib/format";
import type { Org, PurchaseOrder, Stage } from "@/lib/types";

import { ErrorNote } from "./components";

const DONE = new Set(["delivered", "expired", "rejected"]);

function statusClass(o: PurchaseOrder): string {
  if (o.status === "delivered") return "status status-approved";
  if (o.status === "expired" || o.status === "rejected") return "status status-cancelled";
  if (o.status === "partially_delivered") return "status status-frozen";
  return "status status-submitted";
}

/** Карточка заказа материалов. Кнопки зависят от того, кто смотрит: сервер присылает can_*. */
export function OrderCard({
  order,
  onChanged,
  onEdit,
  showAddress = false,
}: {
  order: PurchaseOrder;
  onChanged: (o: PurchaseOrder) => void;
  onEdit?: (o: PurchaseOrder) => void;
  showAddress?: boolean;
}) {
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [slot, setSlot] = useState("");

  async function run(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      const o = await api<PurchaseOrder>(`/purchase-orders/${order.id}/${path}`, { method: "POST", body: body ?? {} });
      if (path === "confirm" && o.payment?.pay_url) {
        location.assign(o.payment.pay_url);
        return;
      }
      setRejecting(false);
      onChanged(o);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const staff = order.supplier_cost_kop !== null;
  return (
    <article className="order">
      <header className="order-head">
        <div>
          <b>{rub(order.total_kop)}</b>
          {showAddress && order.address && <div className="muted small">{order.address}</div>}
          <div className="muted small">
            {order.stage_title ? `Этап «${order.stage_title}» · ` : ""}
            {order.supplier_name ?? "Закупка менеджером платформы"}
          </div>
        </div>
        <span className={statusClass(order)}>{ORDER_STATUS_LABELS[order.status] ?? order.status}</span>
      </header>
      <ul className="order-items">
        {order.items.map((i) => (
          <li key={i.id}>
            <span>{i.title}</span>
            <span className="muted nowrap">
              {qty(i.qty)} × {rub(i.unit_price_kop)} = <b>{rub(i.total_kop)}</b>
            </span>
          </li>
        ))}
      </ul>
      {staff && (
        <p className="muted small">
          Цена поставщика {rub(order.supplier_cost_kop ?? 0)}, маржа платформы <b>{rub(order.margin_kop ?? 0)}</b>
        </p>
      )}
      {order.comment && <p className="small">{order.comment}</p>}
      {order.status === "proposed" && order.expires_at && (
        <p className="small">Подтвердить до {formatDate(order.expires_at)}. Потом цены могут измениться.</p>
      )}
      {order.reject_reason && <p className="small">Причина отказа: {order.reject_reason}</p>}
      {order.delivery_slot_at && !DONE.has(order.status) && (
        <p className="small">Доставка: {formatDate(order.delivery_slot_at)}</p>
      )}
      {order.payment && (order.payment.status === "held" || order.payment.status === "released") && (
        <p className="muted small">
          {order.payment.status === "held"
            ? "Деньги в резерве банка. Поставщик получит их после приёмки доставки."
            : "Доставка принята, деньги отправлены поставщику."}
        </p>
      )}
      {order.deliveries.length > 0 && (
        <ul className="list small">
          {order.deliveries.map((d) => (
            <li key={d.id}>
              <div>
                <b>{DELIVERY_STATUS_LABELS[d.status]}</b>
                {d.delivered_at && <span className="muted"> · {formatDate(d.delivered_at)}</span>}
                {d.received_by_name && <span className="muted"> · {d.received_by_name}</span>}
                {d.discrepancies.map((x, n) => (
                  <div key={n}>
                    {x.title}
                    {x.expected && x.received ? `: ждали ${qty(x.expected)}, приехало ${qty(x.received)}` : ""}
                    {x.comment ? ` (${x.comment})` : ""}
                  </div>
                ))}
                {d.comment && <div>{d.comment}</div>}
              </div>
              {d.invoice_url && (
                <a href={d.invoice_url} target="_blank" rel="noreferrer">
                  Накладная
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="row">
        {order.can_edit && onEdit && (
          <button className="secondary" disabled={busy} onClick={() => onEdit(order)}>
            Изменить
          </button>
        )}
        {order.can_edit && (
          <button className="primary" disabled={busy} onClick={() => void run("propose")}>
            Отправить заказчику
          </button>
        )}
        {order.can_confirm && (
          <button className="primary" disabled={busy} onClick={() => void run("confirm")}>
            Подтвердить и оплатить {rub(order.total_kop)}
          </button>
        )}
        {order.can_confirm && !rejecting && (
          <button className="secondary" disabled={busy} onClick={() => setRejecting(true)}>
            Отказаться
          </button>
        )}
        {order.payment?.status === "pending_funding" && order.payment.pay_url && (
          <a className="button primary" href={order.payment.pay_url}>
            Перейти к оплате
          </a>
        )}
        {order.can_order && (
          <button className="primary" disabled={busy} onClick={() => void run("order")}>
            Заказано у поставщика
          </button>
        )}
        {order.can_complete && (
          <button className="secondary" disabled={busy} onClick={() => void run("complete")}>
            Расхождения решены, закрыть заказ
          </button>
        )}
      </div>
      {order.can_ship && (
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            void run("ship", { delivery_slot_at: slot ? new Date(slot).toISOString() : null });
          }}
        >
          <label className="inline">
            Когда привезут
            <input type="datetime-local" value={slot} onChange={(e) => setSlot(e.target.value)} />
          </label>
          <button className="primary" disabled={busy}>
            Отгружено
          </button>
        </form>
      )}
      {rejecting && (
        <form
          className="stack"
          onSubmit={(e) => {
            e.preventDefault();
            void run("reject", { reason });
          }}
        >
          <label>
            Почему отказываетесь
            <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={3} required />
          </label>
          <div className="row">
            <button className="primary" disabled={busy}>
              Отказаться от корзины
            </button>
            <button type="button" className="secondary" onClick={() => setRejecting(false)}>
              Не отказываться
            </button>
          </div>
        </form>
      )}
      <ErrorNote text={error} />
    </article>
  );
}

type Row = { title: string; qty: string; price: string };
const EMPTY_ROW: Row = { title: "", qty: "1", price: "" };

/** Корзина материалов: собирает менеджер закупок, цены — для заказчика и поставщика. */
export function BasketForm({
  objectId,
  stages,
  suppliers,
  requestId,
  order,
  onSaved,
  onCancel,
}: {
  objectId: string;
  stages: Stage[];
  suppliers: Org[];
  requestId?: string | null;
  order?: PurchaseOrder | null;
  onSaved: (o: PurchaseOrder) => void;
  onCancel: () => void;
}) {
  const [rows, setRows] = useState<Row[]>(
    order ? order.items.map((i) => ({ title: i.title, qty: String(Number(i.qty)), price: String(i.unit_price_kop / 100) })) : [{ ...EMPTY_ROW }],
  );
  const [stageId, setStageId] = useState(order?.stage_id ?? "");
  const [supplierId, setSupplierId] = useState(order?.supplier_org_id ?? "");
  const [cost, setCost] = useState(order?.supplier_cost_kop != null ? String(order.supplier_cost_kop / 100) : "");
  const [comment, setComment] = useState(order?.comment ?? "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const totals = rows.map((r) => {
    const price = parseRub(r.price);
    const q = Number(r.qty.replace(",", "."));
    return price !== null && q > 0 ? Math.round(price * q) : 0;
  });
  const total = totals.reduce((a, b) => a + b, 0);
  const costKop = parseRub(cost);

  function setRow(i: number, patch: Partial<Row>) {
    setRows((rs) => rs.map((r, n) => (n === i ? { ...r, ...patch } : r)));
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    setError(null);
    const items = [];
    for (const r of rows) {
      if (!r.title.trim()) continue;
      const price = parseRub(r.price);
      const q = r.qty.replace(",", ".");
      if (price === null || !(Number(q) > 0)) {
        setError(`Проверьте количество и цену: «${r.title}».`);
        return;
      }
      items.push({ title: r.title.trim(), qty: q, unit_price_kop: price });
    }
    if (items.length === 0) {
      setError("Добавьте хотя бы одну позицию.");
      return;
    }
    if (costKop === null) {
      setError("Укажите цену поставщика за всю корзину.");
      return;
    }
    setBusy(true);
    const body = {
      stage_id: stageId || null,
      supplier_org_id: supplierId || null,
      request_id: requestId ?? order?.request_id ?? null,
      supplier_cost_kop: costKop,
      comment: comment.trim() || null,
      items,
    };
    try {
      const saved = order
        ? await api<PurchaseOrder>(`/purchase-orders/${order.id}`, { method: "PUT", body })
        : await api<PurchaseOrder>(`/objects/${objectId}/purchase-orders`, { method: "POST", body });
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack basket" onSubmit={(e) => void save(e)}>
      <div className="grid2">
        <label>
          Этап
          <select value={stageId} onChange={(e) => setStageId(e.target.value)}>
            <option value="">Без этапа</option>
            {stages.map((s) => (
              <option key={s.id} value={s.id}>
                {s.seq}. {s.title}
              </option>
            ))}
          </select>
        </label>
        <label>
          Поставщик
          <select value={supplierId} onChange={(e) => setSupplierId(e.target.value)}>
            <option value="">Покупаем сами (магазин)</option>
            {suppliers.map((s) => (
              <option key={s.id} value={s.id}>
                {s.legal_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <fieldset className="basket-rows">
        <legend>Позиции (цена для заказчика за единицу)</legend>
        {rows.map((r, i) => (
          <div className="basket-row" key={i}>
            <label>
              <span className="sr-only">Название</span>
              <input placeholder="Цемент М500, 50 кг" value={r.title} onChange={(e) => setRow(i, { title: e.target.value })} />
            </label>
            <label>
              <span className="sr-only">Количество</span>
              <input inputMode="decimal" value={r.qty} onChange={(e) => setRow(i, { qty: e.target.value })} aria-label="Количество" />
            </label>
            <label>
              <span className="sr-only">Цена, ₽</span>
              <input inputMode="decimal" placeholder="₽" value={r.price} onChange={(e) => setRow(i, { price: e.target.value })} aria-label="Цена, ₽" />
            </label>
            <span className="nowrap small">{rub(totals[i])}</span>
            <button
              type="button"
              className="link"
              aria-label={`Убрать позицию ${i + 1}`}
              disabled={rows.length === 1}
              onClick={() => setRows((rs) => rs.filter((_, n) => n !== i))}
            >
              Убрать
            </button>
          </div>
        ))}
        <button type="button" className="secondary" onClick={() => setRows((rs) => [...rs, { ...EMPTY_ROW }])}>
          Добавить позицию
        </button>
      </fieldset>
      <div className="grid2">
        <label>
          Цена поставщика за всю корзину, ₽
          <input inputMode="decimal" value={cost} onChange={(e) => setCost(e.target.value)} required />
        </label>
        <div>
          <div className="muted small">Для заказчика</div>
          <b>{rub(total)}</b>
          {costKop !== null && (
            <div className={total - costKop < 0 ? "error-text small" : "muted small"}>Маржа {rub(total - costKop)}</div>
          )}
        </div>
      </div>
      <label>
        Комментарий для заказчика
        <input value={comment} onChange={(e) => setComment(e.target.value)} maxLength={4000} />
      </label>
      <div className="row">
        <button className="primary" disabled={busy}>
          {order ? "Сохранить корзину" : "Создать корзину"}
        </button>
        <button type="button" className="secondary" onClick={onCancel}>
          Отмена
        </button>
      </div>
      <ErrorNote text={error} />
    </form>
  );
}
