import type { OrgRole, OrgType } from "./types";

export const ROLE_LABELS: Record<OrgRole, string> = {
  contractor_admin: "Руководитель подрядчика",
  estimator: "Сметчик",
  foreman: "Прораб",
  supplier_manager: "Менеджер поставщика",
  platform_admin: "Администратор платформы",
  procurement_manager: "Менеджер снабжения",
  inspector: "Инженер технадзора",
};

export const ROLES_BY_ORG_TYPE: Record<OrgType, OrgRole[]> = {
  contractor: ["contractor_admin", "estimator", "foreman"],
  supplier: ["supplier_manager"],
  platform: ["platform_admin", "procurement_manager", "inspector", "estimator"],
};

export const ORG_TYPE_LABELS: Record<OrgType, string> = {
  contractor: "Подрядчик",
  supplier: "Поставщик",
  platform: "Платформа",
};

export function formatPhone(phone: string): string {
  const m = phone.match(/^\+7(\d{3})(\d{3})(\d{2})(\d{2})$/);
  return m ? `+7 ${m[1]} ${m[2]}-${m[3]}-${m[4]}` : phone;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("ru-RU", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function describeDevice(ua: string | null): string {
  if (!ua) return "Неизвестное устройство";
  const browser = /YaBrowser/.test(ua)
    ? "Яндекс Браузер"
    : /Edg\//.test(ua)
      ? "Edge"
      : /Chrome\//.test(ua)
        ? "Chrome"
        : /Firefox\//.test(ua)
          ? "Firefox"
          : /Safari\//.test(ua)
            ? "Safari"
            : "Браузер";
  const os = /iPhone|iPad/.test(ua)
    ? "iOS"
    : /Android/.test(ua)
      ? "Android"
      : /Mac OS X/.test(ua)
        ? "macOS"
        : /Windows/.test(ua)
          ? "Windows"
          : /Linux/.test(ua)
            ? "Linux"
            : "";
  return os ? `${browser}, ${os}` : browser;
}

export function rub(kop: number): string {
  return (kop / 100).toLocaleString("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
}

/** «1 234,5» или «1234.50» → копейки; null, если не число. */
export function parseRub(text: string): number | null {
  const clean = text.replace(/[\s ₽]/g, "").replace(",", ".");
  if (!/^\d+(\.\d{0,2})?$/.test(clean)) return null;
  return Math.round(parseFloat(clean) * 100);
}

export const OBJECT_STATUS_LABELS: Record<string, string> = {
  draft: "Новый",
  survey: "Замер",
  estimating: "Составление сметы",
  contracting: "Подписание договора",
  active: "Ремонт идёт",
  dispute: "Спор",
  completed: "Завершён",
  cancelled: "Отменён",
};

export const OBJECT_STEPS = ["draft", "survey", "estimating", "contracting", "active", "completed"];

export const SERVICE_TYPE_LABELS: Record<string, string> = { acceptance: "Умная приёмка", survey: "Замер" };

export const SERVICE_STATUS_LABELS: Record<string, string> = {
  created: "Ждёт назначения",
  scheduled: "Назначен, ждёт оплаты",
  paid: "Оплачен",
  completed: "Выполнен",
  cancelled: "Отменён",
};

export const ESTIMATE_TYPE_LABELS: Record<string, string> = { preliminary: "Предварительная", full: "Полная" };

export const ESTIMATE_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  submitted: "Ждёт утверждения",
  approved: "Утверждена",
};

export const ASSIGNMENT_LABELS: Record<string, string> = {
  foreman: "Прораб",
  inspector: "Инженер технадзора",
  procurement_manager: "Менеджер снабжения",
};

export function personName(p: { full_name: string | null; phone: string }): string {
  return p.full_name || formatPhone(p.phone);
}

export const FRAUD_FLAG_LABELS: Record<string, string> = {
  no_challenge: "Без кода съёмки",
  challenge_reused: "Код использован повторно",
  challenge_expired: "Код просрочен",
  delayed_upload: "Отправлено позже (не было связи)",
  geo_mismatch: "Снято далеко от объекта",
  no_geo: "Без геолокации",
  duplicate_exact: "Такое фото уже было",
  duplicate_similar: "Похоже на прежнее фото",
};

export const STAGE_STATUS_LABELS: Record<string, string> = {
  planned: "Запланирован",
  funding: "Ждёт резерва",
  ready: "Готов к работе",
  in_progress: "Идут работы",
  inspection: "На проверке технадзора",
  acceptance: "На приёмке у заказчика",
  accepted: "Принят",
  paid: "Оплачен",
  frozen: "Заморожен (спор)",
};

export const STAGE_TYPE_LABELS: Record<string, string> = {
  demolition: "Демонтаж",
  walls: "Перегородки и штукатурка",
  electric: "Электрика",
  plumbing: "Сантехника",
  screed: "Стяжка пола",
  waterproofing: "Гидроизоляция",
  tiling: "Плитка",
  finishing: "Чистовая отделка",
  ceiling: "Потолки",
  doors: "Двери",
};

export const INSPECTION_RESULT_LABELS: Record<string, string> = {
  passed: "Принято",
  remarks: "Есть замечания",
  failed: "Не принято",
};

export const REVIEW_LABELS: Record<string, string> = {
  pending: "Не проверено",
  approved: "Фото принято",
  rejected: "Фото отклонено",
};

export const PAYMENT_STATUS_LABELS: Record<string, string> = {
  created: "Создан",
  pending_funding: "Ждёт оплаты",
  held: "Деньги зарезервированы",
  release_requested: "Выплата в банке",
  released: "Выплачено подрядчику",
  frozen: "Заморожено",
  refunded: "Возвращено",
  failed: "Не прошло",
};

export const ACT_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик",
  sent: "Ждёт подписи подрядчика",
  signed_contractor: "Ждёт подписи заказчика",
  signed_customer: "Подписан, идёт выплата",
  released: "Подписан и оплачен",
  rejected: "Отклонён",
};

export const ORDER_STATUS_LABELS: Record<string, string> = {
  draft: "Черновик корзины",
  proposed: "Ждёт подтверждения заказчика",
  confirmed: "Подтверждён, ждёт оплаты",
  ordered: "Заказан у поставщика",
  delivering: "В доставке",
  delivered: "Доставлен",
  partially_delivered: "Доставлен с расхождениями",
  expired: "Не подтверждён вовремя",
  rejected: "Заказчик отказался",
};

export const DELIVERY_STATUS_LABELS: Record<string, string> = {
  pending: "Ожидается",
  accepted: "Принята",
  accepted_with_discrepancies: "Принята с расхождениями",
  rejected: "Не принята",
};

export const DISPUTE_STATUS_LABELS: Record<string, string> = {
  open: "Открыт",
  in_review: "На разборе",
  resolved: "Решён",
  rejected: "Отклонён",
};

export const DISPUTE_OUTCOME_LABELS: Record<string, string> = {
  continue: "Работы продолжаются",
  refund: "Деньги возвращены заказчику",
};

export const REQUEST_STATUS_LABELS: Record<string, string> = {
  new: "Новая",
  in_work: "В работе",
  done: "Выполнена",
};

/** Количество без лишних нулей: «2.500» → «2,5». */
export function qty(value: string | number): string {
  return Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 3 });
}
