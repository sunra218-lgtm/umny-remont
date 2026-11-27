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
