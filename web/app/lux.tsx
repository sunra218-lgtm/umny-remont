"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { formatDate, formatPhone } from "@/lib/format";
import type { RepairObject } from "@/lib/types";

import { ErrorNote } from "./components";

export type LuxInfo = {
  categories: Record<string, string>;
  countries: Record<string, string>;
  statuses: Record<string, string>;
  partners: boolean;
  logistics: boolean;
};

export type LuxPerson = { id: string; name: string | null; phone: string };

export type LuxState = {
  package: "standard" | "lux";
  manager: LuxPerson | null;
  contractor_vip: boolean | null;
  can_change: boolean;
  can_assign: boolean;
};

export type FurnitureItem = {
  id: string;
  source: string;
  title: string;
  brand: string | null;
  designer: string | null;
  category: string;
  country: string;
  styles: string[];
  description: string | null;
  dimensions: string | null;
  price: number;
  currency: string;
  price_rub_kop: number;
  volume_l: number;
  weight_kg: number;
  lead_time_days: number;
  image_url: string | null;
  is_active: boolean;
};

export type FurnitureLine = {
  id: string;
  item_id: string | null;
  title: string;
  wish: string | null;
  link: string | null;
  room: string | null;
  qty: number;
  estimate_kop: number | null;
  price_kop: number | null;
  country: string | null;
  category: string | null;
  image_url: string | null;
};

export type FurnitureOrder = {
  id: string;
  object_id: string;
  address: string | null;
  number: number;
  status: string;
  customer_note: string | null;
  manager: LuxPerson | null;
  manager_note: string | null;
  goods_kop: number | null;
  delivery_kop: number | null;
  customs_kop: number | null;
  service_kop: number | null;
  total_kop: number | null;
  eta: string | null;
  quote_valid_until: string | null;
  carrier: string | null;
  tracking: string | null;
  lines: FurnitureLine[];
  events: { kind: string; text: string; created_at: string }[];
  created_at: string;
  can_edit: boolean;
  can_manage: boolean;
};

export type LogisticsEstimate = {
  legs: { region: string; countries: string[]; volume_m3: number; goods_kop: number; delivery_kop: number; customs_kop: number; days: number }[];
  goods_kop: number;
  delivery_kop: number;
  customs_kop: number;
  insurance_kop: number;
  assembly_kop: number;
  total_kop: number;
  days: number;
  source: "tariffs" | "partner";
  carrier: string | null;
  unpriced: number;
};

export const REGION_LABELS: Record<string, string> = {
  ru: "по России",
  eaeu: "из ЕАЭС",
  europe: "из Европы",
  turkey: "из Турции",
  asia: "из Азии",
  middle_east: "с Ближнего Востока",
  america: "из Америки",
};

export function personLabel(p: LuxPerson): string {
  return p.name ? `${p.name}, ${formatPhone(p.phone)}` : formatPhone(p.phone);
}

export function dateOnly(iso: string): string {
  return new Date(`${iso}T12:00:00`).toLocaleDateString("ru-RU", { day: "numeric", month: "long", year: "numeric" });
}

/** Цена у поставщика: «12 800 €». */
export function supplierPrice(i: FurnitureItem): string {
  const units = i.currency === "JPY" ? i.price : i.price / 100;
  try {
    return units.toLocaleString("ru-RU", { style: "currency", currency: i.currency, maximumFractionDigits: 0 });
  } catch {
    return `${units} ${i.currency}`;
  }
}

/** Лента заказа мебели: статусы и отметки пути. */
export function OrderTimeline({ order }: { order: FurnitureOrder }) {
  if (!order.events.length) return null;
  return (
    <ol className="timeline">
      {order.events.map((e, i) => (
        <li key={i} className={e.kind === "logistics" ? "logistics" : undefined}>
          <span className="muted small">{formatDate(e.created_at)}</span>
          <br />
          {e.text}
        </li>
      ))}
    </ol>
  );
}

/** Раздел на странице объекта: пакет «Люкс», персональный менеджер, вход в мебель. */
export function LuxSection({ obj, onChanged }: { obj: RepairObject; onChanged: () => Promise<void> }) {
  const [state, setState] = useState<LuxState | null>(null);
  const [managers, setManagers] = useState<LuxPerson[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await api<LuxState>(`/objects/${obj.id}/lux`);
      setState(s);
      if (s.can_assign && s.package === "lux") setManagers(await api<LuxPerson[]>("/lux/managers"));
    } catch (err) {
      setError(errorText(err));
    }
  }, [obj.id]);
  useEffect(() => {
    void load();
  }, [load]);

  async function setPackage(pkg: "standard" | "lux") {
    setError(null);
    try {
      await api(`/objects/${obj.id}/package`, { method: "PUT", body: { package: pkg } });
      await load();
      await onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function setManager(userId: string) {
    setError(null);
    try {
      setState(await api<LuxState>(`/objects/${obj.id}/lux-manager`, { method: "PUT", body: { user_id: userId || null } }));
    } catch (err) {
      setError(errorText(err));
    }
  }

  if (!state) return error ? <ErrorNote text={error} /> : null;
  const lux = state.package === "lux";
  if (!lux && !state.can_change) return null;
  return (
    <section className={`card${lux ? " lux-card" : ""}`} id="lux">
      <h2>{lux ? "Люкс ремонт" : "Пакет «Люкс»"}</h2>
      <ErrorNote text={error} />
      {lux ? (
        <>
          <ul className="lux-perks">
            <li>VIP-мастера{state.contractor_vip ? " — подрядчик объекта в их числе" : ""}</li>
            <li>
              Персональный менеджер:{" "}
              {state.manager ? <b>{personLabel(state.manager)}</b> : <span className="muted">назначаем, свяжемся в течение дня</span>}
            </li>
            <li>Дизайнерская мебель со всего мира с доставкой, таможней и сборкой</li>
          </ul>
          {state.can_assign && (
            <label className="field-title">
              Персональный менеджер
              <select value={state.manager?.id ?? ""} onChange={(e) => void setManager(e.target.value)}>
                <option value="">Не назначен</option>
                {managers.map((m) => (
                  <option key={m.id} value={m.id}>
                    {personLabel(m)}
                  </option>
                ))}
              </select>
            </label>
          )}
          <p className="row">
            <Link className="button" href={`/objects/${obj.id}/furniture`}>
              Мебель и предметы интерьера
            </Link>
            {state.can_change && (
              <button type="button" className="link" onClick={() => void setPackage("standard")}>
                Вернуться к обычному пакету
              </button>
            )}
          </p>
        </>
      ) : (
        <>
          <p>Премиальный ремонт под ключ:</p>
          <ul className="lux-perks">
            <li>работают только VIP-мастера с лучшими оценками на платформе;</li>
            <li>персональный менеджер на связи на всём ремонте;</li>
            <li>подбор и заказ дизайнерской мебели со всего мира, доставка, таможня и сборка.</li>
          </ul>
          <button type="button" onClick={() => void setPackage("lux")}>
            Перейти на «Люкс»
          </button>
        </>
      )}
    </section>
  );
}
