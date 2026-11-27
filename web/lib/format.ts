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
