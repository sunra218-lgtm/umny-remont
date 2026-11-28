"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { api, errorText } from "@/lib/api";
import { rub } from "@/lib/format";
import type { RepairObject } from "@/lib/types";

import { ErrorNote } from "./components";

export type DesignStyle = { key: string; title: string; about: string; palette: string[] };

export type DesignInfo = {
  styles: DesignStyle[];
  room_types: Record<string, string>;
  slots: Record<string, string>;
  room_slots: Record<string, Record<string, string[]>>;
  plan_reader: boolean;
  renderer: boolean;
  video: boolean;
};

export type DesignMaterial = {
  id: string;
  sku: string;
  kind: string;
  title: string;
  brand: string | null;
  styles: string[];
  color: string;
  pattern: string;
  unit: string;
  price_kop: number;
  rate: number;
  pack_size: number;
  pack_title: string | null;
  is_active: boolean;
};

export type DesignRoom = {
  key: string;
  name: string;
  type: string;
  area: number;
  height: number;
  perimeter: number | null;
  doors: number;
  windows: number;
};

export type DesignProject = {
  id: string;
  object_id: string;
  style: string;
  rooms: DesignRoom[];
  selections: Record<string, Record<string, string>>;
  pinned: Record<string, string[]>;
  materials: DesignMaterial[];
  plan_url: string | null;
  plan_text: string | null;
  version: number;
  updated_at: string;
  can_edit: boolean;
  renders: { room_key: string; status: string; url: string | null; error: string | null }[];
  video_status: string | null;
  video_url: string | null;
  soul?: boolean;
  finalized_at?: string | null;
};

export type DesignLine = {
  group: string;
  kind: "material" | "work";
  room: string | null;
  title: string;
  qty: number;
  unit: string;
  packs: number | null;
  pack_title: string | null;
  unit_price_kop: number;
  total_kop: number;
};

export type DesignEstimate = {
  lines: DesignLine[];
  materials_kop: number;
  works_kop: number;
  total_kop: number;
  low_kop: number;
  high_kop: number;
  floor_area: number;
  wall_area: number;
};

/** Округление до тысяч для вилки: «≈ 1,2 млн ₽» читается легче, чем копейки. */
export function roughRub(kop: number): string {
  const r = kop / 100;
  if (r >= 1_000_000) return `${(r / 1_000_000).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} млн ₽`;
  return `${Math.round(r / 1000).toLocaleString("ru-RU")} тыс. ₽`;
}

function shade(hex: string, k: number): string {
  const n = parseInt(hex.slice(1), 16);
  const c = (v: number) => Math.max(0, Math.min(255, Math.round(v * k)));
  return `rgb(${c((n >> 16) & 255)}, ${c((n >> 8) & 255)}, ${c(n & 255)})`;
}

/** Схема комнаты в цветах и рисунках выбранной отделки. Не фото — понять сочетание. */
export function RoomPreview({
  room,
  pick,
}: {
  room: DesignRoom;
  pick: (slot: string) => DesignMaterial | undefined;
}) {
  const floor = pick("floor");
  const walls = pick("walls");
  const ceiling = pick("ceiling");
  const door = pick("door");
  const plinth = pick("plinth");
  const light = pick("light");
  const sanitary = pick("sanitary");
  const id = `p-${room.key}`;
  const wallColor = walls?.color ?? "#EEEEEE";
  const floorColor = floor?.color ?? "#CCCCCC";
  // Задняя стена 60..340 × 40..170, пол — трапеция до низа картинки.
  return (
    <svg viewBox="0 0 400 230" className="room-preview" role="img" aria-label={`Схема: ${room.name}`}>
      <defs>
        <pattern id={`${id}-plank`} width="60" height="12" patternUnits="userSpaceOnUse">
          <rect width="60" height="12" fill={floorColor} />
          <path d="M0 11.5H60M40 0V12" stroke={shade(floorColor, 0.82)} strokeWidth="1" />
        </pattern>
        <pattern id={`${id}-herringbone`} width="20" height="20" patternUnits="userSpaceOnUse">
          <rect width="20" height="20" fill={floorColor} />
          <path d="M0 10L10 0M10 20L20 10M0 10L10 20M10 0L20 10" stroke={shade(floorColor, 0.8)} strokeWidth="1" />
        </pattern>
        {[30, 60, 120].map((size) => (
          <pattern key={size} id={`${id}-tile${size}`} width={size / 3} height={size / 3} patternUnits="userSpaceOnUse">
            <rect width={size / 3} height={size / 3} fill={floorColor} />
            <path d={`M0 0H${size / 3}M0 0V${size / 3}`} stroke={shade(floorColor, 0.85)} strokeWidth="1" />
          </pattern>
        ))}
        <pattern id={`${id}-wall-tile30`} width="12" height="12" patternUnits="userSpaceOnUse">
          <rect width="12" height="12" fill={wallColor} />
          <path d="M0 0H12M0 0V12" stroke={shade(wallColor, 0.85)} strokeWidth="0.8" />
        </pattern>
        <pattern id={`${id}-wall-brick`} width="24" height="10" patternUnits="userSpaceOnUse">
          <rect width="24" height="10" fill={wallColor} />
          <path d="M0 0H24M0 5H24M12 0V5M0 5V10M24 5V10" stroke={shade(wallColor, 0.8)} strokeWidth="0.8" />
        </pattern>
      </defs>
      {/* потолок */}
      <polygon points="0,0 400,0 340,40 60,40" fill={ceiling?.color ?? "#FAFAFA"} />
      {/* боковые стены темнее задней */}
      <polygon points="0,0 60,40 60,170 0,230" fill={shade(wallColor, 0.9)} />
      <polygon points="400,0 340,40 340,170 400,230" fill={shade(wallColor, 0.86)} />
      <rect
        x="60"
        y="40"
        width="280"
        height="130"
        fill={walls && ["tile30", "brick"].includes(walls.pattern) ? `url(#${id}-wall-${walls.pattern})` : wallColor}
      />
      {/* пол */}
      <polygon
        points="60,170 340,170 400,230 0,230"
        fill={floor && floor.pattern !== "none" ? `url(#${id}-${floor.pattern})` : floorColor}
      />
      {plinth && <rect x="60" y="166" width="280" height="4" fill={plinth.color} />}
      {room.windows > 0 && (
        <g>
          <rect x="210" y="62" width="90" height="70" fill="#CFE3F2" stroke="#FFFFFF" strokeWidth="4" />
          <path d="M255 62V132" stroke="#FFFFFF" strokeWidth="3" />
        </g>
      )}
      {door && (
        <g>
          <rect x="90" y="88" width="44" height="82" fill={door.color} stroke={shade(door.color, 0.75)} strokeWidth="2" />
          {door.pattern === "door_panel" && (
            <>
              <rect x="97" y="96" width="30" height="30" fill="none" stroke={shade(door.color, 0.8)} />
              <rect x="97" y="132" width="30" height="30" fill="none" stroke={shade(door.color, 0.8)} />
            </>
          )}
          <circle cx="127" cy="130" r="2" fill="#888888" />
        </g>
      )}
      {sanitary && (
        <g>
          <rect x="160" y="140" width="60" height="30" rx="8" fill="#FFFFFF" stroke={sanitary.color} strokeWidth="2" />
          <rect x="235" y="138" width="22" height="32" rx="6" fill="#FFFFFF" stroke={sanitary.color} strokeWidth="2" />
        </g>
      )}
      {light && (
        <g>
          <path d="M200 0V18" stroke="#666666" strokeWidth="1.5" />
          <ellipse cx="200" cy="22" rx="16" ry="6" fill={light.color} />
          <ellipse cx="200" cy="30" rx="34" ry="6" fill="#FFF6C8" opacity="0.5" />
        </g>
      )}
    </svg>
  );
}

/** Раздел на странице объекта: короткая сводка и вход в дизайн-проект. */
export function DesignEntry({ obj }: { obj: RepairObject }) {
  const [project, setProject] = useState<DesignProject | null | undefined>(undefined);
  const [estimate, setEstimate] = useState<DesignEstimate | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const p = await api<DesignProject | null>(`/objects/${obj.id}/design`);
        setProject(p);
        if (p) setEstimate(await api<DesignEstimate>(`/objects/${obj.id}/design/estimate`));
      } catch (err) {
        setError(errorText(err));
      }
    })();
  }, [obj.id]);

  if (project === undefined && !error) return null;
  const canStart = obj.access.is_customer || obj.access.is_admin;
  if (!project && !canStart) return null;
  return (
    <section className="card">
      <h2>Дизайн-проект</h2>
      <ErrorNote text={error} />
      {project ? (
        <>
          <p>
            {project.rooms.length} помещ. · {project.rooms.reduce((s, r) => s + r.area, 0).toFixed(1)} м²
          </p>
          {estimate && (
            <p>
              Примерная стоимость ремонта: <b>{roughRub(estimate.low_kop)} – {roughRub(estimate.high_kop)}</b>
              <br />
              <span className="muted small">
                материалы {rub(estimate.materials_kop)}, работы {rub(estimate.works_kop)}
              </span>
            </p>
          )}
          <Link className="button" href={`/objects/${obj.id}/design`}>
            Открыть дизайн-проект
          </Link>
        </>
      ) : (
        <>
          <p>
            Выберите стиль и загрузите план БТИ — сервис подберёт отделку из нашего каталога, покажет, как будут
            выглядеть комнаты, и сразу посчитает примерную стоимость ремонта.
          </p>
          <Link className="button" href={`/objects/${obj.id}/design`}>
            Сделать дизайн-проект
          </Link>
        </>
      )}
    </section>
  );
}
